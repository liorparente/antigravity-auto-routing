import asyncio
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provider_adapters import ReviewerAdapter, build_adapter


ROUTING_CONFIG_PATH = Path(__file__).resolve().parents[2] / "worker-routing" / "routing-config.json"
DEFAULT_CONSULTATION_POLICY: dict[str, Any] = {
    "providers": [
        {"id": "claude", "model": "claude-opus-5", "effort_mapping": {"high": "high"}},
        {"id": "codex", "model": "gpt-5.6-sol", "effort_mapping": {"high": "high"}},
        {"id": "gemini", "model": "gemini-3.1-pro", "effort_mapping": {"high": "high"}},
    ],
    "adjudicators": [
        {"id": "lm-studio", "model": "qwen3-coder-30b", "effort_mapping": {"high": "high"}}
    ],
    "deadlines_seconds": {"round_1": 120, "round_2": 60, "round_3": 60},
    "consensus_policy": [
        "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED",
    ],
    "weighting": {
        "initial_weights": {"claude": 0.40, "codex": 0.40, "gemini": 0.20},
        "min_weight": 0.05,
        "max_weight": 0.65,
        "quorum_threshold": 0.60,
        "dynamic_weights_path": ".ralph/council_weights.json",
    },
    "security_veto": {
        "enabled": True,
        "veto_severities": ["critical", "high"],
        "security_threshold": 0.80,
    },
}


def _merge_policy_defaults(defaults: dict[str, Any], configured: object) -> dict[str, Any]:
    """Overlay a policy section onto its schema defaults without sharing state."""
    if not isinstance(configured, dict):
        return deepcopy(defaults)

    merged = deepcopy(defaults)
    for key, default_value in defaults.items():
        value = configured.get(key)
        if isinstance(default_value, dict):
            merged[key] = _merge_policy_defaults(default_value, value)
        elif value is not None and isinstance(value, type(default_value)):
            merged[key] = deepcopy(value)
    return merged


def load_consultation_policy(config_path: Path = ROUTING_CONFIG_PATH) -> dict[str, Any]:
    """Load ``consultation_policy`` with safe defaults for absent policy keys.

    File and JSON parsing errors deliberately raise, matching the existing
    routing configuration loaders' fail-safe contract.
    """
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    return _merge_policy_defaults(DEFAULT_CONSULTATION_POLICY, config.get("consultation_policy", {}))


_load_consultation_policy = load_consultation_policy


class PrivacyMode:
    AUTO = "auto"
    LOCAL_ONLY = "local-only"


@dataclass
class ReviewRequest:
    objective: str
    workspace_root: str
    subject: str = ""
    privacy_mode: str = PrivacyMode.AUTO

    def __post_init__(self) -> None:
        if not self.workspace_root:
            raise ValueError("workspace_root is required")
        if self.privacy_mode not in [PrivacyMode.AUTO, PrivacyMode.LOCAL_ONLY]:
            raise ValueError("Invalid privacy mode")


@dataclass
class ReviewOutcome:
    status: str
    run_id: str
    report_path: str | None = None
    manifest_path: str | None = None
    unresolved_blockers: int = 0
    source_changed: bool = False


DEFAULT_VOTE_CONFIDENCE: dict[str, float] = {
    "approve": 1.0,
    "revise": -0.3,
    "block": -1.0,
    "abstain": 0.0,
}

NEGATIVE_LOSS_MULTIPLIER = 1.5


class ConsensusTable:
    def __init__(
        self,
        policy: list[str],
        weights: dict[str, float] | None = None,
        quorum_threshold: float = 0.60,
    ) -> None:
        self.policy = policy
        self.weights = weights or {}
        self.quorum_threshold = quorum_threshold

    def _confidence(self, vote: dict[str, Any]) -> float:
        confidence = vote.get("confidence")
        if confidence is None:
            confidence = DEFAULT_VOTE_CONFIDENCE.get(str(vote.get("vote", "")).lower(), 0.0)
        try:
            val = float(confidence)
        except (ValueError, TypeError):
            val = 0.0
        return max(-1.0, min(1.0, val))

    def weighted_score(self, votes: list[dict[str, Any]]) -> float:
        # If all voters are local or unweighted, default each to equal weight
        total_weight = sum(self.weights.get(v.get("provider", ""), 0.0) for v in votes)
        if total_weight <= 0:
            total_weight = float(len(votes))
            effective_weights = {v.get("provider", ""): 1.0 for v in votes}
        else:
            effective_weights = self.weights

        score = 0.0
        for vote in votes:
            provider = vote.get("provider", "")
            weight = effective_weights.get(provider, 1.0 if total_weight == len(votes) else 0.0)
            confidence = self._confidence(vote)
            if confidence < 0:
                confidence *= NEGATIVE_LOSS_MULTIPLIER
            score += weight * confidence
        return score / total_weight

    def evaluate(self, votes: list[dict[str, Any]]) -> str:
        providers = {v.get("provider") for v in votes if v.get("provider")}
        if len(providers) < 1:
            return "INCOMPLETE"

        hashes = {v.get("candidate_hash") for v in votes if v.get("candidate_hash")}
        if len(hashes) > 1:
            return "MATERIAL_DISAGREEMENT"

        score = self.weighted_score(votes)
        if score < self.quorum_threshold:
            return "MATERIAL_DISAGREEMENT"

        approvals = sum(1 for v in votes if str(v.get("vote", "")).lower() == "approve")
        if approvals == len(votes):
            return "UNANIMOUS"

        return "QUALIFIED"


