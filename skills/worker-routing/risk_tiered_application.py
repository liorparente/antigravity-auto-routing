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

**Cross-run memory accumulation (ADR 0010, Ticket 33).** `apply_memory_lesson`
owns accumulating memory lessons across separate calls — a second call does
not replace a first run's lessons the way `apply_routing_table_update` and
`submit_brief_proposal` wholesale-replace `content`. It reads the current
memory document, merges `content`'s entries into it (existing first, then
new, deduped by exact string equality), prunes to `DEFAULT_MAX_MEMORY_LESSONS`
via FIFO eviction, and adopts the merged result — atomically, via a
compare-and-swap retry loop against `learned_state.adopt`'s `expected_current`
precondition. See that function's docstring for the parsing grammar and the
retry mechanics.

**Seams.**
- `root_dir`: Path derived filesystem boundary for all on-disk state.
- `now`: Injected timezone-aware datetime. This module owns no clock.
- `runner`: Injected benchmark trial runner callable for Tier 2 evaluation.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
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
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# The document-wide bound on how many lesson entries `apply_memory_lesson`
# ever accumulates (ADR 0010). Fixed, not a per-call keyword — see that
# function's docstring for why a per-call override was rejected.
DEFAULT_MAX_MEMORY_LESSONS: int = 200

_MAX_MERGE_RETRIES = 8

_BULLET_PREFIX = "- "
_CONTINUATION_INDENT = "  "


class _StaleReadConflict(Exception):
    """Internal: `expected_current` no longer matched by the time `adopt` ran.

    Caught only by `apply_memory_lesson`'s own retry loop; never escapes
    this module. See `_adopt_with_idempotency`.
    """


