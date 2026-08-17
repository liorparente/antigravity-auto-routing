"""Persistence and rendering boundary for advisory consultations.

This module owns the artifacts emitted after a Planner--Critic dialogue:
human-readable transcripts, structured telemetry, and LearningJournal
records.  It deliberately accepts result-shaped objects instead of importing
``advisory_consultation``; that keeps the extraction acyclic and makes the
redaction boundary independently testable when siblings are loaded by path.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import importlib.util
import json
import os
import secrets
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal


def _load_sibling(name: str):
    """Load a sibling module when this file was imported directly by path."""
    try:
        return __import__(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    from dialogue_contracts import (
        AdvisoryRoundVerdict,
        CriticVerdict,
        VerdictContractResult,
    )
except ModuleNotFoundError as exc:
    if exc.name != "dialogue_contracts":
        raise
    _load_sibling("dialogue_contracts")
    from dialogue_contracts import (
        AdvisoryRoundVerdict,
        CriticVerdict,
        VerdictContractResult,
    )

try:
    from dialogue_degradation import (
        _DEGRADATION_RUNG_LABELS,
        BUDGET_DEGRADATION_MARKER,
        DegradationRung,
    )
except ModuleNotFoundError as exc:
    if exc.name != "dialogue_degradation":
        raise
    _load_sibling("dialogue_degradation")
    from dialogue_degradation import (
        _DEGRADATION_RUNG_LABELS,
        BUDGET_DEGRADATION_MARKER,
        DegradationRung,
    )

DEGRADED_INDEPENDENCE_MARKER = "DEGRADED INDEPENDENCE"
CANARY_MARKER = "CANARY DIALOGUE"

AdvisoryOutcome = Literal[
    "consensus", "stalemate", "unparseable_verdict", "worker_error",
    "sensitivity_halt", "canary", "budget_skipped",
]
CanaryResult = Literal["catch", "miss"]
Occasion = Literal["ambiguity", "plan-review", "code-review", "post-mortem"]
RosterTopology = Literal["pair", "panel"]

if TYPE_CHECKING:
    from advisory_consultation import AdvisoryDebateResult, CanaryFixture


@dataclass(frozen=True)
class ConsultationTranscript:
    """A rendered consultation artifact and the identity/outcome it records."""

    content: str
    task_id: str
    outcome: str


@dataclass(frozen=True)
class AdvisoryTelemetryRecord:
    """JSON-serialisable, redacted telemetry for one consultation.

    The record contains only task identity, outcome metadata, and parser-
    derived verdict summaries.  It never carries the task, plan, critique, or
    a detected sensitive value, so it remains safe on a sensitivity halt.
    """

    timestamp: str
    task_id: str
    rounds_run: int
    outcome: AdvisoryOutcome
    planner_model: str
    critic_model: str
    kind: str = "advisory_consultation"
    degraded_independence: bool = False
    canary_result: CanaryResult | None = None
    degradation_rung: DegradationRung = 0
    occasion: Occasion = "ambiguity"
    topology: RosterTopology = "pair"
    round_verdicts: tuple[AdvisoryRoundVerdict, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Return the JSON-safe wire representation used by telemetry JSONL."""
        return dataclasses.asdict(self)