class SecurityVeto(Exception):
    """A unilateral security veto that short-circuits the council before
    weighted scoring runs — a majority of lenient votes must never override
    a valid security finding from a single provider."""

    def __init__(self, provider: str, finding: dict[str, Any]) -> None:
        self.provider = provider
        self.finding = finding
        claim = finding.get("claim", finding.get("id", "unspecified"))
        super().__init__(f"Security veto by {provider}: {claim}")


class SecurityVetoHandler:
    def __init__(self, veto_severities: list[str], security_threshold: float, enabled: bool = True) -> None:
        self.veto_severities = {s.lower() for s in veto_severities}
        self.security_threshold = security_threshold
        self.enabled = enabled

    def check(self, votes: list[dict[str, Any]]) -> SecurityVeto | None:
        if not self.enabled:
            return None
        for vote in votes:
            for finding in vote.get("findings", []):
                severity = str(finding.get("severity", "")).lower()
                if severity not in self.veto_severities:
                    continue
                raw_confidence = finding.get("confidence", 1.0)
                try:
                    confidence = float(raw_confidence)
                except (TypeError, ValueError):
                    confidence = 1.0  # Fail-closed: unparseable confidence is treated as certain

                if confidence >= self.security_threshold:
                    return SecurityVeto(str(vote.get("provider", "unknown")), finding)
        return None


