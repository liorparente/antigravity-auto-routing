import asyncio
import hashlib
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from council_review import (
    DEFAULT_CONSULTATION_POLICY,
    PrivacyMode,
    ReviewCouncil,
    ReviewOutcome,
    ReviewRequest,
    SecurityVetoHandler,
    load_consultation_policy,
)
from provider_adapters import (
    AgyAdapter,
    ClaudeAdapter,
    CLIReviewerAdapter,
    CodexAdapter,
    FakeReviewerAdapter,
    PerspectiveReviewerAdapter,
)

ROUTING_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "worker-routing" / "routing-config.json"
)


class CouncilReviewTDDTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.temp_dir.name
        self.cal_dir = os.path.join(self.workspace_root, ".ralph", "cache")
        os.makedirs(self.cal_dir, exist_ok=True)
        self.cal_key = os.path.join(self.cal_dir, "calibration.key")
        with open(self.cal_key, "w") as f:
            f.write("test_secret_key_12345")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Slice 1: Security Veto execution with uppercase severity support
    def test_unilateral_security_veto_halts_review(self) -> None:
        veto_handler = SecurityVetoHandler(
            veto_severities=["critical", "high"],
            security_threshold=0.80,
            enabled=True,
        )
        votes: list[dict[str, Any]] = [
            {"provider": "claude", "vote": "approve", "confidence": 1.0},
            {"provider": "codex", "vote": "block", "confidence": "0.95", "findings": [
                {"id": "SEC-01", "severity": "CRITICAL", "claim": "SQL injection in auth", "confidence": "0.90"}
            ]},
            {"provider": "gemini", "vote": "approve", "confidence": 0.8},
        ]
        veto = veto_handler.check(votes)
        self.assertIsNotNone(veto)
        assert veto is not None
        self.assertEqual(veto.provider, "codex")
        self.assertEqual(veto.finding["id"], "SEC-01")

    def test_nonfinite_or_out_of_range_security_confidence_fails_closed(self) -> None:
        handler = SecurityVetoHandler(["high"], 0.80)
        for confidence in (math.nan, math.inf, -math.inf, 1.1, -0.1):
            veto = handler.check([{"provider": "codex", "findings": [{"severity": "high", "confidence": confidence}]}])
            self.assertIsNotNone(veto)

    # Slice 2: Dynamic Weights Bounds and Unknown Provider Filtering
    def test_dynamic_weights_clamping_and_filtering(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        weights_file = os.path.join(self.workspace_root, ".ralph", "council_weights.json")
        os.makedirs(os.path.dirname(weights_file), exist_ok=True)
        with open(weights_file, "w") as f:
            json.dump({
                "claude": 10.0,         # Out of bounds (> 0.65)
                "gemini": 0.01,         # Out of bounds (< 0.05)
                "malicious_model": 5.0, # Unknown provider, must be ignored
            }, f)

        weights = council._load_weights(self.workspace_root)
        self.assertNotIn("malicious_model", weights)
        self.assertLessEqual(weights["claude"], 0.65)
        self.assertGreaterEqual(weights["gemini"], 0.05)
        self.assertEqual(weights["codex"], 0.40)  # Default preserved

    # Slice 3: CLI Adapters non-interactive tokens and flags
    def test_cli_adapters_arguments_and_worker_tokens(self) -> None:
        claude = ClaudeAdapter("claude-opus-5", "high")
        codex = CodexAdapter("gpt-5.6-sol", "high")
        agy = AgyAdapter("gemini-3.1-pro", "high")

        claude_args = claude._get_args("Review this plan")
        codex_args = codex._get_args("Review this plan")
        agy_args = agy._get_args("Review this plan")

        self.assertIn("--allow-dangerously-skip-permissions", claude_args)
        self.assertIn("bypassPermissions", claude_args)
        self.assertIn("--no-session-persistence", claude_args)
        self.assertIn("-s", codex_args)
        self.assertIn("workspace-write", codex_args)
        self.assertIn("[WORKER-MODE: NESTED-EXEC]", " ".join(claude_args))
        self.assertIn("[WORKER-MODE: NESTED-EXEC]", " ".join(codex_args))
        self.assertIn("[WORKER-MODE: NESTED-EXEC]", " ".join(agy_args))

    # Slice 4: Privacy Mode Local-Only Enforcement
    def test_local_only_privacy_mode_enforcement(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Sensitive task review",
            workspace_root=self.workspace_root,
            privacy_mode=PrivacyMode.LOCAL_ONLY,
        )
        adapters = council._resolve_adapters(req)
        # Must only return local adjudicator, zero cloud providers
        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].provider_id, "lm-studio")

        # The local adjudicator is a stub with no endpoint wired up: it
        # abstains rather than fabricating an approval, so a local-only
        # review fails closed to UNRESOLVED instead of silently rubber-
        # stamping a sensitive task.
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "UNRESOLVED")

    # Slice 5: Secret Key Resolution with AGY_CALIBRATION_SECRET & Fallback
    def test_hmac_secret_resolution_fallback(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        # Clear env vars if set
        old_agy = os.environ.pop("AGY_CALIBRATION_SECRET", None)
        old_council = os.environ.pop("COUNCIL_REVIEW_SECRET", None)
        try:
            # Test file fallback
            secret = council._resolve_secret(self.workspace_root)
            self.assertEqual(secret, b"test_secret_key_12345")

            # Test AGY_CALIBRATION_SECRET env
            os.environ["AGY_CALIBRATION_SECRET"] = "env_secret_999"
            secret_env = council._resolve_secret(self.workspace_root)
            self.assertEqual(secret_env, b"env_secret_999")
        finally:
            if old_agy:
                os.environ["AGY_CALIBRATION_SECRET"] = old_agy
            if old_council:
                os.environ["COUNCIL_REVIEW_SECRET"] = old_council

    # Slice 6: End-to-End Async Review with Custom Adapters (Unanimous Approval)
    def test_async_review_unanimous_approval(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Feature implementation plan",
            workspace_root=self.workspace_root,
        )
        candidate_hash = hashlib.sha256(req.objective.encode("utf-8")).hexdigest()
        adapters = [
            FakeReviewerAdapter("claude", [{"provider": "claude", "vote": "approve", "confidence": 1.0, "candidate_hash": candidate_hash}] * 3),
            FakeReviewerAdapter("codex", [{"provider": "codex", "vote": "approve", "confidence": 1.0, "candidate_hash": candidate_hash}] * 3),
            FakeReviewerAdapter("gemini", [{"provider": "gemini", "vote": "approve", "confidence": 0.9, "candidate_hash": candidate_hash}] * 3),
        ]
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "UNANIMOUS")
        assert outcome.manifest_path is not None
        self.assertTrue(os.path.isfile(outcome.manifest_path))

    def test_below_quorum_round_2_triggers_round_3_reconciliation(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Feature implementation plan",
            workspace_root=self.workspace_root,
        )
        candidate_hash = hashlib.sha256(req.objective.encode("utf-8")).hexdigest()
        adapters = [
            FakeReviewerAdapter(provider, [
                {"provider": provider, "vote": "approve", "confidence": 1.0},
                {
                    "provider": provider,
                    "vote": "approve",
                    "confidence": 0.5,
                    "candidate_hash": candidate_hash,
                },
                {
                    "provider": provider,
                    "vote": "approve",
                    "confidence": 1.0,
                    "candidate_hash": candidate_hash,
                },
            ])
            for provider in ("claude", "codex", "gemini")
        ]

        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNANIMOUS")
        self.assertEqual([adapter.call_count for adapter in adapters], [3, 3, 3])

    # Slice 7: End-to-End Async Review with Security Veto Halt
    def test_async_review_security_veto_halt(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Feature implementation with vulnerability",
            workspace_root=self.workspace_root,
        )
        adapters = [
            FakeReviewerAdapter("claude", [{"provider": "claude", "vote": "approve", "confidence": 1.0}] * 3),
            FakeReviewerAdapter("codex", [{
                "provider": "codex",
                "vote": "block",
                "confidence": 1.0,
                "findings": [{"id": "CWE-89", "severity": "CRITICAL", "confidence": 1.0, "claim": "SQLi in query"}]
            }] * 3),
            FakeReviewerAdapter("gemini", [{"provider": "gemini", "vote": "approve", "confidence": 1.0}] * 3),
        ]
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))
        self.assertEqual(outcome.status, "SECURITY_HALT")
        self.assertEqual(outcome.unresolved_blockers, 1)

    def test_invalid_adapter_payload_is_recorded_as_abstention(self) -> None:
        class InvalidAdapter:
            provider_id = "invalid-provider"

            async def review(self, _envelope: str, _round: int, _deadline: int) -> Any:
                return ["not", "a", "mapping"]

        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        votes = asyncio.run(council._execute_round([InvalidAdapter()], "proposal", 1, 1))

        self.assertEqual(votes, [{
            "provider": "invalid-provider",
            "vote": "abstain",
            "confidence": 0.0,
            "error": "Invalid adapter response payload",
        }])