def _atomic_text_write(path: Path, content: str) -> None:
    """Atomically replace ``path`` so readers never see a partial artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _default_task_id(task_description: str) -> str:
    """Return a stable, non-plaintext 16-hex task identity."""
    return hashlib.sha256(task_description.encode("utf-8")).hexdigest()[:16]


def _resolve_task_id(task_description: str, task_id: str | None, outcome: AdvisoryOutcome) -> str:
    """Prefer a caller id; otherwise avoid task-derived ids for halt/canary.

    A sensitivity halt must not leak even a confirmation digest of its task.
    A canary also receives a fresh identity so it cannot be joined with the
    real task it probes.
    """
    if task_id is not None:
        return task_id
    if outcome in ("sensitivity_halt", "canary"):
        return secrets.token_hex(8)
    return _default_task_id(task_description)


def _render_consultation_transcript(
    task_description: str,
    result: AdvisoryDebateResult,
    *,
    canary_fixture: CanaryFixture | None = None,
) -> str:
    """Render a complete non-sensitive transcript from a debate result.

    Only callers that have already passed sensitivity detection may use this
    renderer.  The separate halt renderer below never accepts task content.
    """
    rounds = result.rounds
    is_canary = result.outcome == "canary" and canary_fixture is not None
    lines = [
        "# AdvisoryConsultation Transcript",
        "",
        f"**Outcome:** {result.outcome}",
        f"**Planner:** {result.planner_model}",
        f"**Critic:** {result.critic_model}",
        f"**Rounds run:** {len(rounds)}",
        "",
    ]
    if result.degraded_independence:
        lines.extend([
            (
                f"**{DEGRADED_INDEPENDENCE_MARKER}:** This dialogue could not achieve "
                "full cross-family independence. Treat its outcome with reduced confidence."
            ),
            "",
        ])
    if is_canary:
        assert canary_fixture is not None
        result_text = (
            "approved the flawed fixture"
            if result.canary_result == "miss"
            else "did not approve the flawed fixture"
        )
        lines.extend([
            (
                f"**{CANARY_MARKER}:** This dialogue reviewed seeded-flaw fixture "
                f"`{canary_fixture.id}`, not a real mission artifact. No Planner was invoked."
            ),
            "",
            f"Seeded flaw: {canary_fixture.flaw_summary}",
            "",
            f"Critic result: **{result.canary_result}** ({result_text}).",
            "",
        ])
    if result.degradation_rung > 0:
        lines.extend([
            (
                f"**{BUDGET_DEGRADATION_MARKER}:** degradation rung {result.degradation_rung} "
                f"({_DEGRADATION_RUNG_LABELS[result.degradation_rung]})."
            ),
            "",
        ])
    lines.extend(["## Task", "", task_description, ""])
    if result.outcome == "budget_skipped":
        lines.append("_No Planner or Critic was contacted because the session budget was exhausted._")
    elif not rounds:
        lines.append("_No rounds were run._")
    for index, round_ in enumerate(rounds, start=1):
        panel = round_.critic_b_response is not None
        planner_header = f"### Planner ({result.planner_model})"
        if is_canary:
            assert canary_fixture is not None
            planner_header = f"### Seeded-flaw fixture (`{canary_fixture.id}`)"
        critic_header = (
            f"### Critic A ({result.critic_model})"
            if panel
            else f"### Critic ({result.critic_model})"
        )
        lines.extend([
            f"## Round {index}", "", planner_header, "", round_.planner_proposal, "",
            critic_header, "", round_.critic_response, "",
        ])
        critic_b_response = round_.critic_b_response
        if critic_b_response is not None:
            lines.extend(["### Critic B", "", critic_b_response, ""])
    return "\n".join(lines)


def _render_sensitivity_halt_transcript(marker: str, task_id: str) -> str:
    """Render a fail-closed halt artifact without accepting any task text."""
    return "\n".join([
        "# AdvisoryConsultation Transcript", "", "**Outcome:** sensitivity_halt",
        f"**Task ID:** {task_id}", "",
        f"Task text matched sensitivity marker `{marker}`. Human approval is required before this task may proceed.",
        "", "No Planner or Critic was contacted. No task details are recorded here.",
    ])


def _write_transcript(path: Path, content: str) -> str | None:
    """Replace the transcript and return a diagnostic instead of raising on I/O failure."""
    try:
        _atomic_text_write(path, content)
    except OSError as exc:
        return f"failed to write consultation transcript at {path}: {exc}"
    return None


def _append_jsonl_locked(path: Path, record: dict[str, object]) -> None:
    """Append one deterministically encoded JSONL record under an exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _reduce_dialogue_round(entry: AdvisoryRoundVerdict) -> tuple[CriticVerdict, int]:
    """Reduce pair/panel verdicts to fail-closed verdict and weakest quote count."""
    results: Sequence[VerdictContractResult] = (
        [entry.critic_a] if entry.critic_b is None else [entry.critic_a, entry.critic_b]
    )
    verdicts = [item.verdict for item in results]
    if "unparseable" in verdicts:
        verdict: CriticVerdict = "unparseable"
    elif all(item == "approved" for item in verdicts):
        verdict = "approved"
    else:
        verdict = "revise"
    return verdict, min(item.verified_quote_count for item in results)


def _build_telemetry_record(result: AdvisoryDebateResult, *, task_id: str) -> AdvisoryTelemetryRecord:
    """Copy safe result metadata into the consultation telemetry schema."""
    return AdvisoryTelemetryRecord(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), task_id=task_id,
        rounds_run=result.rounds_run, outcome=result.outcome, planner_model=result.planner_model,
        critic_model=result.critic_model, degraded_independence=result.degraded_independence,
        canary_result=result.canary_result, degradation_rung=result.degradation_rung,
        occasion=result.occasion, topology=result.topology, round_verdicts=result.round_verdicts,
    )


def _write_telemetry_record(path: Path, record: AdvisoryTelemetryRecord) -> str | None:
    """Append telemetry and return an error string rather than disrupting dialogue flow."""
    try:
        _append_jsonl_locked(path, record.to_mapping())
    except OSError as exc:
        return f"failed to write consultation telemetry at {path}: {exc}"
    return None


def _write_dialogue_quality_record(
    result: AdvisoryDebateResult, *, task_id: str, run_id: str | None, root_dir: Path
) -> str | None:
    """Best-effort append of the redacted LearningJournal dialogue-quality record."""
    try:
        learning_journal = _load_sibling("learning_journal")
        record = learning_journal.DialogueQualityRecord(
            task=learning_journal.TaskLabel.for_task(task_id), occasion=result.occasion,
            topology=result.topology,
            rounds=tuple(learning_journal.DialogueRound(*_reduce_dialogue_round(item)) for item in result.round_verdicts),
            canaries_planted=1 if result.canary_result is not None else 0,
            canaries_caught=1 if result.canary_result == "catch" else 0,
            degraded=result.degradation_rung != 0,
            independent=not result.degraded_independence, run_id=run_id,
        )
        return learning_journal.append_journal_record(record, root_dir=root_dir)
    except (ImportError, ValueError) as exc:
        return f"failed to build dialogue-quality record: {exc}"


def _write_plan_outcome_record(*, task_id: str, accepted: bool, run_id: str | None, root_dir: Path) -> str | None:
    """Best-effort record of the plan verdict, without letting instrumentation abort a dialogue."""
    try:
        learning_outcomes = _load_sibling("learning_outcomes")
        return learning_outcomes.record_plan_outcome(task_id, accepted=accepted, run_id=run_id, root_dir=root_dir)
    except (ImportError, ValueError) as exc:
        return f"failed to record plan outcome: {exc}"