class ReviewCouncil:
    def __init__(self, config_path: str | Path = ROUTING_CONFIG_PATH) -> None:
        self.policy = load_consultation_policy(Path(config_path))

    def _resolve_secret(self, workspace_root: str) -> bytes:
        # Check AGY_CALIBRATION_SECRET or COUNCIL_REVIEW_SECRET
        for env_var in ["AGY_CALIBRATION_SECRET", "COUNCIL_REVIEW_SECRET"]:
            if env_var in os.environ and os.environ[env_var].strip():
                return os.environ[env_var].strip().encode()

        cal_key_path = os.path.join(workspace_root or ".", ".ralph", "cache", "calibration.key")
        if os.path.isfile(cal_key_path):
            with open(cal_key_path, "rb") as f:
                content = f.read().strip()
                if content:
                    return content

        raise RuntimeError(
            "Council HMAC secret resolution failed: AGY_CALIBRATION_SECRET is unset "
            "and no workspace key found at .ralph/cache/calibration.key."
        )

    def _load_weights(self, workspace_root: str) -> dict[str, float]:
        weighting = self.policy.get("weighting", {})
        weights = dict(weighting.get("initial_weights", {}))
        lo = weighting.get("min_weight", 0.05)
        hi = weighting.get("max_weight", 0.65)
        dynamic_path = weighting.get("dynamic_weights_path")

        if not dynamic_path:
            return weights

        full_path = os.path.join(workspace_root or ".", dynamic_path)
        if not os.path.isfile(full_path):
            return weights

        try:
            with open(full_path, "r") as f:
                dynamic = json.load(f)
            if isinstance(dynamic, dict):
                for provider, value in dynamic.items():
                    if provider not in weights:
                        continue  # Never let a dynamic file introduce an unauthorized voter
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    weights[provider] = max(lo, min(hi, float(value)))
        except (json.JSONDecodeError, OSError):
            pass

        return weights

    def _resolve_adapters(self, request: ReviewRequest) -> list[ReviewerAdapter]:
        if request.privacy_mode == PrivacyMode.LOCAL_ONLY:
            adjudicators = self.policy.get("adjudicators", [])
            adapters = [build_adapter(a) for a in adjudicators]
            if not adapters:
                raise RuntimeError(
                    "local-only review requested but no local adjudicator is configured "
                    "— failing closed rather than egressing data to cloud."
                )
            return adapters
        else:
            providers = self.policy.get("providers", [])
            return [build_adapter(p) for p in providers]

    def _hash_source(self, path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        hasher = hashlib.sha256()
        if os.path.isfile(path):
            with open(path, "rb") as f:
                hasher.update(f.read())
        else:
            for root, _, files in sorted(os.walk(path)):
                for name in sorted(files):
                    p = os.path.join(root, name)
                    rel_p = os.path.relpath(p, path)
                    hasher.update(f"{rel_p}\0".encode())
                    with open(p, "rb") as f:
                        hasher.update(f.read())
        return hasher.hexdigest()

    async def _execute_round(
        self, adapters: Sequence[ReviewerAdapter], envelope: str, round_num: int, deadline: int
    ) -> list[dict[str, Any]]:
        tasks = [a.review(envelope, round_num, deadline) for a in adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: list[dict[str, Any]] = []
        for a, r in zip(adapters, results):
            if isinstance(r, Exception):
                valid_results.append({
                    "provider": getattr(a, "provider_id", "unknown"),
                    "vote": "abstain",
                    "confidence": 0.0,
                    "error": str(r),
                })
            elif isinstance(r, dict):
                valid_results.append(r)
        return valid_results

    def _write_manifest(
        self, status: str, run_id: str, workspace_root: str, security_veto: SecurityVeto | None = None
    ) -> str:
        manifest_path = os.path.join(workspace_root, ".ralph", f"council-manifest-{run_id}.json")
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)

        manifest: dict[str, Any] = {
            "metadata": {"status": status, "run_id": run_id},
            "events": [],
        }
        if security_veto:
            manifest["security_veto"] = {
                "provider": security_veto.provider,
                "finding": security_veto.finding,
            }

        canonical = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
        secret = self._resolve_secret(workspace_root)
        manifest["council_hmac"] = hmac.new(secret, canonical, hashlib.sha256).hexdigest()

        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        return manifest_path

    async def review(
        self, request: ReviewRequest, custom_adapters: Sequence[ReviewerAdapter] | None = None
    ) -> ReviewOutcome:
        initial_hash = self._hash_source(request.subject)
        weights = self._load_weights(request.workspace_root)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        weighting_policy = self.policy.get("weighting", {})
        security_policy = self.policy.get("security_veto", {})

        veto_handler = SecurityVetoHandler(
            veto_severities=security_policy.get("veto_severities", ["critical", "high"]),
            security_threshold=security_policy.get("security_threshold", 0.80),
            enabled=security_policy.get("enabled", True),
        )
        table = ConsensusTable(
            self.policy.get("consensus_policy", []),
            weights=weights,
            quorum_threshold=weighting_policy.get("quorum_threshold", 0.60),
        )

        adapters = custom_adapters if custom_adapters is not None else self._resolve_adapters(request)

        # Round 1 (Blind full reviews)
        deadline_r1 = self.policy.get("deadlines_seconds", {}).get("round_1", 120)
        round1_votes = await self._execute_round(adapters, request.objective, 1, deadline_r1)

        veto = veto_handler.check(round1_votes)
        if veto is not None:
            manifest_path = self._write_manifest("SECURITY_HALT", run_id, request.workspace_root, veto)
            return ReviewOutcome(
                status="SECURITY_HALT",
                run_id=run_id,
                unresolved_blockers=1,
                manifest_path=manifest_path,
            )

        candidate_hash = "synth1"

        # Round 2 ratification
        deadline_r2 = self.policy.get("deadlines_seconds", {}).get("round_2", 60)
        round2_votes = await self._execute_round(adapters, candidate_hash, 2, deadline_r2)

        veto = veto_handler.check(round2_votes)
        if veto is not None:
            manifest_path = self._write_manifest("SECURITY_HALT", run_id, request.workspace_root, veto)
            return ReviewOutcome(
                status="SECURITY_HALT",
                run_id=run_id,
                unresolved_blockers=1,
                manifest_path=manifest_path,
            )

        consensus = table.evaluate(round2_votes)

        if consensus == "MATERIAL_DISAGREEMENT":
            # Round 3 reconciliation
            deadline_r3 = self.policy.get("deadlines_seconds", {}).get("round_3", 60)
            round3_votes = await self._execute_round(adapters, candidate_hash, 3, deadline_r3)

            veto = veto_handler.check(round3_votes)
            if veto is not None:
                manifest_path = self._write_manifest("SECURITY_HALT", run_id, request.workspace_root, veto)
                return ReviewOutcome(
                    status="SECURITY_HALT",
                    run_id=run_id,
                    unresolved_blockers=1,
                    manifest_path=manifest_path,
                )

            consensus = table.evaluate(round3_votes)
            if consensus == "MATERIAL_DISAGREEMENT":
                consensus = "UNRESOLVED"

        final_hash = self._hash_source(request.subject)
        source_changed = bool(request.subject) and (initial_hash != final_hash)

        if source_changed:
            consensus = "UNRESOLVED"

        manifest_path = self._write_manifest(consensus, run_id, request.workspace_root)

        return ReviewOutcome(
            status=consensus,
            run_id=run_id,
            source_changed=source_changed,
            manifest_path=manifest_path,
        )
