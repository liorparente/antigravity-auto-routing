#!/usr/bin/env python3
"""Deterministic three-tier task-routing decision engine.

The council deliberately has no model or network dependency.  Tier 1 rejects
shell-shaped task descriptions with :mod:`shlex`; Tier 2 normalizes the
requested calibration; and Tier 3 asynchronously creates a signed decision
manifest.  A manifest is both written per task and retained in a 24-hour
planning cache, so repeated planning is deterministic and inexpensive.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shlex
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

CACHE_TTL_SECONDS = 24 * 60 * 60
CACHE_VERSION = 2
MAX_DEBATE_ROUNDS = 3
PROTOCOL_VERSION = "3.5"
VALID_COMPLEXITIES = frozenset({"trivial", "simple", "medium", "complex"})
VALID_EFFORTS = frozenset({"low", "medium", "high", "ultra"})
DEFAULT_EFFORTS = {
    "trivial": "low",
    "simple": "medium",
    "medium": "high",
    "complex": "ultra",
}
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
UNSAFE_LEXEMES = ("$(", "`", ";", "&&", "||", "|", ">", "<")
CALIBRATION_FIELDS = ("task_id", "task", "complexity", "effort", "decision", "nonce")
HMAC_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
SENSITIVE_PATTERNS = (
    "AGY_CALIBRATION_SECRET",
    "api_key",
    "sk-",
    "bearer ",
    "BEGIN PRIVATE KEY",
    "password",
    "secret",
    "[SENSITIVE]",
)


def detect_sensitive_data(text: str) -> bool:
    """Return True if text contains sensitive security patterns or flags."""
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in SENSITIVE_PATTERNS)


def evaluate_sensitivity(task_text: str) -> tuple[bool, bool]:
    """Evaluate task sensitivity. Returns (is_sensitive, requires_human_approval)."""
    is_sens = detect_sensitive_data(task_text)
    return is_sens, is_sens


def check_local_model_endpoint(
    endpoint_url: str = "http://127.0.0.1:1234/v1/models",
    timeout_seconds: float = 1.0,
) -> bool:
    """Non-blocking health probe for local inference server."""
    try:
        req = urllib.request.Request(
            endpoint_url,
            headers={"User-Agent": f"Antigravity/{PROTOCOL_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            return response.status == 200
    except Exception:
        return False


def should_route_to_local_model(
    complexity: str,
    is_sensitive: bool = False,
    is_local_available: bool = False,
) -> bool:
    """Determine whether task should be routed to local model (LM Studio).

    Sensitive tasks (PII, credentials) must stay on the local model whenever
    it is available — "Sensitive ... LM Studio ALWAYS (local model)" — so
    sensitivity short-circuits to routing local rather than away from it.
    """
    if not is_local_available:
        return False
    if is_sensitive:
        return True
    normalized = complexity.lower().strip()
    return normalized in ("trivial", "simple")


def append_jsonl_locked(file_path: str | Path, record: dict[str, Any]) -> None:
    """Safely append a JSON record using advisory file locking (fcntl)."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def log_routing_telemetry(
    task_id: str,
    complexity: str,
    chosen_worker: str,
    reason: str,
    log_file: str | Path | None = None,
) -> dict[str, Any]:
    """Record structured telemetry for a routing decision."""
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_id": task_id,
        "complexity": complexity,
        "chosen_worker": chosen_worker,
        "reason": reason,
    }
    if log_file:
        append_jsonl_locked(log_file, record)
    return record


class SensitiveTaskFallbackBlocked(RuntimeError):
    """Raised when a sensitive task's local model fails and Rule 3.5 forbids
    escalating to a cloud worker: the caller must fail closed instead."""


