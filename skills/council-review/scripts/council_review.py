import json
import hashlib
import os
from dataclasses import dataclass
from typing import Optional, Dict, List

class PrivacyMode:
    AUTO = "auto"
    LOCAL_ONLY = "local-only"

@dataclass
class ReviewRequest:
    objective: str
    workspace_root: str
    subject: str = ""
    privacy_mode: str = PrivacyMode.AUTO

    def __post_init__(self):
        if not self.workspace_root:
            raise ValueError("workspace_root is required")
        if self.privacy_mode not in [PrivacyMode.AUTO, PrivacyMode.LOCAL_ONLY]:
            raise ValueError("Invalid privacy mode")


@dataclass
class ReviewOutcome:
    status: str
    run_id: str
    report_path: Optional[str] = None
    manifest_path: Optional[str] = None
    unresolved_blockers: int = 0
    source_changed: bool = False

class FindingLedger:
    def __init__(self):
        self.findings: Dict[str, dict] = {}
        
    def add_finding(self, finding: dict):
        self.findings[finding['id']] = finding
        
    def get_unresolved_blockers(self) -> int:
        count = 0
        for f in self.findings.values():
            if f.get('severity') in ['critical', 'high'] and not f.get('resolved', False):
                count += 1
        return count

class ConsensusTable:
    def __init__(self, policy: List[str]):
        self.policy = policy
        
    def evaluate(self, votes: List[dict]) -> str:
        providers = {v['provider'] for v in votes}
        if len(providers) < 3:
            return "INCOMPLETE"
            
        hashes = {v.get('candidate_hash') for v in votes}
        if len(hashes) > 1:
            return "MATERIAL_DISAGREEMENT"
            
        approvals = sum(1 for v in votes if v.get('vote') == 'approve')
        if approvals == len(votes):
            return "UNANIMOUS"
            
        return "MATERIAL_DISAGREEMENT"

class ReviewCouncil:
    def __init__(self, policy_path: str):
        with open(policy_path, 'r') as f:
            self.policy = json.load(f)

    def _hash_source(self, path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        hasher = hashlib.sha256()
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                hasher.update(f.read())
        else:
            for root, _, files in sorted(os.walk(path)):
                for name in sorted(files):
                    p = os.path.join(root, name)
                    hasher.update(name.encode())
                    with open(p, 'rb') as f:
                        hasher.update(f.read())
        return hasher.hexdigest()

    async def _execute_round(self, adapters: List[Any], envelope: str, round_num: int, deadline: int) -> List[dict]:
        import asyncio
        tasks = [a.review(envelope, round_num, deadline) for a in adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_results = []
        for a, r in zip(adapters, results):
            if isinstance(r, Exception):
                valid_results.append({"provider": getattr(a, 'provider_id', 'unknown'), "vote": "abstain", "error": str(r)})
            else:
                valid_results.append(r)
        return valid_results

    async def review(self, request: ReviewRequest) -> ReviewOutcome:
        initial_hash = self._hash_source(request.subject)
        
        # Load providers based on policy
        from provider_adapters import FakeReviewerAdapter
        # For scaffolding Phase 4, we use fake adapters representing the 3 primary ones
        adapters = [
            FakeReviewerAdapter("claude", [{"provider": "claude", "vote": "approve", "candidate_hash": "synth1"}]),
            FakeReviewerAdapter("codex", [{"provider": "codex", "vote": "approve", "candidate_hash": "synth1"}]),
            FakeReviewerAdapter("agy", [{"provider": "agy", "vote": "approve", "candidate_hash": "synth1"}])
        ]
        
        # Round 1 (Blind full reviews)
        round1_votes = await self._execute_round(adapters, "envelope_hash", 1, self.policy.get('deadlines_seconds', {}).get('round_1', 120))
        
        # Non-voting candidate synthesis
        # In a real implementation this creates a unified plan/candidate_hash
        candidate_hash = "synth1"
        
        # Round 2 ratification
        round2_votes = await self._execute_round(adapters, candidate_hash, 2, self.policy.get('deadlines_seconds', {}).get('round_2', 60))
        
        table = ConsensusTable(self.policy.get("consensus_policy", []))
        consensus = table.evaluate(round2_votes)
        
        # Local advisory packet and Round 3 reconciliation would trigger if MATERIAL_DISAGREEMENT
        if consensus == "MATERIAL_DISAGREEMENT":
            # Round 3
            round3_votes = await self._execute_round(adapters, candidate_hash, 3, self.policy.get('deadlines_seconds', {}).get('round_3', 60))
            consensus = table.evaluate(round3_votes)
            if consensus == "MATERIAL_DISAGREEMENT":
                consensus = "UNRESOLVED"
                
        final_hash = self._hash_source(request.subject)
        source_changed = initial_hash != final_hash
        
        if source_changed:
            consensus = "UNRESOLVED"
            
        manifest = self._generate_manifest(consensus, "run-001")
            
        return ReviewOutcome(
            status=consensus,
            run_id="run-001",
            source_changed=source_changed,
            manifest_path="council-manifest-v1.json"
        )
        
    def _generate_manifest(self, status: str, run_id: str) -> dict:
        import hmac
        manifest = {
            "metadata": {"status": status, "run_id": run_id},
            "events": []
        }
        
        # create HMAC
        canonical = json.dumps(manifest, separators=(',', ':'), sort_keys=True).encode()
        if "COUNCIL_REVIEW_SECRET" not in os.environ:
            raise RuntimeError("COUNCIL_REVIEW_SECRET environment variable is missing.")
        secret = os.environ["COUNCIL_REVIEW_SECRET"].encode()
        council_hmac = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
        
        manifest["council_hmac"] = council_hmac
        return manifest
