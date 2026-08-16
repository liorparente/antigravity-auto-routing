#!/usr/bin/env python3
"""Unit tests for Risk-Tiered Application (Spec 0004 Ticket 20).

Verifies the four risk tiers, acceptance gate evaluation, pending proposal
lifecycle for briefs, protocol inaccessibility by construction, and idempotency /
no-op handling when adopting unchanged documents.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import learned_state
from learned_state import DocumentChange
from learning_scoreboard import MetricChange, MetricValue, ScoreboardComparison
from risk_tiered_application import (
    PendingProposal,
    apply_memory_lesson,
    apply_routing_table_update,
    approve_pending_proposal,
    read_pending_proposals,
    reject_pending_proposal,
    revert_attributable_regression,
    submit_brief_proposal,
)

_NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 8, 15, 13, 0, 0, tzinfo=timezone.utc)


def _comparison_with_regression(name: str = "mean_rework_per_task") -> ScoreboardComparison:
    baseline = MetricValue(name=name, direction="lower_is_better", value=1.0, sample_size=5)
    current = MetricValue(name=name, direction="lower_is_better", value=5.0, sample_size=5)
    change = MetricChange(
        name=name, direction="lower_is_better", status="regressed", baseline=baseline, current=current
    )
    return ScoreboardComparison(changes=(change,))


def _comparison_without_regression(name: str = "mean_rework_per_task") -> ScoreboardComparison:
    baseline = MetricValue(name=name, direction="lower_is_better", value=1.0, sample_size=5)
    current = MetricValue(name=name, direction="lower_is_better", value=1.0, sample_size=5)
    change = MetricChange(
        name=name, direction="lower_is_better", status="held", baseline=baseline, current=current
    )
    return ScoreboardComparison(changes=(change,))


class Tier1MemoryLessonTests(unittest.TestCase):
    """Tier 1: Memory lessons auto-apply without requiring an acceptance gate."""

    def test_memory_lesson_auto_applies_and_creates_version_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = apply_memory_lesson(
                "Lesson: Always test seams directly.",
                root_dir=root,
                now=_NOW,
                change_id="lesson-01",
            )
            self.assertEqual(outcome.document, "memory")
            self.assertEqual(outcome.status, "applied")
            self.assertTrue(outcome.applied)
            self.assertIsNotNone(outcome.version_entry)
            assert outcome.version_entry is not None
            self.assertEqual(outcome.version_entry.version, 1)
            self.assertEqual(outcome.version_entry.change_id, "lesson-01")

            # Verify on-disk learned state
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "Lesson: Always test seams directly.")

    def test_memory_lesson_requires_timezone_aware_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive_now = datetime(2026, 8, 15, 12, 0, 0)  # noqa: DTZ001 - the value under test
            with self.assertRaises(ValueError):
                apply_memory_lesson("Lesson text", root_dir=root, now=naive_now)


class Tier2RoutingTableUpdateTests(unittest.TestCase):
    """Tier 2: Routing table updates auto-apply only after clearing the acceptance gate."""

    def test_routing_table_update_applies_when_gate_accepts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # High score runner that clears threshold (default threshold is 0.85)
            runner = lambda: 0.95
            outcome = apply_routing_table_update(
                '{"version": "v2", "routes": []}',
                root_dir=root,
                now=_NOW,
                runner=runner,
                change_id="route-update-01",
            )
            self.assertEqual(outcome.document, "routing_table")
            self.assertEqual(outcome.status, "applied")
            self.assertTrue(outcome.applied)
            self.assertIsNotNone(outcome.gate_decision)
            assert outcome.gate_decision is not None
            self.assertTrue(outcome.gate_decision.accepted)
            self.assertIsNotNone(outcome.version_entry)
            assert outcome.version_entry is not None
            self.assertEqual(outcome.version_entry.version, 1)

            # Verify on-disk
            current = learned_state.read_current(root)
            self.assertEqual(current.get("routing_table"), '{"version": "v2", "routes": []}')

    def test_routing_table_update_rejected_outright_when_gate_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Low score runner that fails threshold
            runner = lambda: 0.40
            outcome = apply_routing_table_update(
                '{"version": "v2_bad", "routes": []}',
                root_dir=root,
                now=_NOW,
                runner=runner,
                change_id="route-update-bad",
            )
            self.assertEqual(outcome.document, "routing_table")
            self.assertEqual(outcome.status, "rejected")
            self.assertFalse(outcome.applied)
            self.assertIsNotNone(outcome.gate_decision)
            assert outcome.gate_decision is not None
            self.assertFalse(outcome.gate_decision.accepted)
            self.assertIsNone(outcome.version_entry)

            # Learned state must remain empty
            self.assertEqual(learned_state.read_current(root), {})
            self.assertEqual(learned_state.read_history(root), ())

    def test_routing_table_update_rejected_when_runner_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def failing_runner() -> float:
                raise RuntimeError("Runner connection failed")

            outcome = apply_routing_table_update(
                '{"version": "v2_failing"}',
                root_dir=root,
                now=_NOW,
                runner=failing_runner,
            )
            self.assertEqual(outcome.status, "rejected")
            self.assertFalse(outcome.applied)
            assert outcome.gate_decision is not None
            self.assertFalse(outcome.gate_decision.accepted)
            self.assertEqual(learned_state.read_history(root), ())


class Tier3BriefProposalTests(unittest.TestCase):
    """Tier 3: Brief diffs are held as pending proposals until explicit human approval."""

    def test_brief_proposal_held_pending_and_not_adopted_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = submit_brief_proposal(
                "# Context Brief v2\nAlways prefer deep modules.",
                root_dir=root,
                now=_NOW,
                proposal_id="brief-prop-01",
            )
            self.assertEqual(outcome.document, "briefs")
            self.assertEqual(outcome.status, "pending")
            self.assertFalse(outcome.applied)
            self.assertEqual(outcome.proposal_id, "brief-prop-01")
            self.assertIsNone(outcome.version_entry)

            # Check that learned_state was not modified
            self.assertEqual(learned_state.read_current(root), {})
            self.assertEqual(learned_state.read_history(root), ())

            # Check pending store
            pending = read_pending_proposals(root)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].proposal_id, "brief-prop-01")
            self.assertEqual(pending[0].document, "briefs")
            self.assertEqual(pending[0].content, "# Context Brief v2\nAlways prefer deep modules.")

    def test_approve_pending_proposal_adopts_into_learned_state_and_removes_from_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submit_brief_proposal(
                "# Context Brief v2",
                root_dir=root,
                now=_NOW,
                proposal_id="brief-prop-01",
            )
            self.assertEqual(len(read_pending_proposals(root)), 1)

            # Explicit human approval
            outcome = approve_pending_proposal(
                "brief-prop-01",
                root_dir=root,
                now=_LATER,
                change_id="human-approved-brief-01",
            )
            self.assertEqual(outcome.document, "briefs")
            self.assertEqual(outcome.status, "applied")
            self.assertTrue(outcome.applied)
            self.assertIsNotNone(outcome.version_entry)
            assert outcome.version_entry is not None
            self.assertEqual(outcome.version_entry.version, 1)

            # Pending store must now be empty
            self.assertEqual(read_pending_proposals(root), ())

            # Document must be present in current learned state
            current = learned_state.read_current(root)
            self.assertEqual(current.get("briefs"), "# Context Brief v2")

    def test_approve_pending_proposal_uses_proposal_id_as_change_id_when_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submit_brief_proposal(
                "# Context Brief v3",
                root_dir=root,
                now=_NOW,
                proposal_id="brief-01",
            )

            outcome = approve_pending_proposal("brief-01", root_dir=root, now=_LATER)
            self.assertEqual(outcome.change_id, "brief-01")

    def test_reject_pending_proposal_removes_from_store_without_adopting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submit_brief_proposal(
                "# Rejected Brief",
                root_dir=root,
                now=_NOW,
                proposal_id="brief-prop-rejected",
            )
            self.assertEqual(len(read_pending_proposals(root)), 1)

            # Reject proposal
            reject_pending_proposal("brief-prop-rejected", root_dir=root)
            self.assertEqual(read_pending_proposals(root), ())
            self.assertEqual(learned_state.read_current(root), {})
            self.assertEqual(learned_state.read_history(root), ())

    def test_brief_proposal_never_approved_remains_pending_across_other_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            submit_brief_proposal(
                "# Unapproved Brief",
                root_dir=root,
                now=_NOW,
                proposal_id="brief-unapproved",
            )

            # Other tier 1 action happens
            apply_memory_lesson("Lesson 1", root_dir=root, now=_NOW)

            # Brief is still only pending, and not in learned state
            current = learned_state.read_current(root)
            self.assertIn("memory", current)
            self.assertNotIn("briefs", current)
            self.assertEqual(len(read_pending_proposals(root)), 1)

    def test_approve_non_existent_proposal_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                approve_pending_proposal("non-existent-prop", root_dir=root, now=_NOW)


class Tier4ProtocolInaccessibilityTests(unittest.TestCase):
    """Tier 4: The protocol is unreachable by construction — no code path writes it."""

    def test_protocol_is_not_a_valid_learned_document(self) -> None:
        self.assertNotIn("protocol", learned_state.LEARNED_DOCUMENTS)
        self.assertNotIn("protocol.md", learned_state.LEARNED_DOCUMENTS)

        # Attempting to construct DocumentChange for protocol raises ValueError by construction
        with self.assertRaises(ValueError):
            DocumentChange(document="protocol", content="new protocol")  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            DocumentChange(document="protocol.md", content="new protocol")  # type: ignore[arg-type]

    def test_pending_proposal_refuses_protocol_document(self) -> None:
        with self.assertRaises(ValueError):
            PendingProposal(
                proposal_id="prop-proto",
                document="protocol",  # type: ignore[arg-type]
                content="new protocol",
                timestamp="2026-08-15T12:00:00Z",
            )


class IdempotencyNoOpTests(unittest.TestCase):
    """Idempotency: Adopting unchanged content produces a successful no_op rather than a failure."""

    def test_adopting_identical_memory_lesson_returns_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # First adoption
            first = apply_memory_lesson("Lesson 1", root_dir=root, now=_NOW)
            self.assertEqual(first.status, "applied")
            assert first.version_entry is not None
            self.assertEqual(first.version_entry.version, 1)

            # Second identical adoption
            second = apply_memory_lesson("Lesson 1", root_dir=root, now=_LATER)
            self.assertEqual(second.status, "no_op")
            self.assertTrue(second.applied)
            assert second.version_entry is not None
            self.assertEqual(second.version_entry.version, 1)
            self.assertIn("identical", second.reason or "")

            # History must still contain only 1 version
            history = learned_state.read_history(root)
            self.assertEqual(len(history), 1)

    def test_adopting_identical_routing_table_returns_no_op_after_clearing_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = lambda: 0.95
            first = apply_routing_table_update(
                '{"routes": []}',
                root_dir=root,
                now=_NOW,
                runner=runner,
            )
            self.assertEqual(first.status, "applied")
            assert first.version_entry is not None
            self.assertEqual(first.version_entry.version, 1)

            # Second identical update
            second = apply_routing_table_update(
                '{"routes": []}',
                root_dir=root,
                now=_LATER,
                runner=runner,
            )
            self.assertEqual(second.status, "no_op")
            self.assertTrue(second.applied)
            assert second.gate_decision is not None
            self.assertTrue(second.gate_decision.accepted)
            assert second.version_entry is not None
            self.assertEqual(second.version_entry.version, 1)
            self.assertEqual(len(learned_state.read_history(root)), 1)

    def test_approving_identical_brief_proposal_returns_no_op_and_clears_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Seed briefs v1
            first = learned_state.adopt(
                [DocumentChange(document="briefs", content="Brief v1")],
                root_dir=root,
                now=_NOW,
            )
            self.assertEqual(first.version, 1)

            # Propose identical Brief v1
            submit_brief_proposal("Brief v1", root_dir=root, now=_NOW, proposal_id="prop-same")
            self.assertEqual(len(read_pending_proposals(root)), 1)

            # Approve it
            outcome = approve_pending_proposal("prop-same", root_dir=root, now=_LATER)
            self.assertEqual(outcome.status, "no_op")
            self.assertTrue(outcome.applied)
            assert outcome.version_entry is not None
            self.assertEqual(outcome.version_entry.version, 1)
            self.assertEqual(read_pending_proposals(root), ())
            self.assertEqual(len(learned_state.read_history(root)), 1)


class AutoRevertOnRegressionTests(unittest.TestCase):
    """Auto-revert: a scoreboard regression rolls back the attributable live adoption."""

    def test_attributable_regression_triggers_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson v1", root_dir=root, now=_NOW, change_id="adopt-1")
            apply_memory_lesson("Lesson v2", root_dir=root, now=_LATER, change_id="adopt-2")

            outcome = revert_attributable_regression(
                _comparison_with_regression(),
                root_dir=root,
                now=_LATER,
                window_days=7,
                change_id="revert-1",
            )

            self.assertEqual(outcome.status, "reverted")
            self.assertEqual(outcome.regressed_metrics, ("mean_rework_per_task",))
            self.assertEqual(outcome.reverted_change_id, "adopt-2")
            self.assertIsNotNone(outcome.version_entry)
            assert outcome.version_entry is not None
            self.assertEqual(outcome.version_entry.kind, "rollback")

            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "Lesson v1")

    def test_unattributable_regression_when_no_adoptions_in_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson v1", root_dir=root, now=_NOW, change_id="adopt-1")

            far_future = _NOW + timedelta(days=30)
            outcome = revert_attributable_regression(
                _comparison_with_regression(),
                root_dir=root,
                now=far_future,
                window_days=7,
            )

            self.assertEqual(outcome.status, "unattributable")
            self.assertEqual(outcome.regressed_metrics, ("mean_rework_per_task",))
            self.assertIsNone(outcome.reverted_change_id)
            self.assertIsNone(outcome.version_entry)

            # No rollback was attempted: history is unchanged.
            self.assertEqual(len(learned_state.read_history(root)), 1)

    def test_no_regression_returns_no_regression_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = revert_attributable_regression(
                _comparison_without_regression(),
                root_dir=root,
                now=_NOW,
            )

            self.assertEqual(outcome.status, "no_regression")
            self.assertEqual(outcome.regressed_metrics, ())
            self.assertIn("no scoreboard metric regressed", outcome.reason or "")
            self.assertEqual(learned_state.read_history(root), ())

    def test_first_adoption_regression_handled_as_unrevertable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson v1", root_dir=root, now=_NOW, change_id="adopt-1")

            outcome = revert_attributable_regression(
                _comparison_with_regression(),
                root_dir=root,
                now=_NOW,
                window_days=7,
            )

            self.assertEqual(outcome.status, "unrevertable")
            self.assertEqual(outcome.regressed_metrics, ("mean_rework_per_task",))
            self.assertIn("first adoption", outcome.reason or "")
            self.assertIsNone(outcome.reverted_change_id)
            self.assertIsNone(outcome.version_entry)

            # The refused rollback wrote nothing new.
            self.assertEqual(len(learned_state.read_history(root)), 1)

    def test_revert_requires_timezone_aware_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive_now = datetime(2026, 8, 15, 12, 0, 0)  # noqa: DTZ001 - the value under test
            with self.assertRaises(ValueError):
                revert_attributable_regression(
                    _comparison_with_regression(),
                    root_dir=root,
                    now=naive_now,
                )


if __name__ == "__main__":
    unittest.main()