def record_local_model_failure(
    task_id: str,
    primary_worker: str,
    error_reason: str,
    errors_log: str | Path | None = None,
    is_sensitive: bool = False,
) -> str:
    """Record a local model failure/unavailability event and return next fallback worker.

    Sensitive tasks can never be handed to a cloud worker (Rule 3.5:
    "Sensitive tasks: Local models only ... -> fail closed immediately"), so
    this raises :class:`SensitiveTaskFallbackBlocked` instead of returning a
    cloud worker name.
    """
    fallback_worker = "codex_terra"
    fallback_action = (
        "FAIL-CLOSED: no cloud fallback permitted for a sensitive task"
        if is_sensitive
        else f"Escalated to {fallback_worker}"
    )
    if errors_log:
        err_path = Path(errors_log)
        err_path.parent.mkdir(parents=True, exist_ok=True)
        entry = (
            f"\n## [{time.strftime('%Y-%m-%d %H:%M:%S')}] Local Worker Failure\n"
            f"- Task ID: {task_id}\n"
            f"- Primary Worker: {primary_worker}\n"
            f"- Error: {error_reason}\n"
            f"- Fallback Action: {fallback_action}\n"
        )
        with open(err_path, "a", encoding="utf-8") as f:
            f.write(entry)
    if is_sensitive:
        raise SensitiveTaskFallbackBlocked(
            f"task {task_id!r} is sensitive: local model failed ({error_reason}) "
            "and no cloud worker fallback is permitted; failing closed per Rule 3.5"
        )
    return fallback_worker


def escalate_routing_effort(
    complexity: str,
    current_effort: str,
    attempts: int = 1,
) -> tuple[str, str]:
    """Escalate reasoning effort and model tier after 2+ failed worker attempts."""
    if attempts < 2:
        worker = (
            "codex_terra"
            if complexity in ("trivial", "simple")
            else "claude_sonnet_5"
        )
        return current_effort, worker

    effort_ladder = ["low", "medium", "high", "ultra"]
    cur_idx = effort_ladder.index(current_effort) if current_effort in effort_ladder else 1
    new_idx = min(cur_idx + (attempts - 1), len(effort_ladder) - 1)
    escalated_effort = effort_ladder[new_idx]

    if escalated_effort in ("high", "ultra") or complexity in ("medium", "complex"):
        worker = "claude_opus_5"
    else:
        worker = "codex_sol"

    return escalated_effort, worker


def lexical_safety_scan(task: str) -> bool:
    """Tier 1: return false for malformed or shell-shaped task text."""
    try:
        tokens = shlex.split(task, posix=True)
    except ValueError:
        return False
    return bool(tokens) and not any(
        marker in token for token in tokens for marker in UNSAFE_LEXEMES
    )


def evaluate_tier2(complexity: str, effort: str | None = None) -> tuple[str, str]:
    """Tier 2: validate complexity and apply its calibrated default effort."""
    normalized_complexity = complexity.lower().strip()
    if normalized_complexity not in VALID_COMPLEXITIES:
        raise ValueError(f"unsupported complexity: {complexity!r}")
    if effort is None:
        return normalized_complexity, DEFAULT_EFFORTS[normalized_complexity]
    normalized_effort = effort.lower().strip()
    if normalized_effort not in VALID_EFFORTS:
        raise ValueError(f"unsupported effort: {effort!r}")
    return normalized_complexity, normalized_effort


