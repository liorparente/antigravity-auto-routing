"""Hermetic unit tests for pure debate orchestration state."""
from __future__ import annotations

import importlib.util
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dialogue_contracts = _load("dialogue_contracts")
debate_orchestrator = _load("debate_orchestrator")


class PanelTopologyTests(unittest.TestCase):
    def test_only_complex_reviews_use_the_panel(self) -> None:
        for occasion in ("plan-review", "code-review"):
            self.assertTrue(debate_orchestrator.is_panel_topology(occasion, " Complex "))
        for occasion in ("ambiguity", "post-mortem"):
            self.assertFalse(debate_orchestrator.is_panel_topology(occasion, "complex"))
        for complexity in ("trivial", "simple", "medium", "unknown"):
            self.assertFalse(debate_orchestrator.is_panel_topology("plan-review", complexity))


class StalemateReportTests(unittest.TestCase):
    def test_pair_report_keeps_one_critic_and_three_options(self) -> None:
        report = debate_orchestrator.build_stalemate_report("planner", "critic")

        self.assertEqual(report.planner_position, "planner")
        self.assertEqual(report.critic_position, "critic")
        self.assertIsNone(report.critic_b_position)
        self.assertEqual([option.id for option in report.options], [1, 2, 3])
        self.assertEqual(report.options[1].label, "Approve Critic Architecture")

    def test_panel_report_preserves_each_critic_and_combines_option_text(self) -> None:
        report = debate_orchestrator.build_stalemate_report("planner", "critic a", "critic b")

        self.assertEqual(report.critic_position, "critic a")
        self.assertEqual(report.critic_b_position, "critic b")
        self.assertEqual(report.options[1].label, "Approve Critics' Architecture")
        self.assertEqual(
            report.options[1].description,
            "Critic A:\ncritic a\n\nCritic B:\ncritic b",
        )


class VerdictEvaluationTests(unittest.TestCase):
    def test_single_critic_verdicts(self) -> None:
        self.assertEqual(debate_orchestrator.evaluate_round_verdicts("APPROVE"), (True, None))
        self.assertEqual(debate_orchestrator.evaluate_round_verdicts("REVISE"), (False, None))
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(None),
            (False, "unparseable verdict: None"),
        )

    def test_canonical_contract_verdicts_are_case_insensitive(self) -> None:
        approved = dialogue_contracts.VerdictContractResult("approved", 1, 0)
        revise = dialogue_contracts.VerdictContractResult("revise", 1, 1)
        malformed = dialogue_contracts.VerdictContractResult("unparseable", 0, 0)

        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(approved.verdict), (True, None)
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(revise.verdict), (False, None)
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts(malformed.verdict),
            (False, "unparseable verdict: unparseable"),
        )

    def test_panel_verdicts_require_both_approvals(self) -> None:
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", "APPROVE", is_panel=True),
            (True, None),
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", "REVISE", is_panel=True),
            (False, None),
        )
        self.assertEqual(
            debate_orchestrator.evaluate_round_verdicts("APPROVE", None, is_panel=True),
            (False, "unparseable verdict: critic_a=APPROVE, critic_b=None"),
        )


class CriticResponsePayloadTests(unittest.TestCase):
    def test_vote_identity_uses_provider_family_for_weight_lookup(self) -> None:
        raw_response = '{"vote": "approve", "confidence": 1.0}'
        cases = (
            ("critic_a", "Claude Opus 5 (Thinking)", "claude"),
            ("critic_a", "Codex 5.6 Sol", "codex"),
            ("critic_b", "Gemini 3.6 Flash (High)", "gemini"),
            ("codex", None, "codex"),
            ("critic", None, "critic"),
        )

        for critic_id, model_name, expected_identity in cases:
            with self.subTest(model_name=model_name, critic_id=critic_id):
                response = debate_orchestrator._critic_response_from_payload(
                    critic_id, raw_response, model_name
                )
                self.assertEqual(response.critic_id, expected_identity)


