import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provider_adapters import ReviewerAdapter, build_adapter


def _load_worker_routing_module(module_name: str) -> Any:
    """Load shared worker-routing code without relying on a hyphenated package."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).resolve().parents[2] / "worker-routing" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_debate_state_machine = _load_worker_routing_module("debate_state_machine")
_consultation_policy = _load_worker_routing_module("consultation_policy")
ConsensusTable = _debate_state_machine.ConsensusTable
SecurityVeto = _debate_state_machine.SecurityVeto
SecurityVetoHandler = _debate_state_machine.SecurityVetoHandler

ROUTING_CONFIG_PATH = _consultation_policy.ROUTING_CONFIG_PATH
DEFAULT_CONSULTATION_POLICY = _consultation_policy.DEFAULT_CONSULTATION_POLICY
load_consultation_policy = _consultation_policy.load_consultation_policy


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


class ReviewCouncil:
    def __init__(self, policy_path: str | Path = ROUTING_CONFIG_PATH) -> None:
        self.policy = load_consultation_policy(Path(policy_path))

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

        consensus = table.evaluate(round2_votes, expected_hash=candidate_hash)

        if consensus in ("MATERIAL_DISAGREEMENT", "UNRESOLVED"):
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

            consensus = table.evaluate(round3_votes, expected_hash=candidate_hash)
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
