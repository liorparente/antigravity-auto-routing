#!/usr/bin/env python3
"""RiskTieredApplication: four tiers of change application for the learning loop.

Spec 0004 ticket 20 (spec 0004 user story 13, 14, 15, 16):
- Tier 1 (Memory lessons): auto-apply, versioned in `learned_state`.
- Tier 2 (Routing-table updates): auto-apply only after passing the acceptance
  gate (`acceptance_gate.evaluate_proposal`).
- Tier 3 (Brief diffs): held as pending proposals until explicit human approval.
- Tier 4 (The protocol): unreachable by construction — `LearnedDocument` has no
  member for protocol files.

**Idempotency guarantee.** `learned_state.adopt` refuses a change identical to
the document's current content with a `ValueError`. Tier application catches
that refusal and reports it as a successful `no_op` rather than a failure,
since the intended state is already current.

**Seams.**
- `root_dir`: Path derived filesystem boundary for all on-disk state.
- `now`: Injected timezone-aware datetime. This module owns no clock.
- `runner`: Injected benchmark trial runner callable for Tier 2 evaluation.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import acceptance_gate
import learned_state
import learning_scoreboard
from acceptance_gate import (
    DEFAULT_SCORE_THRESHOLD,
    DEFAULT_TRIAL_COUNT,
    GateDecision,
)
from learned_state import (
    DocumentChange,
    LearnedDocument,
    VersionEntry,
)

ApplicationStatus = Literal["applied", "rejected", "pending", "no_op"]

PENDING_PROPOSALS_RELATIVE_PATH = Path(".ralph") / "pending_proposals.jsonl"
_PROPOSAL_LOCK_RELATIVE_PATH = Path(".ralph") / ".pending_proposals.lock"

_PROPOSAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _require_aware_now(now: datetime) -> None:
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _wire_timestamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_proposal_id(value: object, field_name: str = "proposal_id") -> None:
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not _PROPOSAL_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must match {_PROPOSAL_ID_RE.pattern}, got {value!r}")


@dataclass(frozen=True)
class PendingProposal:
    proposal_id: str
    document: LearnedDocument
    content: str
    timestamp: str

    def __post_init__(self) -> None:
        _validate_proposal_id(self.proposal_id)
        if self.document != "briefs":
            raise ValueError(
                f"only 'briefs' proposals are held as pending proposals, got {self.document!r}"
            )
        if not isinstance(self.content, str):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
                f"content must be a string, got {type(self.content).__name__}"
            )
        if not isinstance(self.timestamp, str) or not _TIMESTAMP_RE.fullmatch(self.timestamp):
            raise ValueError(f"timestamp must match {_TIMESTAMP_RE.pattern}, got {self.timestamp!r}")


@dataclass(frozen=True)
class TierOutcome:
    document: LearnedDocument
    status: ApplicationStatus
    applied: bool
    version_entry: VersionEntry | None = None
    gate_decision: GateDecision | None = None
    proposal_id: str | None = None
    change_id: str | None = None
    reason: str | None = None


def _adopt_with_idempotency(
    changes: Sequence[DocumentChange],
    *,
    root_dir: Path,
    now: datetime,
    change_id: str | None = None,
) -> tuple[ApplicationStatus, bool, VersionEntry | None, str | None]:
    """Adopt changes, treating an identical-content refusal as a successful no-op."""
    try:
        entry = learned_state.adopt(changes, root_dir=root_dir, now=now, change_id=change_id)
        return "applied", True, entry, None
    except ValueError as exc:
        if "changes describe no actual difference from the current version" in str(exc):
            history = learned_state.read_history(root_dir)
            current_entry = history[-1] if history else None
            return "no_op", True, current_entry, "content identical to current version"
        raise


def apply_memory_lesson(
    content: str,
    *,
    root_dir: Path,
    now: datetime,
    change_id: str | None = None,
) -> TierOutcome:
    """Tier 1: Auto-apply a memory lesson directly into learned_state."""
    _require_aware_now(now)
    change = DocumentChange(document="memory", content=content)
    status, applied, entry, reason = _adopt_with_idempotency(
        [change],
        root_dir=root_dir,
        now=now,
        change_id=change_id,
    )
    return TierOutcome(
        document="memory",
        status=status,
        applied=applied,
        version_entry=entry,
        change_id=change_id,
        reason=reason,
    )


def apply_routing_table_update(
    content: str,
    *,
    root_dir: Path,
    now: datetime,
    runner: Callable[[], float],
    task_set: str = "routing_benchmark",
    change_id: str | None = None,
    trials: int = DEFAULT_TRIAL_COUNT,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    report_journal_error: Callable[[str], None] | None = None,
) -> TierOutcome:
    """Tier 2: Apply a routing-table update only after passing the acceptance gate."""
    _require_aware_now(now)
    gate_kwargs: dict[str, Any] = {
        "task_set": task_set,
        "root_dir": root_dir,
        "now": now,
        "trials": trials,
        "score_threshold": score_threshold,
    }
    if report_journal_error is not None:
        gate_kwargs["report_journal_error"] = report_journal_error

    decision = acceptance_gate.evaluate_proposal(runner, **gate_kwargs)
    if not decision.accepted:
        return TierOutcome(
            document="routing_table",
            status="rejected",
            applied=False,
            gate_decision=decision,
            change_id=change_id,
            reason="acceptance gate rejected proposal",
        )

    change = DocumentChange(document="routing_table", content=content)
    status, applied, entry, reason = _adopt_with_idempotency(
        [change],
        root_dir=root_dir,
        now=now,
        change_id=change_id,
    )
    return TierOutcome(
        document="routing_table",
        status=status,
        applied=applied,
        version_entry=entry,
        gate_decision=decision,
        change_id=change_id,
        reason=reason,
    )


@contextmanager
def _pending_proposals_lock(root_dir: Path) -> Iterator[None]:
    lock_path = root_dir / _PROPOSAL_LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _pending_proposals_file(root_dir: Path) -> Path:
    return root_dir / PENDING_PROPOSALS_RELATIVE_PATH


def _read_pending_proposals_unlocked(root_dir: Path) -> tuple[PendingProposal, ...]:
    path = _pending_proposals_file(root_dir)
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    proposals = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        data = json.loads(text)
        proposals.append(
            PendingProposal(
                proposal_id=data["proposal_id"],
                document=data["document"],
                content=data["content"],
                timestamp=data["timestamp"],
            )
        )
    return tuple(proposals)


def read_pending_proposals(root_dir: Path) -> tuple[PendingProposal, ...]:
    """Read all pending brief proposals from disk."""
    path = _pending_proposals_file(root_dir)
    if not path.exists():
        return ()
    with _pending_proposals_lock(root_dir):
        return _read_pending_proposals_unlocked(root_dir)


def _write_pending_proposals_unlocked(root_dir: Path, proposals: Sequence[PendingProposal]) -> None:
    path = _pending_proposals_file(root_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "proposal_id": p.proposal_id,
                "document": p.document,
                "content": p.content,
                "timestamp": p.timestamp,
            },
            sort_keys=True,
        )
        + "\n"
        for p in proposals
    ]
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".pending_proposals.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as stream:
            stream.write("".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def submit_brief_proposal(
    content: str,
    *,
    root_dir: Path,
    now: datetime,
    proposal_id: str,
) -> TierOutcome:
    """Tier 3: Stage a brief update proposal as pending until human approval."""
    _require_aware_now(now)
    _validate_proposal_id(proposal_id)
    proposal = PendingProposal(
        proposal_id=proposal_id,
        document="briefs",
        content=content,
        timestamp=_wire_timestamp(now),
    )
    with _pending_proposals_lock(root_dir):
        current_proposals = list(_read_pending_proposals_unlocked(root_dir))
        current_proposals = [p for p in current_proposals if p.proposal_id != proposal_id]
        current_proposals.append(proposal)
        _write_pending_proposals_unlocked(root_dir, current_proposals)

    return TierOutcome(
        document="briefs",
        status="pending",
        applied=False,
        proposal_id=proposal_id,
        reason="held pending human approval",
    )


def approve_pending_proposal(
    proposal_id: str,
    *,
    root_dir: Path,
    now: datetime,
    change_id: str | None = None,
) -> TierOutcome:
    """Tier 3: Explicitly approve and adopt a pending brief proposal."""
    _require_aware_now(now)
    _validate_proposal_id(proposal_id)
    with _pending_proposals_lock(root_dir):
        current_proposals = list(_read_pending_proposals_unlocked(root_dir))
        target: PendingProposal | None = None
        remaining = []
        for p in current_proposals:
            if p.proposal_id == proposal_id:
                target = p
            else:
                remaining.append(p)

        if target is None:
            raise ValueError(f"no pending proposal found with proposal_id={proposal_id!r}")

        _write_pending_proposals_unlocked(root_dir, remaining)

    effective_change_id = change_id or proposal_id
    change = DocumentChange(document=target.document, content=target.content)
    status, applied, entry, reason = _adopt_with_idempotency(
        [change],
        root_dir=root_dir,
        now=now,
        change_id=effective_change_id,
    )
    return TierOutcome(
        document="briefs",
        status=status,
        applied=applied,
        version_entry=entry,
        proposal_id=proposal_id,
        change_id=effective_change_id,
        reason=reason,
    )


def reject_pending_proposal(
    proposal_id: str,
    *,
    root_dir: Path,
) -> None:
    """Tier 3: Discard a pending brief proposal without adopting it."""
    _validate_proposal_id(proposal_id)
    with _pending_proposals_lock(root_dir):
        current_proposals = list(_read_pending_proposals_unlocked(root_dir))
        remaining = [p for p in current_proposals if p.proposal_id != proposal_id]
        if len(remaining) == len(current_proposals):
            raise ValueError(f"no pending proposal found with proposal_id={proposal_id!r}")
        _write_pending_proposals_unlocked(root_dir, remaining)


@dataclass(frozen=True)
class RevertOutcome:
    """The result of attempting to revert a scoreboard regression to whichever
    live adoption is most likely responsible for it."""

    status: Literal["reverted", "unattributable", "unrevertable", "no_regression"]
    regressed_metrics: tuple[str, ...]
    reverted_change_id: str | None = None
    version_entry: VersionEntry | None = None
    reason: str | None = None


def _parse_version_timestamp(value: str) -> datetime:
    """Parse a `VersionEntry.timestamp` wire string into an aware UTC datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def revert_attributable_regression(
    comparison: learning_scoreboard.ScoreboardComparison,
    *,
    root_dir: Path,
    now: datetime,
    window_days: int = learning_scoreboard.DEFAULT_WINDOW_DAYS,
    change_id: str | None = None,
) -> RevertOutcome:
    """Auto-revert a scoreboard regression to the adoption most likely responsible.

    Attribution is deliberately narrow: only the most recent live adoption
    (per `learned_state.most_recent_live_adoption`, the same bracket-matching
    walk `learned_state.roll_back` uses to pick its own target) is ever a
    candidate, and only when its timestamp falls in the trailing
    `window_days` window ending at `now`. A regression with no such
    adoption is `unattributable` rather than guessed at; a regression whose
    only live adoption is the very first one ever made is `unrevertable`,
    since `learned_state.roll_back` refuses to undo the un-learned system's
    starting state.
    """
    _require_aware_now(now)
    regressed_metrics = tuple(
        change.name for change in comparison.changes if change.status == "regressed"
    )
    if not regressed_metrics:
        return RevertOutcome(
            status="no_regression",
            regressed_metrics=(),
            reason="no scoreboard metric regressed",
        )

    history = learned_state.read_history(root_dir)
    window_start = now - timedelta(days=window_days)

    target_adoption = learned_state.most_recent_live_adoption(history)
    if target_adoption is None or not (
        window_start <= _parse_version_timestamp(target_adoption.timestamp) <= now
    ):
        return RevertOutcome(
            status="unattributable",
            regressed_metrics=regressed_metrics,
            reason="no live adoption found in the trailing window",
        )

    try:
        entry = learned_state.roll_back(root_dir=root_dir, now=now, change_id=change_id)
    except ValueError as exc:
        if "cannot roll back the first adoption" in str(exc):
            return RevertOutcome(
                status="unrevertable",
                regressed_metrics=regressed_metrics,
                reason=(
                    "cannot roll back the first adoption — state before it is "
                    "un-learned system"
                ),
            )
        raise

    return RevertOutcome(
        status="reverted",
        regressed_metrics=regressed_metrics,
        reverted_change_id=target_adoption.change_id,
        version_entry=entry,
    )


__all__ = [
    "ApplicationStatus",
    "PendingProposal",
    "RevertOutcome",
    "TierOutcome",
    "apply_memory_lesson",
    "apply_routing_table_update",
    "approve_pending_proposal",
    "read_pending_proposals",
    "reject_pending_proposal",
    "revert_attributable_regression",
    "submit_brief_proposal",
]