class DebateStateTests(unittest.TestCase):
    def test_round_and_session_state_construct_with_safe_defaults(self) -> None:
        record = debate_orchestrator.DebateRoundRecord(
            1, "plan", "critic", "critic b", "REVISE", "APPROVE"
        )
        state = debate_orchestrator.DebateSessionState("plan-review", "complex", 3, True)

        self.assertEqual(record.round_index, 1)
        self.assertFalse(record.is_consensus)
        self.assertEqual(state.rounds, ())
        self.assertFalse(state.consensus_reached)
        with self.assertRaises(FrozenInstanceError):
            state.rounds = (record,)

    def test_advance_returns_a_new_pair_or_panel_state(self) -> None:
        pair = debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False)
        revised = debate_orchestrator.advance_debate_state(
            pair, debate_orchestrator.DebateRoundRecord(1, "plan", "critique", critic_a_verdict="REVISE")
        )
        approved = debate_orchestrator.advance_debate_state(
            debate_orchestrator.DebateSessionState("plan-review", "complex", 2, True),
            debate_orchestrator.DebateRoundRecord(1, "panel plan", "a", "b", "APPROVE", "APPROVE"),
        )
        self.assertEqual(pair.rounds, ())
        self.assertIsInstance(revised.rounds, tuple)
        self.assertEqual(len(revised.rounds), 1)
        self.assertTrue(approved.consensus_reached)
        self.assertEqual(approved.final_plan, "panel plan")

    def test_advance_normalizes_record_outcome_fields(self) -> None:
        state = debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False)
        result = debate_orchestrator.advance_debate_state(
            state,
            debate_orchestrator.DebateRoundRecord(
                1, "plan", "critique", critic_a_verdict="APPROVE", is_consensus=False, error="stale"
            ),
        )

        self.assertTrue(result.consensus_reached)
        self.assertTrue(result.rounds[0].is_consensus)
        self.assertIsNone(result.rounds[0].error)

    def test_advance_leaves_terminal_states_unchanged(self) -> None:
        record = debate_orchestrator.DebateRoundRecord(2, "new", "critic", critic_a_verdict="APPROVE")
        terminal_states = (
            debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False, consensus_reached=True),
            debate_orchestrator.DebateSessionState("ambiguity", "medium", 2, False, error="failed"),
            debate_orchestrator.DebateSessionState(
                "ambiguity", "medium", 2, False,
                stalemate_report=debate_orchestrator.build_stalemate_report("plan", "critic"),
            ),
            debate_orchestrator.DebateSessionState(
                "ambiguity", "medium", 1, False,
                rounds=(debate_orchestrator.DebateRoundRecord(1, "plan", "critic"),),
            ),
        )

        for state in terminal_states:
            with self.subTest(state=state):
                self.assertIs(debate_orchestrator.advance_debate_state(state, record), state)


class ProductionOrchestrationTests(unittest.TestCase):
    def test_roster_resolution_prefers_distinct_reachable_families(self) -> None:
        resolution = debate_orchestrator.resolve_roster(
            "pair", is_family_reachable=lambda family: family in {"claude", "codex-gpt"}
        )
        self.assertEqual(resolution.model_for("planner"), "Claude Opus 5 (Thinking)")
        self.assertEqual(resolution.model_for("critic_a"), "Codex 5.6 Sol")
        self.assertFalse(resolution.degraded_independence)

    def test_canary_execution_returns_a_measurement(self) -> None:
        fixture = debate_orchestrator.CANARY_FIXTURES[0]
        response = f'QUOTE: "{fixture.plan_text.splitlines()[0]}"\\n1. flaw found\\nVERDICT: REVISE'
        with tempfile.TemporaryDirectory() as tmp:
            result = debate_orchestrator.run_canary_dialogue(
                "unused", lambda *_args: response, root_dir=Path(tmp), canary_fixture=fixture
            )
        self.assertEqual(result.outcome, "canary")
        self.assertEqual(result.canary_result, "catch")

    def test_budget_degradation_alert_is_emitted_to_stderr(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return 'QUOTE: "Proposed plan"\nVERDICT: APPROVE'

        stderr = io.StringIO()
        cap = debate_orchestrator._load_dialogue_budget_config(
            debate_orchestrator._CONFIG_PATH
        )
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(stderr):
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Plan the implementation",
                invoker,
                root_dir=Path(tmp),
                session_spend_so_far=cap,
            )

        self.assertEqual(result.degradation_rung, 1)
        self.assertEqual(stderr.getvalue(), result.executive_report.budget_alert)

    def test_consecutive_default_path_worker_failures_alert_to_errors_md(self) -> None:
        """debate_orchestrator's default worker path routes isolated process
        execution and failure alerting through its re-exported DebateTransport
        / RecurringFailureNotifier (see `run_advisory_consultation_debate`'s
        `invoke_worker is None` branch), rather than a hand-rolled notifier
        closure. This exercises that wiring directly through the same
        symbols the orchestrator itself instantiates.
        """

        def failing_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="worker unreachable")

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            transport = debate_orchestrator.DebateTransport(
                runner=failing_runner,
                notifier=debate_orchestrator.RecurringFailureNotifier(
                    threshold=debate_orchestrator.ESCALATION_FAILURE_THRESHOLD
                ),
                root_dir=root_dir,
            )
            errors_path = root_dir / "ERRORS.md"

            with self.assertRaises(RuntimeError):
                transport.invoke_worker("Codex 5.6 Sol", "high", "prompt one")
            self.assertFalse(errors_path.exists())

            with self.assertRaises(RuntimeError):
                transport.invoke_worker("Codex 5.6 Sol", "high", "prompt two")

            self.assertTrue(errors_path.exists())
            contents = errors_path.read_text(encoding="utf-8")
            self.assertIn("## Recurring worker failure", contents)
            self.assertIn("Model: `Codex 5.6 Sol`", contents)
            self.assertIn("Consecutive failures: 2", contents)

    def test_default_path_invoke_worker_none_alerts_through_run_advisory_consultation_debate(
        self,
    ) -> None:
        """`run_advisory_consultation_debate`'s own `invoke_worker is None`
        default path (not `DebateTransport` invoked directly, as
        `test_consecutive_default_path_worker_failures_alert_to_errors_md`
        above does) must still wire through the shared transport/notifier
        pair, so a real production outage escalates to `ERRORS.md` from the
        orchestrator entry point production callers actually use. The
        escalation threshold is patched to 1 here: every `invoke_worker`
        failure inside `run_advisory_consultation_debate` returns
        immediately (fail-closed), so a single call can never produce two
        failures for the same model and reach the real threshold of 2.
        """

        def failing_invoke_worker(model: str, effort: str, prompt: str, **_kwargs: object) -> str:
            raise RuntimeError("simulated production_invoker failure")

        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            errors_path = root_dir / "ERRORS.md"
            with patch.object(
                sys.modules["production_invoker"], "invoke_worker", failing_invoke_worker
            ), patch.object(debate_orchestrator, "ESCALATION_FAILURE_THRESHOLD", 1):
                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Plan the implementation", invoke_worker=None, root_dir=root_dir
                )

            self.assertEqual(result.outcome, "worker_error")
            self.assertTrue(errors_path.exists())
            contents = errors_path.read_text(encoding="utf-8")
            self.assertIn("## Recurring worker failure", contents)
            self.assertIn("Consecutive failures: 1", contents)


