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
from unittest.mock import patch

if __package__:
    from . import learned_state, learning_journal, risk_tiered_application
    from .learned_state import DocumentChange
    from .learning_scoreboard import MetricChange, MetricValue, ScoreboardComparison
    from .risk_tiered_application import (
        DEFAULT_MAX_MEMORY_LESSONS,
        PendingProposal,
        apply_memory_lesson,
        apply_routing_table_update,
        approve_pending_proposal,
        read_pending_proposals,
        reject_pending_proposal,
        revert_attributable_regression,
        submit_brief_proposal,
    )
else:
    import learned_state  # type: ignore[no-redef]
    import learning_journal  # type: ignore[no-redef]
    import risk_tiered_application  # type: ignore[no-redef]
    from learned_state import DocumentChange  # type: ignore[no-redef]
    from learning_scoreboard import (  # type: ignore[no-redef]
        MetricChange,
        MetricValue,
        ScoreboardComparison,
    )
    from risk_tiered_application import (  # type: ignore[no-redef]
        DEFAULT_MAX_MEMORY_LESSONS,
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
_LATEST = datetime(2026, 8, 15, 14, 0, 0, tzinfo=timezone.utc)


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
    """Tier 1: Memory lessons auto-apply without requiring an acceptance gate.

    ADR 0010 (Ticket 33): `apply_memory_lesson` accumulates rather than
    replaces — every adopted candidate is canonical-wrapped (`"- "`-prefixed)
    regardless of whether the caller's own `content` already looked that way.
    """

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

            # Verify on-disk learned state. Canonical-wrapped: the merge
            # candidate is always serialized in canonical bullet form, even
            # for a first, single, un-bulleted lesson.
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson: Always test seams directly.")

    def test_memory_lesson_requires_timezone_aware_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            naive_now = datetime(2026, 8, 15, 12, 0, 0)  # noqa: DTZ001 - the value under test
            with self.assertRaises(ValueError):
                apply_memory_lesson("Lesson text", root_dir=root, now=naive_now)

    def test_second_call_accumulates_with_first_rather_than_replacing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW, change_id="lesson-01")
            second = apply_memory_lesson(
                "Lesson B", root_dir=root, now=_LATER, change_id="lesson-02"
            )

            self.assertEqual(second.status, "applied")
            self.assertIn("merged 1 new lesson(s)", second.reason or "")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson B")

    def test_reapplying_identical_lesson_text_is_deduplicated_and_returns_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW)
            apply_memory_lesson("Lesson B", root_dir=root, now=_LATER)

            third = apply_memory_lesson("Lesson A", root_dir=root, now=_LATEST)

            self.assertEqual(third.status, "no_op")
            self.assertTrue(third.applied)
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson B")
            self.assertEqual(len(learned_state.read_history(root)), 2)

    def test_duplicate_only_input_returns_no_op_without_writing_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("- Lesson A\n- Lesson B", root_dir=root, now=_NOW)

            outcome = apply_memory_lesson("- Lesson B\n- Lesson A", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "no_op")
            self.assertEqual(len(learned_state.read_history(root)), 1)

    def test_exceeding_max_lessons_prunes_oldest_first_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(DEFAULT_MAX_MEMORY_LESSONS + 5):
                apply_memory_lesson(f"Lesson {i}", root_dir=root, now=_NOW)

            current = learned_state.read_current(root)
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(len(entries), DEFAULT_MAX_MEMORY_LESSONS)
            # The five oldest (0-4) were evicted; the most recent survive.
            self.assertEqual(entries[0], "Lesson 5")
            self.assertEqual(entries[-1], f"Lesson {DEFAULT_MAX_MEMORY_LESSONS + 4}")

            last_outcome = apply_memory_lesson(
                f"Lesson {DEFAULT_MAX_MEMORY_LESSONS + 5}", root_dir=root, now=_LATER
            )
            self.assertIn(
                f"pruned 1 oldest to stay within max_lessons={DEFAULT_MAX_MEMORY_LESSONS}",
                last_outcome.reason or "",
            )

    def test_legacy_non_bulleted_memory_content_is_preserved_as_one_entry_not_split(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_content = "Legacy multi-line lesson\nwith no bullets at all"
            learned_state.adopt(
                [DocumentChange(document="memory", content=legacy_content)],
                root_dir=root,
                now=_NOW,
            )

            outcome = apply_memory_lesson("New lesson", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0], legacy_content)
            self.assertEqual(entries[1], "New lesson")

    def test_legacy_entry_survives_two_subsequent_merges_as_one_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_content = "Legacy multi-line lesson\nwith no bullets at all"
            learned_state.adopt(
                [DocumentChange(document="memory", content=legacy_content)],
                root_dir=root,
                now=_NOW,
            )
            apply_memory_lesson("New lesson", root_dir=root, now=_LATER)
            apply_memory_lesson("Another lesson", root_dir=root, now=_LATEST)

            current = learned_state.read_current(root)
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(len(entries), 3)
            self.assertEqual(entries[0], legacy_content)

    def test_blank_content_raises_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson("   \n  ", root_dir=root, now=_NOW)

            self.assertEqual(learned_state.read_history(root), ())

    def test_non_string_content_raises_value_error_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson(123, root_dir=root, now=_NOW)  # type: ignore[arg-type]

            self.assertEqual(learned_state.read_history(root), ())

    def test_non_string_reject_if_candidate_digest_raises_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson(
                    "Lesson A",
                    root_dir=root,
                    now=_NOW,
                    reject_if_candidate_digest=123,  # type: ignore[arg-type]
                )

            self.assertEqual(learned_state.read_history(root), ())

    def test_invalid_hex_reject_if_candidate_digest_raises_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson(
                    "Lesson A",
                    root_dir=root,
                    now=_NOW,
                    reject_if_candidate_digest="g" * 64,
                )

            self.assertEqual(learned_state.read_history(root), ())

    def test_short_reject_if_candidate_digest_raises_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson(
                    "Lesson A",
                    root_dir=root,
                    now=_NOW,
                    reject_if_candidate_digest="a" * 63,
                )

            self.assertEqual(learned_state.read_history(root), ())

    def test_uppercase_reject_if_candidate_digest_raises_and_writes_no_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                apply_memory_lesson(
                    "Lesson A",
                    root_dir=root,
                    now=_NOW,
                    reject_if_candidate_digest="A" * 64,
                )

            self.assertEqual(learned_state.read_history(root), ())

    def test_crlf_and_trailing_newline_input_normalizes_to_lf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = apply_memory_lesson(
                "- Lesson A\r\n- Lesson B\r\n", root_dir=root, now=_NOW
            )

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson B")
            self.assertNotIn("\r", current.get("memory", ""))

    def test_multiline_continuation_entry_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            multiline = "- First line of a lesson\n  continued on a second line"
            outcome = apply_memory_lesson(multiline, root_dir=root, now=_NOW)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), multiline)

            # A second, identical multiline entry dedupes to a no-op.
            second = apply_memory_lesson(multiline, root_dir=root, now=_LATER)
            self.assertEqual(second.status, "no_op")

    def test_malformed_bulleted_mixture_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as ctx:
                apply_memory_lesson(
                    "- Lesson A\nunindented continuation line", root_dir=root, now=_NOW
                )
            self.assertIn("malformed", str(ctx.exception))
            self.assertEqual(learned_state.read_history(root), ())

    def test_case_sensitive_dedup_treats_differently_cased_lessons_as_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW)
            outcome = apply_memory_lesson("lesson a", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- lesson a")

    def test_rollback_after_accumulated_adopt_restores_exact_prior_accumulated_state(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW, change_id="adopt-1")
            apply_memory_lesson("Lesson B", root_dir=root, now=_LATER, change_id="adopt-2")

            learned_state.roll_back(root_dir=root, now=_LATEST, change_id="rollback-1")

            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A")

    def test_concurrent_apply_memory_lesson_calls_both_survive(self) -> None:
        """A second caller's `apply_memory_lesson` call lands entirely
        between this call's read and its own `adopt` — simulated in-process
        (deterministic, no real threads/processes) by making the first
        `read_current` call also perform the "concurrent" write before this
        call's own retry loop ever sees it. Both lessons must survive, via
        the CAS retry rather than one silently overwriting the other.

        Patches `risk_tiered_application.learned_state.read_current` — the
        exact attribute `apply_memory_lesson`'s own code resolves through —
        rather than this test file's own `learned_state` import. `test_routing.py`
        still reloads `learned_state` via `importlib.util.spec_from_file_location`
        at import time, which under `unittest discover`'s whole-suite collection
        can leave this test file's `learned_state` name bound to a different
        module object than the one `risk_tiered_application.py` itself already
        imported — patching that copy would silently no-op.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rta_learned_state = risk_tiered_application.learned_state
            real_read_current = rta_learned_state.read_current
            call_count = {"n": 0}

            def racing_read_current(root_dir: Path):
                call_count["n"] += 1
                result = real_read_current(root_dir)
                if call_count["n"] == 1:
                    apply_memory_lesson("Lesson B", root_dir=root_dir, now=_LATER)
                return result

            with patch.object(rta_learned_state, "read_current", side_effect=racing_read_current):
                outcome = apply_memory_lesson("Lesson A", root_dir=root, now=_NOW)

            self.assertEqual(outcome.status, "applied")
            self.assertGreater(call_count["n"], 1, "the race must have forced at least one retry")
            current = rta_learned_state.read_current(root)
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(set(entries), {"Lesson A", "Lesson B"})

    def test_concurrent_rollback_cannot_be_overwritten_by_a_stale_apply(self) -> None:
        """A rollback landing inside this call's read-merge-write window
        must not be silently overwritten by a stale-based adopt — the CAS
        retry must pick up the post-rollback state on its next iteration.
        See the previous test's docstring for why this patches
        `risk_tiered_application.learned_state` rather than this test
        file's own `learned_state` import.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW, change_id="adopt-1")
            apply_memory_lesson("Lesson B", root_dir=root, now=_LATER, change_id="adopt-2")

            rta_learned_state = risk_tiered_application.learned_state
            real_read_current = rta_learned_state.read_current
            call_count = {"n": 0}

            def racing_read_current(root_dir: Path):
                call_count["n"] += 1
                result = real_read_current(root_dir)
                if call_count["n"] == 1:
                    rta_learned_state.roll_back(
                        root_dir=root_dir, now=_LATEST, change_id="rollback-1"
                    )
                return result

            with patch.object(rta_learned_state, "read_current", side_effect=racing_read_current):
                outcome = apply_memory_lesson("Lesson C", root_dir=root, now=_LATEST)

            self.assertEqual(outcome.status, "applied")
            self.assertGreater(call_count["n"], 1, "the race must have forced at least one retry")
            current = rta_learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson C")

    def test_concurrent_modification_during_anti_flapping_check_triggers_retry(self) -> None:
        """The anti-flapping recheck must not reject on a candidate digest
        computed against state that is already stale. A concurrent writer
        lands between this iteration's initial read (which produced a
        candidate matching `reject_if_candidate_digest`) and the recheck
        read just before returning `rejected` — the recheck must see the
        changed `"memory"` value and retry instead, so the reject is never
        issued against state nobody can act on anymore. See
        `test_concurrent_apply_memory_lesson_calls_both_survive`'s docstring
        for why this patches `risk_tiered_application.learned_state`
        directly rather than this test file's own `learned_state` import.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            apply_memory_lesson("Lesson A", root_dir=root, now=_NOW, change_id="adopt-1")
            reverted_before_digest = risk_tiered_application._digest("- Lesson A\n- Lesson B")

            rta_learned_state = risk_tiered_application.learned_state
            real_read_current = rta_learned_state.read_current
            call_count = {"n": 0}

            def racing_read_current(root_dir: Path):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    # Lands inside the anti-flapping recheck: a concurrent
                    # writer changes "memory" before this call returns.
                    apply_memory_lesson("Lesson C", root_dir=root_dir, now=_LATER)
                return real_read_current(root_dir)

            with patch.object(rta_learned_state, "read_current", side_effect=racing_read_current):
                outcome = apply_memory_lesson(
                    "Lesson B",
                    root_dir=root,
                    now=_LATEST,
                    reject_if_candidate_digest=reverted_before_digest,
                )

            # The concurrent write invalidated the reject decision: the
            # retried iteration merges against post-race state, whose
            # candidate no longer matches `reject_if_candidate_digest`.
            self.assertEqual(outcome.status, "applied")
            self.assertTrue(outcome.applied)
            current = rta_learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A\n- Lesson C\n- Lesson B")


class ParseMemoryDocumentLegacyLineEndingTests(unittest.TestCase):
    """`_parse_memory_document`'s legacy (no `"- "`-prefixed line) branch must
    normalize `\\r`/`\\r\\n` to `\\n` via `str.splitlines()` + `"\\n".join`,
    not return `text` verbatim — a verbatim return lets an embedded `\\r`
    survive into the parsed entry and, later, into whatever re-serializes it.
    """

    def test_legacy_content_with_cr_line_endings_normalizes_embedded_cr(self) -> None:
        entries = risk_tiered_application._parse_memory_document(
            "Legacy line one\rLegacy line two"
        )
        self.assertEqual(entries, ("Legacy line one\nLegacy line two",))
        self.assertNotIn("\r", entries[0])

    def test_legacy_content_with_crlf_line_endings_normalizes_embedded_crlf(self) -> None:
        entries = risk_tiered_application._parse_memory_document(
            "Legacy line one\r\nLegacy line two\r\n"
        )
        self.assertEqual(entries, ("Legacy line one\nLegacy line two",))
        self.assertNotIn("\r", entries[0])

    def test_legacy_cr_content_round_trips_through_apply_memory_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_content = "Legacy line one\rLegacy line two"
            learned_state.adopt(
                [DocumentChange(document="memory", content=legacy_content)],
                root_dir=root,
                now=_NOW,
            )

            outcome = apply_memory_lesson("New lesson", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertNotIn("\r", current.get("memory", ""))
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(entries[0], "Legacy line one\nLegacy line two")
            self.assertEqual(entries[1], "New lesson")

    def test_legacy_crlf_content_round_trips_through_apply_memory_lesson(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_content = "Legacy line one\r\nLegacy line two\r\n"
            learned_state.adopt(
                [DocumentChange(document="memory", content=legacy_content)],
                root_dir=root,
                now=_NOW,
            )

            outcome = apply_memory_lesson("New lesson", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertNotIn("\r", current.get("memory", ""))
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(entries[0], "Legacy line one\nLegacy line two")
            self.assertEqual(entries[1], "New lesson")


class ParseMemoryDocumentContinuationIndentationTests(unittest.TestCase):
    """A continuation line's indentation beyond the canonical two-space
    `_CONTINUATION_INDENT` prefix (e.g. code embedded in a lesson) must
    survive parsing, not be flattened to the continuation's first line via
    `str.lstrip()`.
    """

    def test_continuation_line_indentation_beyond_prefix_is_preserved(self) -> None:
        text = "- Do X:\n      code_line_one()\n      code_line_two()"
        entries = risk_tiered_application._parse_memory_document(text)
        self.assertEqual(
            entries, ("Do X:\n    code_line_one()\n    code_line_two()",)
        )

    def test_multiline_lesson_with_internal_code_indentation_round_trips_through_apply(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            multiline = "- Do X:\n      code_line_one()\n      code_line_two()"
            outcome = apply_memory_lesson(multiline, root_dir=root, now=_NOW)

            self.assertEqual(outcome.status, "applied")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), multiline)
            entries = risk_tiered_application._parse_memory_document(current.get("memory", ""))
            self.assertEqual(
                entries, ("Do X:\n    code_line_one()\n    code_line_two()",)
            )


class MergeMemoryContentAddedCountTests(unittest.TestCase):
    """`_merge_memory_content`'s `added_count` must be computed from
    membership of each `new_entries` item against `existing_entries`, not
    `len(merged) - len(existing_entries)` — the length-difference formula
    undercounts whenever `existing_entries` itself already contains a
    duplicate, since the duplicate inflates `len(existing_entries)` without
    ever occupying a second slot in `merged`.
    """

    def test_added_count_correct_when_existing_document_has_duplicate_entries(self) -> None:
        existing = risk_tiered_application._serialize_memory_document(
            ["Lesson A", "Lesson A", "Lesson B"]
        )
        candidate, added_count, pruned_count = risk_tiered_application._merge_memory_content(
            existing, ["Lesson C"], max_lessons=200
        )
        self.assertEqual(added_count, 1)
        self.assertEqual(pruned_count, 0)
        entries = risk_tiered_application._parse_memory_document(candidate)
        self.assertEqual(entries, ("Lesson A", "Lesson B", "Lesson C"))

    def test_added_count_zero_when_new_entry_already_present_despite_existing_duplicates(
        self,
    ) -> None:
        existing = risk_tiered_application._serialize_memory_document(
            ["Lesson A", "Lesson A", "Lesson B"]
        )
        _candidate, added_count, _pruned_count = risk_tiered_application._merge_memory_content(
            existing, ["Lesson B"], max_lessons=200
        )
        self.assertEqual(added_count, 0)

    def test_apply_memory_lesson_reports_correct_added_count_with_existing_duplicates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_content = risk_tiered_application._serialize_memory_document(
                ["Lesson A", "Lesson A", "Lesson B"]
            )
            learned_state.adopt(
                [DocumentChange(document="memory", content=legacy_content)],
                root_dir=root,
                now=_NOW,
            )

            outcome = apply_memory_lesson("Lesson C", root_dir=root, now=_LATER)

            self.assertEqual(outcome.status, "applied")
            self.assertIn("merged 1 new lesson(s)", outcome.reason or "")

    def test_duplicate_new_entries_are_not_overcounted(self) -> None:
        candidate, added_count, pruned_count = risk_tiered_application._merge_memory_content(
            None, ["Lesson A", "Lesson A"], max_lessons=200
        )
        self.assertEqual(added_count, 1)
        self.assertEqual(pruned_count, 0)
        entries = risk_tiered_application._parse_memory_document(candidate)
        self.assertEqual(entries, ("Lesson A",))

    def test_apply_memory_lesson_reports_added_count_one_for_duplicate_new_entries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = apply_memory_lesson("- Lesson A\n- Lesson A", root_dir=root, now=_NOW)

            self.assertEqual(outcome.status, "applied")
            self.assertIn("merged 1 new lesson(s)", outcome.reason or "")
            current = learned_state.read_current(root)
            self.assertEqual(current.get("memory"), "- Lesson A")


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

    def test_run_id_reaches_gate_trials_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outcome = apply_routing_table_update(
                '{"version": "v2", "routes": []}',
                root_dir=root,
                now=_NOW,
                runner=lambda: 0.95,
                run_id="weekly-run-32",
            )

            assert outcome.gate_decision is not None
            self.assertTrue(outcome.gate_decision.accepted)
            self.assertEqual(len(outcome.gate_decision.trial_records), 5)
            self.assertTrue(
                all(
                    record.run_id == "weekly-run-32"
                    for record in outcome.gate_decision.trial_records
                )
            )
            journal = learning_journal.read_journal(root)
            self.assertEqual(len(journal.replay_benchmarks), 5)
            self.assertTrue(
                all(record.run_id == "weekly-run-32" for record in journal.replay_benchmarks)
            )

    def test_invalid_run_id_fails_before_trials_or_journal_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = 0

            def runner() -> float:
                nonlocal calls
                calls += 1
                return 0.95

            with self.assertRaises(ValueError):
                apply_routing_table_update(
                    '{"version": "v2", "routes": []}',
                    root_dir=root,
                    now=_NOW,
                    runner=runner,
                    run_id="invalid run id",
                )

            self.assertEqual(calls, 0)
            self.assertFalse(learning_journal.journal_path(root).exists())
            self.assertEqual(learning_journal.read_journal(root).replay_benchmarks, ())


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
            self.assertEqual(current.get("memory"), "- Lesson v1")

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