def _digest(content: str) -> str:
    """The sha256 hex digest over `content`'s UTF-8 bytes.

    Mirrors `learned_state._digest` / `learner_worker._content_digest`
    exactly (not imported — see `learned_state.py`'s docstring on why a
    sibling in this directory restates rather than imports another's
    private helper), so a digest computed here is directly comparable to
    one either of those two produces.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _parse_memory_document(text: str) -> tuple[str, ...]:
    """Parse a memory document into its entries.

    Grammar (ADR 0010): an entry is one `"- "`-prefixed line, optionally
    followed by indented continuation lines (any line whose first character
    is whitespace), concatenated by `"\\n"` into that entry's text. Exactly
    one layer of indentation is stripped from each continuation line — the
    canonical `_CONTINUATION_INDENT` (two spaces) if present, else a single
    leading whitespace character — so any indentation beyond that layer
    (e.g. code embedded in a lesson) survives into the entry's text
    verbatim. `text`
    is first split with `str.splitlines()`, which recognizes `\\r\\n`, `\\r`,
    and `\\n` alike, so CRLF input round-trips identically to LF — no line
    in the parsed result ever holds an embedded `\\r`.

    Two outcomes when the strict bulleted grammar does not hold:
    - No line anywhere starts with `"- "`: the whole text is one opaque
      entry, verbatim (covers legacy pre-ticket content and any plain-string
      caller). Preserved as exactly one entry, never split at internal
      newlines, so it round-trips as one entry through every subsequent
      merge once this function's caller re-serializes it in canonical form.
    - At least one line starts with `"- "`, but some other line is neither a
      new entry's start nor a continuation of one (not indented): a
      malformed mixture. Partial bullet intent with a structurally invalid
      remainder is a bug to surface, not to silently reinterpret — raises
      `ValueError` naming the offending line.
    """
    lines = text.splitlines()
    if not any(line.startswith(_BULLET_PREFIX) for line in lines):
        return ("\n".join(lines),)

    entries: list[list[str]] = []
    for line in lines:
        if line.startswith(_BULLET_PREFIX):
            entries.append([line[len(_BULLET_PREFIX):]])
        elif line.startswith(_CONTINUATION_INDENT) and entries:
            entries[-1].append(line[len(_CONTINUATION_INDENT):])
        elif line[:1].isspace() and entries:
            entries[-1].append(line[1:])
        else:
            raise ValueError(
                "memory document is malformed: line "
                f"{line!r} is neither a '- '-prefixed entry nor an indented "
                "continuation of one"
            )
    return tuple("\n".join(entry_lines) for entry_lines in entries)


def _serialize_memory_document(entries: Sequence[str]) -> str:
    """The canonical wire form of `entries`: `"- "` first line, `"  "`-indented
    continuation lines, one entry after another. The inverse of
    `_parse_memory_document` for canonical and legacy content alike — a
    legacy entry, once serialized here, is a bulleted entry on every
    subsequent parse.
    """
    rendered: list[str] = []
    for entry in entries:
        entry_lines = entry.split("\n")
        rendered.append(_BULLET_PREFIX + entry_lines[0])
        rendered.extend(_CONTINUATION_INDENT + line for line in entry_lines[1:])
    return "\n".join(rendered)


def _merge_memory_content(
    existing: str | None, new_entries: Sequence[str], *, max_lessons: int
) -> tuple[str, int, int]:
    """Merge `new_entries` into `existing`'s own entries and prune to `max_lessons`.

    Order: existing entries first, then new — deduped by exact,
    case-sensitive string equality, first occurrence wins position (so a
    reaffirmed lesson keeps its original, older position rather than moving
    to the end). Bound enforcement is plain FIFO: once the deduped count
    exceeds `max_lessons`, the oldest-positioned entries are dropped first.

    Returns `(candidate, added_count, pruned_count)`: `added_count` is how
    many *distinct* `new_entries` were not already present in `existing`
    (a duplicate within `new_entries` itself is counted once, not once per
    occurrence); `pruned_count` is how many entries FIFO eviction dropped.
    """
    existing_entries = _parse_memory_document(existing) if existing else ()

    merged: list[str] = []
    seen: set[str] = set()
    for entry in (*existing_entries, *new_entries):
        if entry in seen:
            continue
        seen.add(entry)
        merged.append(entry)

    added_count = len(set(new_entries) - set(existing_entries))
    pruned_count = 0
    if len(merged) > max_lessons:
        pruned_count = len(merged) - max_lessons
        merged = merged[pruned_count:]

    return _serialize_memory_document(merged), added_count, pruned_count


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
    expected_current: Mapping[LearnedDocument, str | None] | None = None,
) -> tuple[ApplicationStatus, bool, VersionEntry | None, str | None]:
    """Adopt changes, treating an identical-content refusal as a successful
    no-op and a stale `expected_current` precondition as a private
    `_StaleReadConflict` for `apply_memory_lesson`'s own retry loop to catch
    — never surfaced to a `TierOutcome`, since a caller only ever sees the
    eventual settled outcome, not the transient races behind it.
    """
    try:
        entry = learned_state.adopt(
            changes,
            root_dir=root_dir,
            now=now,
            change_id=change_id,
            expected_current=expected_current,
        )
        return "applied", True, entry, None
    except ValueError as exc:
        message = str(exc)
        if "changes describe no actual difference from the current version" in message:
            history = learned_state.read_history(root_dir)
            current_entry = history[-1] if history else None
            return "no_op", True, current_entry, "content identical to current version"
        if "current state changed since expected_current was captured" in message:
            raise _StaleReadConflict from exc
        raise


def apply_memory_lesson(
    content: str,
    *,
    root_dir: Path,
    now: datetime,
    change_id: str | None = None,
    reject_if_candidate_digest: str | None = None,
) -> TierOutcome:
    """Tier 1: merge a memory lesson into the accumulated memory document and
    auto-apply the merged result directly into learned_state (ADR 0010).

    Owns cross-run accumulation. Unlike `apply_routing_table_update` and
    `submit_brief_proposal`, which adopt `content` as a document's entire
    new value, this function reads the current memory document, merges
    `content`'s entries into it (existing entries first, then new, deduped
    by exact case-sensitive string equality), prunes to
    `DEFAULT_MAX_MEMORY_LESSONS` via FIFO eviction of the oldest entries,
    and adopts the merged candidate — never `content` verbatim. See
    `_parse_memory_document` for the parsing grammar and
    `_merge_memory_content` for the merge/prune rules.

    `content` must not be blank (empty after stripping); that is rejected
    before any read happens, so a blank call never creates an empty
    version.

    The read-merge-write is atomic against a concurrent writer of the same
    `root_dir`. Each iteration reads `learned_state.read_current`, computes
    a merge candidate, and adopts it with `expected_current` set to exactly
    what was just read for `"memory"` — `learned_state.adopt` verifies that
    precondition inside its own lock (see its docstring), so a writer that
    adopts or rolls back between this function's read and its adopt call is
    detected as a `_StaleReadConflict` rather than silently overwritten, and
    this loop simply retries against a fresh read. `_MAX_MERGE_RETRIES = 8`
    is generous for real contention, which is expected to be rare — the two
    cadences that call this run per-session and per-week, not concurrently
    against the same `root_dir` in normal operation.

    `reject_if_candidate_digest`, when given, is compared against the
    digest of the actual merged candidate — not the raw `content` — inside
    the same atomic step: a candidate identical to content a caller's own
    revert just undid for a regression is rejected outright rather than
    silently re-adopted the moment merge would reconstruct it. Because that
    comparison happens after the merge but before another read, a
    concurrent writer could change `"memory"` in the gap between this
    iteration's read and the rejection decision; before returning
    `rejected`, `learned_state.read_current` is consulted once more, and a
    changed `"memory"` value sends this iteration back through the retry
    loop against a fresh read rather than rejecting on a state that is
    already stale. Replaces a
    two-call preview-then-apply interface, which would have been stale by
    the time a real call adopted.
    """
    _require_aware_now(now)
    if not isinstance(content, str):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"apply_memory_lesson: content must be a string, got {type(content).__name__}"
        )
    if not content.strip():
        raise ValueError("apply_memory_lesson: content must not be blank")
    if reject_if_candidate_digest is not None:
        if not isinstance(reject_if_candidate_digest, str):
            raise ValueError(
                "apply_memory_lesson: reject_if_candidate_digest must be a string, "
                f"got {type(reject_if_candidate_digest).__name__}"
            )
        if not _DIGEST_RE.fullmatch(reject_if_candidate_digest):
            raise ValueError(
                "apply_memory_lesson: reject_if_candidate_digest must match "
                f"{_DIGEST_RE.pattern}, got {reject_if_candidate_digest!r}"
            )
    new_entries = _parse_memory_document(content.strip())

    for _ in range(_MAX_MERGE_RETRIES):
        current = learned_state.read_current(root_dir)
        existing = current.get("memory")
        candidate, added_count, pruned_count = _merge_memory_content(
            existing, new_entries, max_lessons=DEFAULT_MAX_MEMORY_LESSONS
        )

        if (
            reject_if_candidate_digest is not None
            and _digest(candidate) == reject_if_candidate_digest
        ):
            if learned_state.read_current(root_dir).get("memory") != existing:
                continue
            return TierOutcome(
                document="memory",
                status="rejected",
                applied=False,
                change_id=change_id,
                reason=(
                    "anti-flapping guard: merged candidate identical to "
                    "content this run just reverted for a regression"
                ),
            )

        change = DocumentChange(document="memory", content=candidate)
        try:
            status, applied, entry, reason = _adopt_with_idempotency(
                [change],
                root_dir=root_dir,
                now=now,
                change_id=change_id,
                expected_current={"memory": existing},
            )
        except _StaleReadConflict:
            continue

        if reason is None and (added_count or pruned_count):
            parts = []
            if added_count:
                parts.append(f"merged {added_count} new lesson(s)")
            if pruned_count:
                parts.append(
                    f"pruned {pruned_count} oldest to stay within "
                    f"max_lessons={DEFAULT_MAX_MEMORY_LESSONS}"
                )
            reason = "; ".join(parts)

        return TierOutcome(
            document="memory",
            status=status,
            applied=applied,
            version_entry=entry,
            change_id=change_id,
            reason=reason,
        )

    raise RuntimeError(
        "apply_memory_lesson: exceeded retry budget under sustained contention"
    )


def apply_routing_table_update(
    content: str,
    *,
    root_dir: Path,
    now: datetime,
    runner: Callable[[], float],
    task_set: str = "routing_benchmark",
    change_id: str | None = None,
    run_id: str | None = None,
    trials: int = DEFAULT_TRIAL_COUNT,
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    report_journal_error: Callable[[str], None] | None = None,
) -> TierOutcome:
    """Tier 2: Apply a routing-table update only after passing the acceptance gate.

    `run_id`, when supplied, is forwarded to the acceptance gate so every
    trial's journaled `ReplayBenchmarkRecord` is traceable to the learner run
    that requested this update. The gate validates it before any trial runs or
    journal record is written.
    """
    _require_aware_now(now)
    gate_kwargs: dict[str, Any] = {
        "task_set": task_set,
        "root_dir": root_dir,
        "now": now,
        "run_id": run_id,
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
    "DEFAULT_MAX_MEMORY_LESSONS",
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