_PERSPECTIVE_TEST_OBJECTIVE = "Add a caching layer"
_PERSPECTIVE_TEST_CANDIDATE_HASH = hashlib.sha256(_PERSPECTIVE_TEST_OBJECTIVE.encode("utf-8")).hexdigest()


def _perspective_vote(perspective: str, vote: str, **extra: Any) -> dict[str, Any]:
    """A perspective vote for `_PERSPECTIVE_TEST_OBJECTIVE` — every round reviews
    the same envelope in the by-perspective flow, so `candidate_hash` defaults to
    the hash of that fixed objective, matching what `ConsensusTable.evaluate`'s
    `expected_hash` ratification now requires. Override via `extra` to test
    tamper detection."""
    payload: dict[str, Any] = {
        "provider": perspective,
        "perspective": perspective,
        "vote": vote,
        "candidate_hash": _PERSPECTIVE_TEST_CANDIDATE_HASH,
    }
    payload.update(extra)
    return payload


class CouncilPerspectiveFastPathTests(unittest.TestCase):
    """Spec 0012 / workflow-v2 ticket 07: the 1-shot Council fast path,
    unilateral security veto, 4-perspective concurrency, and stalemate
    escalation, exercised through `ReviewCouncil.review(..., by_perspective=True)`.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = self.temp_dir.name
        cal_dir = os.path.join(self.workspace_root, ".ralph", "cache")
        os.makedirs(cal_dir, exist_ok=True)
        with open(os.path.join(cal_dir, "calibration.key"), "w") as f:
            f.write("test_secret_key_12345")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _perspective_adapters(self, votes_by_perspective: dict[str, list[dict[str, Any]]]) -> list[Any]:
        return [
            FakeReviewerAdapter(perspective, responses)
            for perspective, responses in votes_by_perspective.items()
        ]

    def test_fast_path_terminates_in_round_1_on_weighted_quorum(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            "reviewer_architecture": [_perspective_vote("reviewer_architecture", "approved")],
            "reviewer_risk": [_perspective_vote("reviewer_risk", "approved")],
            "reviewer_maintainability": [_perspective_vote("reviewer_maintainability", "approved")],
            "reviewer_security": [_perspective_vote("reviewer_security", "approved")],
        })
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertIsInstance(outcome, ReviewOutcome)
        self.assertEqual(outcome.status, "UNANIMOUS")
        # 1-shot: every one of the 4 perspective reviewers ran exactly once,
        # in round 1, never reaching round 2 or 3.
        self.assertEqual([a.call_count for a in adapters], [1, 1, 1, 1])

    def test_four_perspectives_are_all_consulted_concurrently(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            perspective: [_perspective_vote(perspective, "approved")]
            for perspective in (
                "reviewer_architecture",
                "reviewer_risk",
                "reviewer_maintainability",
                "reviewer_security",
            )
        })
        votes = asyncio.run(council._execute_round(adapters, req.objective, 1, 45))

        self.assertEqual(len(votes), 4)
        self.assertEqual(
            {vote["perspective"] for vote in votes},
            {"reviewer_architecture", "reviewer_risk", "reviewer_maintainability", "reviewer_security"},
        )
        self.assertEqual([a.call_count for a in adapters], [1, 1, 1, 1])

    def test_unilateral_security_veto_overrides_a_passing_quorum(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            "reviewer_architecture": [_perspective_vote("reviewer_architecture", "approved")],
            "reviewer_risk": [_perspective_vote("reviewer_risk", "approved")],
            "reviewer_maintainability": [_perspective_vote("reviewer_maintainability", "approved")],
            "reviewer_security": [_perspective_vote(
                "reviewer_security",
                "blocked",
                findings=[{"id": "SEC-1", "severity": "critical", "claim": "SQLi", "confidence": 0.95}],
            )],
        })
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "SECURITY_HALT")
        self.assertEqual(outcome.unresolved_blockers, 1)

    def test_round_1_below_quorum_recovers_in_round_2(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            "reviewer_architecture": [
                _perspective_vote("reviewer_architecture", "revise"),
                _perspective_vote("reviewer_architecture", "approved"),
            ],
            "reviewer_risk": [
                _perspective_vote("reviewer_risk", "revise"),
                _perspective_vote("reviewer_risk", "approved"),
            ],
            "reviewer_maintainability": [
                _perspective_vote("reviewer_maintainability", "approved"),
                _perspective_vote("reviewer_maintainability", "approved"),
            ],
            "reviewer_security": [
                _perspective_vote("reviewer_security", "approved"),
                _perspective_vote("reviewer_security", "approved"),
            ],
        })
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNANIMOUS")
        self.assertEqual([a.call_count for a in adapters], [2, 2, 2, 2])

    def test_stalemate_after_round_3_escalates_with_advisory_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "routing-config.json"
            base = json.loads(ROUTING_CONFIG_PATH.read_text())
            base.setdefault("consultation_policy", {})["adjudicators"] = []
            config_path.write_text(json.dumps(base), encoding="utf-8")

            council = ReviewCouncil(config_path)
            req = ReviewRequest(
                objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
            )
            adapters = self._perspective_adapters({
                perspective: [_perspective_vote(perspective, "revise")] * 3
                for perspective in (
                    "reviewer_architecture",
                    "reviewer_risk",
                    "reviewer_maintainability",
                    "reviewer_security",
                )
            })
            outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNRESOLVED")
        self.assertEqual(outcome.unresolved_blockers, 1)
        assert outcome.stalemate_report is not None
        self.assertEqual(outcome.stalemate_report.planner_position, "Add a caching layer")
        self.assertIn("reviewer_security", outcome.stalemate_report.critic_position)
        self.assertEqual([a.call_count for a in adapters], [3, 3, 3, 3])

    def test_stalemate_fails_closed_when_configured_adjudicator_is_offline(self) -> None:
        # The default policy's `lm-studio` adjudicator is a stub with no
        # endpoint wired up: it abstains rather than fabricating an
        # approval. A stalemate routed to it must therefore fail closed to
        # UNRESOLVED/HITL, never resolve to QUALIFIED off a rubber-stamped
        # vote — the fail-closed half of ticket 07's "Adjudicator or HITL"
        # requirement.
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            perspective: [_perspective_vote(perspective, "revise")] * 3
            for perspective in (
                "reviewer_architecture",
                "reviewer_risk",
                "reviewer_maintainability",
                "reviewer_security",
            )
        })
        outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "UNRESOLVED")
        self.assertEqual(outcome.unresolved_blockers, 1)
        assert outcome.stalemate_report is not None

    def test_stalemate_resolved_by_a_genuinely_approving_adjudicator(self) -> None:
        # The counterpart to the fail-closed case above: an adjudicator that
        # actively returns APPROVE still resolves the stalemate to
        # QUALIFIED — the fail-closed fix must not make resolution
        # impossible outright.
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = self._perspective_adapters({
            perspective: [_perspective_vote(perspective, "revise")] * 3
            for perspective in (
                "reviewer_architecture",
                "reviewer_risk",
                "reviewer_maintainability",
                "reviewer_security",
            )
        })
        approving_adjudicator = FakeReviewerAdapter(
            "lm-studio", [{"provider": "lm-studio", "vote": "approve", "confidence": 1.0}]
        )
        with patch("provider_adapters.build_adapter", return_value=approving_adjudicator):
            outcome = asyncio.run(council.review(req, custom_adapters=adapters))

        self.assertEqual(outcome.status, "QUALIFIED")
        assert outcome.stalemate_report is not None

    def test_resolve_adapters_builds_perspective_reviewers_when_requested(self) -> None:
        council = ReviewCouncil(ROUTING_CONFIG_PATH)
        req = ReviewRequest(
            objective="Add a caching layer", workspace_root=self.workspace_root, by_perspective=True
        )
        adapters = council._resolve_adapters(req)

        self.assertEqual(len(adapters), 4)
        self.assertTrue(all(isinstance(adapter, PerspectiveReviewerAdapter) for adapter in adapters))
        self.assertEqual(
            {adapter.provider_id for adapter in adapters},
            {"reviewer_architecture", "reviewer_risk", "reviewer_maintainability", "reviewer_security"},
        )


class ConsultationPolicyConfigTests(unittest.TestCase):
    def test_loads_complete_policy_from_real_routing_config(self) -> None:
        policy = load_consultation_policy(ROUTING_CONFIG_PATH)

        self.assertEqual([provider["id"] for provider in policy["providers"]], ["claude", "codex", "gemini"])
        self.assertEqual(policy["adjudicators"][0]["model"], "qwen3-coder-30b")
        self.assertEqual(policy["deadlines_seconds"], {"round_1": 120, "round_2": 60, "round_3": 60})
        self.assertEqual(policy["weighting"]["quorum_threshold"], 0.60)

    def test_missing_or_partial_policy_uses_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({}), encoding="utf-8")
            self.assertEqual(load_consultation_policy(path), DEFAULT_CONSULTATION_POLICY)

            path.write_text(
                json.dumps({"consultation_policy": {"weighting": {"quorum_threshold": 0.75}}}),
                encoding="utf-8",
            )
            policy = load_consultation_policy(path)

        self.assertEqual(policy["weighting"]["quorum_threshold"], 0.75)
        self.assertEqual(policy["weighting"]["min_weight"], 0.05)
        self.assertEqual(policy["providers"], DEFAULT_CONSULTATION_POLICY["providers"])

    def test_loads_legacy_flat_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy-policy.json"
            path.write_text(json.dumps({
                "providers": [{"id": "local", "model": "test"}],
                "weighting": {"quorum_threshold": 0.75},
            }), encoding="utf-8")
            policy = load_consultation_policy(path)
            self.assertEqual(ReviewCouncil(policy_path=path).policy, policy)
        self.assertEqual(policy["providers"], [{"id": "local", "model": "test"}])
        self.assertEqual(policy["weighting"]["quorum_threshold"], 0.75)

    def test_missing_legacy_policy_path_falls_back_to_routing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "references" / "council-policy.json"
            council = ReviewCouncil(policy_path=missing_path)

        self.assertEqual(council.policy, load_consultation_policy(ROUTING_CONFIG_PATH))

    def test_invalid_policy_values_revert_to_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({"consultation_policy": {
                "providers": {"not": "a list"},
                "adjudicators": ["not a dict"],
                "deadlines_seconds": {"round_1": True, "round_2": -1},
                "weighting": {
                    "quorum_threshold": True,
                    "min_weight": 0.9,
                    "max_weight": 0.2,
                },
                "security_veto": {"security_threshold": 1.2},
            }}), encoding="utf-8")
            policy = load_consultation_policy(path)
        self.assertEqual(policy["providers"], DEFAULT_CONSULTATION_POLICY["providers"])
        self.assertEqual(policy["adjudicators"], DEFAULT_CONSULTATION_POLICY["adjudicators"])
        self.assertEqual(policy["deadlines_seconds"], DEFAULT_CONSULTATION_POLICY["deadlines_seconds"])
        self.assertEqual(policy["weighting"]["quorum_threshold"], 0.60)
        self.assertEqual(policy["weighting"]["min_weight"], 0.05)
        self.assertEqual(policy["weighting"]["max_weight"], 0.65)
        self.assertEqual(policy["security_veto"]["security_threshold"], 0.80)

    def test_malformed_provider_mapping_and_consensus_policy_use_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({"consultation_policy": {
                "providers": [{"id": "codex", "model": "gpt-5.6-sol", "effort_mapping": {"high": 1}}],
                "consensus_policy": ["UNANIMOUS", "MAYBE"],
            }}), encoding="utf-8")
            policy = load_consultation_policy(path)
        self.assertEqual(policy["providers"], DEFAULT_CONSULTATION_POLICY["providers"])
        self.assertEqual(policy["consensus_policy"], DEFAULT_CONSULTATION_POLICY["consensus_policy"])

    def test_duplicate_provider_ids_use_safe_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({"consultation_policy": {
                "providers": [
                    {"id": "codex", "model": "gpt-5.6-sol"},
                    {"id": "codex", "model": "gpt-5.6-terra"},
                ],
            }}), encoding="utf-8")
            policy = load_consultation_policy(path)

        self.assertEqual(policy["providers"], DEFAULT_CONSULTATION_POLICY["providers"])

    def test_deep_schema_validation_reverts_each_invalid_field_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({"consultation_policy": {
                "providers": [{"id": "", "model": "model"}],
                "adjudicators": [{"id": "local"}],
                "deadlines_seconds": {"round_1": False, "round_2": -1, "round_3": 30},
                "weighting": {
                    "initial_weights": {"codex": float("nan")},
                    "min_weight": float("inf"),
                    "max_weight": 0.5,
                    "quorum_threshold": float("nan"),
                },
                "security_veto": {
                    "security_threshold": float("inf"),
                    "veto_severities": ["high", ""],
                },
            }}), encoding="utf-8")
            policy = load_consultation_policy(path)

        self.assertEqual(policy["providers"], DEFAULT_CONSULTATION_POLICY["providers"])
        self.assertEqual(policy["adjudicators"], DEFAULT_CONSULTATION_POLICY["adjudicators"])
        self.assertEqual(policy["deadlines_seconds"]["round_1"], 120)
        self.assertEqual(policy["deadlines_seconds"]["round_2"], 60)
        self.assertEqual(policy["deadlines_seconds"]["round_3"], 30)
        self.assertEqual(policy["weighting"]["initial_weights"], DEFAULT_CONSULTATION_POLICY["weighting"]["initial_weights"])
        self.assertEqual(policy["weighting"]["min_weight"], 0.05)
        self.assertEqual(policy["weighting"]["max_weight"], 0.5)
        self.assertEqual(policy["weighting"]["quorum_threshold"], 0.60)
        self.assertEqual(policy["security_veto"]["security_threshold"], 0.80)
        self.assertEqual(policy["security_veto"]["veto_severities"], ["critical", "high"])

    def test_missing_config_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(FileNotFoundError):
            load_consultation_policy(Path(tmp) / "missing.json")

    def test_malformed_config_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_consultation_policy(path)


class _FakeAsyncProcess:
    """A minimal stand-in for the process handle `invoke_worker_async` awaits."""

    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _runner_returning(process: "_FakeAsyncProcess"):
    async def _runner(*args, **kwargs):
        return process

    return _runner


def _runner_raising(exc: Exception):
    async def _runner(*args, **kwargs):
        raise exc

    return _runner


class CLIReviewerAdapterReviewTests(unittest.TestCase):
    # CLIReviewerAdapter.review() delegates to production_invoker.invoke_worker_async
    # via an injected runner, rather than spawning its own subprocess.

    def test_review_success_returns_parsed_payload_with_provider(self) -> None:
        process = _FakeAsyncProcess(stdout=b'{"vote": "approve", "confidence": 0.9}')
        adapter = CodexAdapter("gpt-5.6-sol", "high", runner=_runner_returning(process))

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "codex")
        self.assertEqual(outcome["vote"], "approve")
        self.assertEqual(outcome["confidence"], 0.9)

    def test_review_nonzero_exit_returns_abstain_with_error(self) -> None:
        process = _FakeAsyncProcess(stdout=b"", stderr=b"boom", returncode=1)
        adapter = ClaudeAdapter("claude-sonnet-5", "high", runner=_runner_returning(process))

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "claude")
        self.assertEqual(outcome["vote"], "abstain")
        self.assertEqual(outcome["confidence"], 0.0)
        self.assertIn("error", outcome)

    def test_review_spawn_failure_returns_abstain_with_error(self) -> None:
        adapter = AgyAdapter(
            "gemini-3.1-pro", "high", runner=_runner_raising(RuntimeError("no such binary"))
        )

        outcome = asyncio.run(adapter.review("proposal text", 1, 30))

        self.assertEqual(outcome["provider"], "gemini")
        self.assertEqual(outcome["vote"], "abstain")
        self.assertEqual(outcome["confidence"], 0.0)
        self.assertIn("error", outcome)

    def test_parse_output_tags_payload_with_provider_id(self) -> None:
        adapter = CLIReviewerAdapter("codex", "gpt-5.6-sol", "high", "codex")

        payload = adapter._parse_output('{"vote": "revise", "confidence": 0.2}')

        self.assertEqual(payload["provider"], "codex")
        self.assertEqual(payload["vote"], "revise")
        self.assertEqual(payload["confidence"], 0.2)

    def test_parse_output_empty_output_uses_defaults(self) -> None:
        adapter = CLIReviewerAdapter("gemini", "gemini-3.1-pro", "high", "agy")

        payload = adapter._parse_output("")

        self.assertEqual(payload["provider"], "gemini")
        self.assertEqual(payload["vote"], "approve")
        self.assertEqual(payload["confidence"], 1.0)


class PerspectiveReviewerAdapterReviewTests(unittest.TestCase):
    def test_review_success_parses_perspective_contract(self) -> None:
        process = _FakeAsyncProcess(
            stdout=(
                b"[PERSPECTIVE: reviewer_security]\n"
                b"rationale here\n"
                b'QUOTE: "reviewed artifact"\n'
                b"VERDICT: APPROVE"
            )
        )
        adapter = PerspectiveReviewerAdapter(
            "reviewer_security", model="claude-opus-5", effort="high", runner=_runner_returning(process)
        )

        outcome = asyncio.run(adapter.review("reviewed artifact", 1, 30))

        self.assertEqual(outcome["provider"], "reviewer_security")
        self.assertEqual(outcome["perspective"], "reviewer_security")
        self.assertEqual(outcome["vote"], "approved")
        self.assertEqual(outcome["verified_quote_count"], 1)

    def test_review_block_verdict_carries_structured_findings(self) -> None:
        process = _FakeAsyncProcess(
            stdout=(
                b"[PERSPECTIVE: reviewer_security]\n"
                b"rationale\n"
                b"FINDING: [id=SEC-1 severity=critical category=injection] SQL injection\n"
                b"VERDICT: BLOCK"
            )
        )
        adapter = PerspectiveReviewerAdapter(
            "reviewer_security", model="claude-opus-5", effort="high", runner=_runner_returning(process)
        )

        outcome = asyncio.run(adapter.review("reviewed artifact", 1, 30))

        self.assertEqual(outcome["vote"], "blocked")
        self.assertEqual(len(outcome["findings"]), 1)
        self.assertEqual(outcome["findings"][0]["id"], "SEC-1")
        self.assertEqual(outcome["findings"][0]["severity"], "critical")

    def test_review_spawn_failure_returns_abstain_with_error(self) -> None:
        adapter = PerspectiveReviewerAdapter(
            "reviewer_architecture",
            model="claude-opus-5",
            effort="high",
            runner=_runner_raising(RuntimeError("no such binary")),
        )

        outcome = asyncio.run(adapter.review("reviewed artifact", 1, 30))

        self.assertEqual(outcome["provider"], "reviewer_architecture")
        self.assertEqual(outcome["vote"], "abstain")
        self.assertIn("error", outcome)


if __name__ == "__main__":
    unittest.main()