class SecurityVetoAndManifestTests(unittest.TestCase):
    SECRET = b"ticket-03-test-secret"

    @staticmethod
    def _write_secret(root: Path, secret: bytes = SECRET) -> None:
        key_path = root / ".ralph" / "cache" / "calibration.key"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(secret)

    def _assert_valid_manifest(self, path: str, expected_status: str) -> dict[str, object]:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
        signature = manifest.pop("council_hmac")
        canonical = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.assertEqual(signature, hmac.new(self.SECRET, canonical, hashlib.sha256).hexdigest())
        self.assertEqual(manifest["metadata"]["status"], expected_status)
        return manifest

    @staticmethod
    def _review_response(
        plan: str,
        *,
        vote: str = "approve",
        verdict: str = "APPROVE",
        findings: list[dict[str, object]] | None = None,
        confidence: float = 1.0,
    ) -> str:
        payload = {
            "vote": vote,
            "confidence": confidence,
            "candidate_hash": "candidate-1",
            "findings": findings or [],
        }
        return f'{json.dumps(payload)}\nQUOTE: "{plan}"\nVERDICT: {verdict}'

    def test_dyad_security_veto_halts_without_manifest(self) -> None:
        calls: list[str] = []

        def invoker(_model: str, _effort: str, prompt: str) -> str:
            calls.append(prompt)
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response(
                "Proposed plan",
                vote="block",
                verdict="REVISE",
                findings=[{"id": "SEC-DYAD", "severity": "HIGH", "confidence": 0.95}],
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "implementation_plan.md").write_text("stale", encoding="utf-8")
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Plan safely", invoker, root_dir=root
            )

            self.assertEqual(result.outcome, "security_halt")
            self.assertEqual(len(calls), 2)
            self.assertIsNone(result.manifest_path)
            self.assertEqual(result.security_veto.finding["id"], "SEC-DYAD")
            self.assertFalse((root / "implementation_plan.md").exists())
            self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_panel_security_veto_halts_and_signs_manifest(self) -> None:
        calls: list[str] = []

        def invoker(model: str, _effort: str, prompt: str) -> str:
            calls.append(model)
            if "You are the Planner" in prompt:
                return "Proposed plan"
            if "Gemini" in model:
                return self._review_response(
                    "Proposed plan",
                    vote="block",
                    verdict="REVISE",
                    findings=[{"id": "SEC-PANEL", "severity": "critical", "confidence": 0.9}],
                )
            return self._review_response("Proposed plan")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Review architecture",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
            )

            self.assertEqual(result.outcome, "security_halt")
            self.assertEqual(len(calls), 3)
            self.assertIsNotNone(result.manifest_path)
            self.assertEqual(result.security_veto.provider, "gemini")
            manifest = self._assert_valid_manifest(result.manifest_path, "SECURITY_HALT")
            self.assertEqual(manifest["security_veto"]["finding"]["id"], "SEC-PANEL")

    def test_panel_consensus_manifests_record_genuine_status_and_are_signed(self) -> None:
        cases = (
            ("UNANIMOUS", None, "consensus", "UNANIMOUS"),
            (
                "QUALIFIED",
                {
                    "consensus_policy": [
                        "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"
                    ],
                    "weighting": {
                        "initial_weights": {"codex": 0.8, "gemini": 0.2},
                        "quorum_threshold": 0.7,
                    },
                },
                "consensus",
                "QUALIFIED",
            ),
            (
                "UNRESOLVED",
                {
                    "consensus_policy": [
                        "UNANIMOUS", "QUALIFIED", "MATERIAL_DISAGREEMENT", "INCOMPLETE", "UNRESOLVED"
                    ],
                    "weighting": {
                        "initial_weights": {"codex": 0.8, "gemini": 0.2},
                        "quorum_threshold": 0.7,
                    },
                },
                "stalemate",
                "STALEMATE",
            ),
        )
        for panel_status, policy, expected_outcome, manifest_status in cases:
            with self.subTest(status=panel_status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self._write_secret(root)

                def invoker(model: str, _effort: str, prompt: str) -> str:
                    if "You are the Planner" in prompt:
                        return "Proposed plan"
                    if panel_status == "UNRESOLVED":
                        return self._review_response(
                            "Proposed plan", vote="revise", verdict="APPROVE", confidence=0.0
                        )
                    if panel_status == "QUALIFIED" and "Gemini" in model:
                        return self._review_response(
                            "Proposed plan", vote="revise", verdict="APPROVE", confidence=0.0
                        )
                    return self._review_response("Proposed plan")

                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Review architecture",
                    invoker,
                    root_dir=root,
                    occasion="plan-review",
                    complexity="complex",
                    consultation_policy=policy,
                )

                self.assertEqual(result.outcome, expected_outcome)
                self._assert_valid_manifest(result.manifest_path, manifest_status)

    def test_panel_stalemate_manifest_is_signed(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response("Proposed plan", vote="revise", verdict="REVISE")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_secret(root)
            result = debate_orchestrator.run_advisory_consultation_debate(
                "Review architecture",
                invoker,
                root_dir=root,
                occasion="plan-review",
                complexity="complex",
                max_rounds=1,
            )
            self.assertEqual(result.outcome, "stalemate")
            self._assert_valid_manifest(result.manifest_path, "STALEMATE")

    def test_normal_dyad_outcomes_do_not_write_manifests(self) -> None:
        for verdict, expected_outcome in (("APPROVE", "consensus"), ("REVISE", "stalemate")):
            with self.subTest(outcome=expected_outcome), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def invoker(_model: str, _effort: str, prompt: str) -> str:
                    if "You are the Planner" in prompt:
                        return "Proposed plan"
                    return self._review_response(
                        "Proposed plan", vote=verdict.casefold(), verdict=verdict
                    )

                result = debate_orchestrator.run_advisory_consultation_debate(
                    "Plan safely", invoker, root_dir=root, max_rounds=1
                )
                self.assertEqual(result.outcome, expected_outcome)
                self.assertIsNone(result.manifest_path)
                self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_missing_panel_secret_fails_closed(self) -> None:
        def invoker(_model: str, _effort: str, prompt: str) -> str:
            if "You are the Planner" in prompt:
                return "Proposed plan"
            return self._review_response("Proposed plan")

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"AGY_CALIBRATION_SECRET": "", "COUNCIL_REVIEW_SECRET": ""},
        ):
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "secret resolution failed"):
                debate_orchestrator.run_advisory_consultation_debate(
                    "Review architecture",
                    invoker,
                    root_dir=root,
                    occasion="plan-review",
                    complexity="complex",
                )
            self.assertFalse((root / "implementation_plan.md").exists())
            self.assertEqual(list((root / ".ralph").glob("council-manifest-*.json")), [])

    def test_facade_and_orchestrator_signatures_match(self) -> None:
        import inspect

        advisory_consultation = _load("advisory_consultation")

        for symbol in (
            "run_advisory_consultation_debate",
            "run_debate_loop",
            "run_canary_dialogue",
            "run_post_mortem_loop",
            "dispatch_post_mortem_consultation",
        ):
            facade_fn = getattr(advisory_consultation, symbol)
            orch_fn = getattr(debate_orchestrator, symbol)
            self.assertEqual(
                inspect.signature(facade_fn),
                inspect.signature(orch_fn),
                f"Signature mismatch on {symbol}",
            )


if __name__ == "__main__":
    unittest.main()