def _canonical_json(value: dict[str, str]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def deterministic_nonce(
    task_id: str, task: str, complexity: str, effort: str, decision: str
) -> str:
    """Return a stable nonce that binds a decision to its complete input."""
    payload = _canonical_json(
        {
            "task_id": task_id,
            "task": task,
            "complexity": complexity,
            "effort": effort,
            "decision": decision,
        }
    )
    return hashlib.sha256(payload).hexdigest()[:32]


def generate_calibration_signature(
    task_id: str,
    task: str,
    complexity: str,
    effort: str,
    decision: str,
    nonce: str,
    secret: bytes | None = None,
) -> str:
    """Compatibility facade for :meth:`AgentCouncil.generate_signature`."""
    if secret is None:
        secret = get_calibration_secret(None, create=False)
    return AgentCouncil.generate_signature(
        {
            "task_id": task_id,
            "task": task,
            "complexity": complexity,
            "effort": effort,
            "decision": decision,
            "nonce": nonce,
        },
        secret,
    )


def get_calibration_secret(root_dir: Path | None, *, create: bool = True) -> bytes:
    """Compatibility facade for the central secret provider.

    ``create`` retains its historical meaning: council generation may create a
    workspace key, whereas signature verification must remain read-only.
    """
    return AgentCouncil.load_secret(root_dir, read_only=not create)


def verify_calibration_signature(manifest: dict[str, Any], secret: bytes) -> bool:
    """Pure module function to verify a calibration-v1 signature."""
    return AgentCouncil.verify_signature(manifest, secret=secret)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON without exposing a partially-written manifest/cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class AgentCouncil:
    """Tier 3 manifest generator with an isolated, expiring planning cache."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = (root_dir or Path.cwd()).resolve()
        self.decisions_dir = self.root_dir / ".ralph" / "decisions"
        self.cache_file = self.root_dir / ".ralph" / "cache" / "planning_cache.json"

    def _calibration_secret(self) -> bytes:
        """Compatibility wrapper around the central secret provider."""
        return self.load_secret(self.root_dir, read_only=False)

    @staticmethod
    def load_secret(
        root_dir: str | Path | None = None, read_only: bool = True
    ) -> bytes:
        """Load the calibration secret without trusting unsafe local key files.

        An explicit environment secret has priority.  Workspace keys are only
        accepted when they are ordinary, non-symlink files no larger than 4 KiB.
        Verification callers use the default read-only mode, so a missing key
        cannot create authentication material as a side effect.
        """
        configured_secret = os.environ.get("AGY_CALIBRATION_SECRET")
        if configured_secret:
            return configured_secret.encode("utf-8")

        secret_file = (
            Path(root_dir or ".").resolve()
            / ".ralph"
            / "cache"
            / "calibration.key"
        )

        if secret_file.is_symlink():
            raise ValueError(f"calibration secret must be a regular file: {secret_file}")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        try:
            descriptor = os.open(secret_file, flags)
        except OSError:
            if secret_file.is_symlink():
                raise ValueError(f"calibration secret must be a regular file: {secret_file}")
            if read_only:
                raise FileNotFoundError(secret_file)
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            generated = secrets.token_bytes(32)
            try:
                descriptor = os.open(
                    secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                return AgentCouncil.load_secret(root_dir, read_only=True)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(generated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(secret_file, 0o600)
            return generated

        try:
            stat_result = os.fstat(descriptor)
            if not os.path.isabs(str(secret_file)) or secret_file.is_symlink():
                raise ValueError(f"calibration secret must be a regular file: {secret_file}")
            if stat_result.st_size > 4096:
                raise ValueError(f"calibration secret exceeds 4096 bytes: {secret_file}")
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                secret = stream.read()
                descriptor = -1
            if not secret:
                raise ValueError(f"calibration secret is empty: {secret_file}")
            return secret
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def generate_signature(manifest: dict[str, Any], secret: bytes) -> str:
        """Return the calibration-v1 HMAC for the decision-bearing fields."""
        payload: dict[str, str] = {}
        for field in CALIBRATION_FIELDS:
            value = manifest.get(field)
            if not isinstance(value, str):
                raise TypeError(f"calibration manifest field {field!r} must be a string")
            payload[field] = value
        return hmac.new(secret, _canonical_json(payload), hashlib.sha256).hexdigest()

    @staticmethod
    def validate_manifest_structure(manifest: dict[str, Any]) -> bool:
        """Return whether a manifest contains a syntactically valid v1 signature."""
        return (
            isinstance(manifest, dict)
            and all(isinstance(manifest.get(field), str) for field in CALIBRATION_FIELDS)
            and isinstance(manifest.get("signature"), str)
            and HMAC_SHA256_RE.fullmatch(manifest["signature"]) is not None
        )

    @staticmethod
    def verify_signature(
        manifest: dict[str, Any],
        secret: bytes | None = None,
        root_dir: str | Path | None = None,
    ) -> bool:
        """Fail closed unless ``manifest`` has a valid calibration-v1 HMAC."""
        if not AgentCouncil.validate_manifest_structure(manifest):
            return False
        try:
            verification_secret = (
                secret
                if secret is not None
                else AgentCouncil.load_secret(root_dir, read_only=True)
            )
            expected = AgentCouncil.generate_signature(manifest, verification_secret)
        except (OSError, ValueError):
            return False
        return hmac.compare_digest(manifest["signature"].lower(), expected)

    @staticmethod
    def _task_id(task: str, complexity: str, effort: str, task_id: str | None) -> str:
        if task_id is not None:
            if not TASK_ID_RE.fullmatch(task_id):
                raise ValueError("task_id must contain only letters, digits, '.', '_' or '-'")
            return task_id
        digest = hashlib.sha256(f"{task}\0{complexity}\0{effort}".encode()).hexdigest()
        return f"task-{digest[:16]}"

    def _load_cache(self) -> dict[str, dict[str, Any]]:
        try:
            loaded = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if (
            not isinstance(loaded, dict)
            or loaded.get("version") != CACHE_VERSION
            or set(loaded) != {"version", "entries"}
        ):
            return {}
        entries = loaded.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {
            key: entry
            for key, entry in entries.items()
            if isinstance(key, str) and isinstance(entry, dict)
        }

    @staticmethod
    def _constraints_satisfied(task: str, complexity: str, effort: str) -> bool:
        return (
            bool(task.strip())
            and complexity in VALID_COMPLEXITIES
            and effort in VALID_EFFORTS
        )

    @classmethod
    def _debate_round_plan(
        cls, task: str, complexity: str, effort: str, safe: bool
    ) -> list[dict[str, Any]]:
        constraints_satisfied = cls._constraints_satisfied(
            task, complexity, effort
        )
        rounds = [
            {
                "round": 1,
                "focus": "safety",
                "assessment": (
                    "task passed lexical safety scan"
                    if safe
                    else "task contains malformed or shell-shaped input"
                ),
                "vote": "APPROVE" if safe else "REJECT",
            },
            {
                "round": 2,
                "focus": "constraints",
                "assessment": (
                    "task and calibration constraints are valid"
                    if constraints_satisfied
                    else "task or calibration constraints are invalid"
                ),
                "vote": "APPROVE" if constraints_satisfied else "REJECT",
            },
        ]
        if len({item["vote"] for item in rounds}) > 1:
            accepted = safe and constraints_satisfied
            rounds.append(
                {
                    "round": 3,
                    "focus": "adjudication",
                    "assessment": (
                        "all safety and constraint checks passed"
                        if accepted
                        else "safety or constraint rejection is controlling"
                    ),
                    "vote": "APPROVE" if accepted else "REJECT",
                }
            )
        return rounds[:MAX_DEBATE_ROUNDS]

    @staticmethod
    def _valid_debate_rounds(rounds: Any, consensus_round: Any) -> bool:
        if (
            not isinstance(rounds, list)
            or not 2 <= len(rounds) <= MAX_DEBATE_ROUNDS
            or not isinstance(consensus_round, int)
            or consensus_round != len(rounds)
        ):
            return False
        required = {"round", "focus", "assessment", "vote"}
        return all(
            isinstance(item, dict)
            and set(item) == required
            and item["round"] == index
            and item["focus"] in {"safety", "constraints", "adjudication"}
            and item["vote"] in {"APPROVE", "REJECT"}
            and isinstance(item["assessment"], str)
            for index, item in enumerate(rounds, 1)
        )

    def _valid_cached_manifest(
        self,
        manifest: Any,
        *,
        task_id: str,
        task: str,
        complexity: str,
        effort: str,
        safe: bool,
        secret: bytes,
    ) -> bool:
        if not isinstance(manifest, dict):
            return False
        if (
            TASK_ID_RE.fullmatch(task_id) is None
            or not task.strip()
            or complexity not in VALID_COMPLEXITIES
            or effort not in VALID_EFFORTS
        ):
            return False
        decision = "APPROVED" if safe else "REJECTED_LEXICAL"
        expected_fields = {
            "task_id",
            "task",
            "complexity",
            "effort",
            "decision",
            "signature",
            "nonce",
            "debate_rounds",
            "consensus_status",
            "consensus_round",
        }
        if set(manifest) != expected_fields:
            return False
        if any(
            manifest.get(key) != expected
            for key, expected in {
                "task_id": task_id,
                "task": task,
                "complexity": complexity,
                "effort": effort,
                "decision": decision,
                "consensus_status": "CONSENSUS_REACHED",
            }.items()
        ):
            return False
        nonce = deterministic_nonce(task_id, task, complexity, effort, decision)
        if manifest.get("nonce") != nonce:
            return False
        if not self._valid_debate_rounds(
            manifest.get("debate_rounds"), manifest.get("consensus_round")
        ):
            return False
        if manifest.get("debate_rounds") != self._debate_round_plan(
            task, complexity, effort, safe
        ):
            return False
        signature = manifest.get("signature")
        if not isinstance(signature, str):
            return False
        expected_signature = generate_calibration_signature(
            task_id, task, complexity, effort, decision, nonce, secret
        )
        return hmac.compare_digest(signature, expected_signature)

    async def _tier3_debate_manifest(
        self, task_id: str, task: str, complexity: str, effort: str, safe: bool, secret: bytes
    ) -> dict[str, Any]:
        """Run deterministic safety and constraint passes, then reach consensus."""
        rounds = self._debate_round_plan(task, complexity, effort, safe)
        for _round in rounds:
            await asyncio.sleep(0)

        approved = all(item["vote"] == "APPROVE" for item in rounds)
        decision = "APPROVED" if approved else "REJECTED_LEXICAL"
        nonce = deterministic_nonce(task_id, task, complexity, effort, decision)
        return {
            "task_id": task_id,
            "task": task,
            "complexity": complexity,
            "effort": effort,
            "decision": decision,
            "signature": generate_calibration_signature(
                task_id, task, complexity, effort, decision, nonce, secret
            ),
            "nonce": nonce,
            "debate_rounds": rounds,
            "consensus_status": "CONSENSUS_REACHED",
            "consensus_round": len(rounds),
        }

    def run(
        self, task: str, complexity: str, effort: str | None = None, task_id: str | None = None
    ) -> dict[str, Any]:
        safe = lexical_safety_scan(task)
        normalized_complexity, normalized_effort = evaluate_tier2(complexity, effort)
        normalized_task_id = self._task_id(task, normalized_complexity, normalized_effort, task_id)
        cache_key = hashlib.sha256(
            _canonical_json(
                {
                    "task_id": normalized_task_id,
                    "task": task,
                    "complexity": normalized_complexity,
                    "effort": normalized_effort,
                }
            )
        ).hexdigest()
        now = time.time()
        secret = self._calibration_secret()
        cache = self._load_cache()
        valid_cache: dict[str, dict[str, Any]] = {}
        for key, entry in cache.items():
            if not isinstance(key, str) or not isinstance(entry, dict) or set(entry) != {
                "created_at",
                "manifest",
            }:
                continue
            created_at = entry.get("created_at")
            if (
                not isinstance(created_at, (int, float))
                or isinstance(created_at, bool)
                or not math.isfinite(created_at)
                or created_at > now
                or now - created_at >= CACHE_TTL_SECONDS
            ):
                continue
            candidate = entry.get("manifest")
            if not isinstance(candidate, dict):
                continue
            candidate_task_id = candidate.get("task_id")
            candidate_task = candidate.get("task")
            candidate_complexity = candidate.get("complexity")
            candidate_effort = candidate.get("effort")
            if not (
                isinstance(candidate_task_id, str)
                and isinstance(candidate_task, str)
                and isinstance(candidate_complexity, str)
                and isinstance(candidate_effort, str)
            ):
                continue
            identity = {
                "task_id": candidate_task_id,
                "task": candidate_task,
                "complexity": candidate_complexity,
                "effort": candidate_effort,
            }
            expected_key = hashlib.sha256(_canonical_json(identity)).hexdigest()
            if key != expected_key or not self._valid_cached_manifest(
                candidate,
                task_id=candidate_task_id,
                task=candidate_task,
                complexity=candidate_complexity,
                effort=candidate_effort,
                safe=lexical_safety_scan(candidate_task),
                secret=secret,
            ):
                continue
            valid_cache[key] = entry
        cache = valid_cache
        cached_entry = cache.get(cache_key)
        cached = cached_entry.get("manifest") if cached_entry is not None else None
        manifest: dict[str, Any]
        if isinstance(cached, dict) and self._valid_cached_manifest(
            cached,
            task_id=normalized_task_id,
            task=task,
            complexity=normalized_complexity,
            effort=normalized_effort,
            safe=safe,
            secret=secret,
        ):
            manifest = cached
        else:
            manifest = asyncio.run(
                self._tier3_debate_manifest(
                    normalized_task_id, task, normalized_complexity, normalized_effort, safe, secret
                )
            )
            cache[cache_key] = {"created_at": now, "manifest": manifest}
            _atomic_json_write(
                self.cache_file, {"version": CACHE_VERSION, "entries": cache}
            )

        # Always materialize the task-specific decision, including cache hits.
        _atomic_json_write(self.decisions_dir / f"{normalized_task_id}.json", manifest)
        return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="3-tier deterministic Agent Council")
    parser.add_argument("--task", required=True, help="Task description")
    parser.add_argument("--complexity", required=True, choices=sorted(VALID_COMPLEXITIES))
    parser.add_argument("--effort", choices=sorted(VALID_EFFORTS))
    parser.add_argument("--task-id", help="Optional stable task identifier")
    args = parser.parse_args()
    try:
        manifest = AgentCouncil().run(args.task, args.complexity, args.effort, args.task_id)
    except ValueError as error:
        parser.error(str(error))
    print(f"[CALIBRATION: {manifest['signature']}]")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
