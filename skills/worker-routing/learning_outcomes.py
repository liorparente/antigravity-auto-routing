#!/usr/bin/env python3
"""Outcome recording: the seam where a decision's ground truth gets journaled.

`learning_journal.OutcomeRecord` already exists — schema, validation, and the
`(signal, verdict)` pairing are all settled there. What is missing is the
public surface a caller far from that schema actually calls: a test runner
reading its own exit code, a reviewer stating a verdict, a human settling a
stalemate. None of those callers should have to import `learning_journal`,
build a `TaskLabel`, or know what an `OutcomeRecord` looks like. This module
is that surface: four small functions, one per ground truth, each taking the
`task_id` of the decision being graded and the plain fact that became known
about it.

**One function per truth, not one function with a mode flag.** The four
truths become known at different times, from different callers, carrying
different plain facts (`passed`, `approved`, `accepted`, a chosen option) —
a single `record_outcome(signal, verdict, ...)` entry point would just move
`learning_journal`'s enum-pairing problem one layer up and hand every caller
a `verdict` vocabulary to get right by hand instead of a boolean or an
object they already have.

**No caller ever gets a synthetic `task_id`.** `advisory_consultation`'s
`_resolve_task_id` invents one when a *decision* needs an identity and the
caller supplied none — that is correct there because some identity has to
exist before a decision can be journaled at all. An *outcome* is the
opposite case: it grades a decision that (by construction) already has an
identity, so inventing one here could never do anything but fabricate a join
that does not exist. That is this module's answer to "recording an outcome
for an unknown task": every function below requires `task_id` as a plain,
non-optional argument, and the only validation performed on it is the one
`TaskLabel.for_task` already runs — well-formed and non-empty, exactly the
bar every other identifier in the journal clears. This module deliberately
does not go further and cross-check `task_id` against either
`.ralph/routing_telemetry.jsonl` or the journal's own `worker_execution`
records: those two streams are written by different processes on different
schedules, an outcome can legitimately arrive before, after, or without ever
seeing a matching decision record show up in either one, and a presence
check would therefore either race a slow decision or force every outcome
caller to serialize itself behind one. So "unknown" here means exactly one
thing — no `task_id` was named — and it is refused loudly, at construction,
by the same `ValueError` `TaskLabel.for_task` already raises for a missing
or malformed identifier. A syntactically valid `task_id` that in fact
matches no decision anywhere is a scoreboard-reader's problem (an outcome
record with no telemetry counterpart to join against), not something this
module can honestly detect.

**Construction raises, writing never does.** Every function below returns
`str | None` — an error message or success — because the write itself goes
through `learning_journal.append_journal_record`, whose own contract is
"a broken disk degrades the learning loop, never the caller" (see that
module's docstring). Nothing here changes that: a caller can fold the
returned string into whatever it already reports and move on, exactly as
`production_invoker.make_journaled_invoke_worker` does for the
worker-execution family. Building an invalid record — a malformed
`task_id`, a stalemate option that does not belong to the report it is
paired with — is a call-site bug instead, and raises, matching every other
construction path in `learning_journal`.

**The stalemate function is wired to the real stalemate path, not a stand-in
for one.** `record_stalemate_resolution` takes the actual
`advisory_consultation.AdvisoryStalemateReport` a stalemate produced and the
actual `AdvisoryResolutionOption` the human chose from `report.options` —
not a bare integer a caller could mistype — and verifies the option genuinely
belongs to that report before mapping it to the `stalemate_resolution`
verdict `learning_journal` already reserved for it (`"planner"`, `"critic"`,
or `"human"`, in the same order `advisory_consultation._build_stalemate_report`
builds its three options). As of this ticket nothing in the codebase captures
which option a human actually picked — `run_advisory_consultation_debate`
returns the report and stops, and no interactive prompt exists anywhere in
this repo to ask the question. This function is that missing recording step:
the seam a future human-facing caller (a CLI prompt, a chat reply handler)
calls once the pick is made. It does not invent that caller.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import learning_journal

if TYPE_CHECKING:
    # Annotation-only under `from __future__ import annotations`:
    # `record_stalemate_resolution` names two of this module's types in its
    # signature and touches neither at runtime (`chosen not in
    # report.options` and `chosen.id` are plain attribute access). Importing
    # it for real would make every caller of `record_test_result` pay for
    # `advisory_consultation` and the `production_invoker` chain behind it.
    import advisory_consultation

# Mirrors `advisory_consultation._build_stalemate_report`'s three options, in
# order: (1) approve the Planner's architecture, (2) approve the Critic's,
# (3) escalate to a human. `learning_journal.OUTCOME_VERDICTS` already
# documents this alignment on `stalemate_resolution` — keep both in step; a
# fourth option there needs a fourth verdict and a fourth entry here.
_STALEMATE_VERDICT_BY_OPTION_ID: dict[int, learning_journal.OutcomeVerdict] = {
    1: "planner",
    2: "critic",
    3: "human",
}


def _record(
    task_id: str,
    *,
    signal: learning_journal.OutcomeSignal,
    verdict: learning_journal.OutcomeVerdict,
    root_dir: Path,
) -> str | None:
    """Build and append one `OutcomeRecord`. The one path every truth below shares.

    `TaskLabel.for_task(task_id)` is where an unnamed, non-string, or
    malformed `task_id` raises — see this module's docstring for why that is
    the whole of "unknown task" handling here.
    """
    record = learning_journal.OutcomeRecord(
        task=learning_journal.TaskLabel.for_task(task_id),
        signal=signal,
        verdict=verdict,
    )
    return learning_journal.append_journal_record(record, root_dir=root_dir)


def record_test_result(task_id: str, *, passed: bool, root_dir: Path) -> str | None:
    """Record whether the tests a decision produced passed or failed."""
    return _record(
        task_id, signal="tests", verdict="pass" if passed else "fail", root_dir=root_dir
    )


def record_review_verdict(task_id: str, *, approved: bool, root_dir: Path) -> str | None:
    """Record a review's verdict on the decision it graded."""
    return _record(
        task_id,
        signal="review",
        verdict="approved" if approved else "rejected",
        root_dir=root_dir,
    )


def record_plan_outcome(task_id: str, *, accepted: bool, root_dir: Path) -> str | None:
    """Record whether a plan a decision produced was accepted or rejected."""
    return _record(
        task_id,
        signal="plan",
        verdict="accepted" if accepted else "rejected",
        root_dir=root_dir,
    )


def record_stalemate_resolution(
    task_id: str,
    report: advisory_consultation.AdvisoryStalemateReport,
    chosen: advisory_consultation.AdvisoryResolutionOption,
    *,
    root_dir: Path,
) -> str | None:
    """Record which of a stalemate's three options the human picked.

    `chosen` must be one of `report.options`, unchanged — passing back an
    option built by hand, or one taken from a different stalemate report, is
    a call-site bug and raises `ValueError` rather than silently mapping it
    by `id` alone. That is deliberately stricter than the other three
    `record_*` functions need to be: `passed`/`approved`/`accepted` are
    plain facts with no report to belong to, while a stalemate's options are
    specific to the stalemate that produced them.
    """
    if chosen not in report.options:
        raise ValueError(
            "chosen option does not belong to this stalemate report's options "
            "(pass back one of report.options unchanged, not a hand-built copy)"
        )
    verdict = _STALEMATE_VERDICT_BY_OPTION_ID.get(chosen.id)
    if verdict is None:
        raise ValueError(
            f"stalemate option id {chosen.id} has no stalemate_resolution "
            "verdict mapping in _STALEMATE_VERDICT_BY_OPTION_ID"
        )
    return _record(
        task_id, signal="stalemate_resolution", verdict=verdict, root_dir=root_dir
    )


__all__ = [
    "record_plan_outcome",
    "record_review_verdict",
    "record_stalemate_resolution",
    "record_test_result",
]
