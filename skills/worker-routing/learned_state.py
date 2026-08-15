#!/usr/bin/env python3
"""LearnedState: versioned snapshots of the system's own learned documents,
with a one-step rollback. Spec 0004 ticket 19 (spec 0004 user story 17).

**What this module is not.** `learning_journal.py`'s entire design is
"content-free" — no field anywhere may carry prose, because the journal is a
*signal* stream a learner reads, not the material it learns. This module is
the opposite kind of thing on purpose: a learned lesson, a routing-table
diff, or a distilled brief *is* text, and this store's whole job is to keep
successive versions of that text safe to adopt and cheap to undo. A reviewer
coming from `learning_journal.py`, `learning_scoreboard.py`, or
`acceptance_gate.py` — every one of which forbids exactly what this module
does — should read this paragraph before flagging a "content-bearing field"
here as a defect. It is the point.

**Decision 1 — "git-tracked" means the files sit in the working tree and are
not gitignored. This module never runs `git`.** Three facts forced that,
independently:

- `.gitignore` ignores `.ralph/`, where `learning_journal.jsonl` lives, so a
  git-tracked learned-state store cannot reuse that location — it needs a
  tracked directory of its own (`learned-state/`, checked by
  `test_learned_state.py` against the repository's real `.gitignore`, not
  merely asserted in prose).
- The routing protocol's own rule: "worker sandboxes lock `.git/`, routing
  these to a worker deadlocks." Ticket 22's `LearnerWorker` — the eventual
  caller of `adopt` — *is* a worker. A module that shelled out to `git
  commit` on the adoption path would deadlock in the exact environment it
  was built to run in.
- Spec 0004 routes learned state through `install.sh`'s propagation, not
  through git plumbing this module would have to own and test.

So a version is nothing more than ordinary files under a tracked directory.
The human's ordinary `git commit` is what actually captures a version in
history; this module's `roll_back` restores from its own on-disk snapshot,
never from git history, and has no code path that could reach for one.

**Decision 2 — a rollback moves a pointer between immutable snapshots; it
never rewrites one, and a version's directory is written exactly once.**
"The state after a rollback matches the prior version exactly, byte for
byte" is a claim this module does not verify after the fact — it is true by
construction, because the bytes are never touched a second time. `adopt`
writes a version's directory exactly once (`Path.mkdir(...,
exist_ok=False)`), and `roll_back` only ever changes which version
`current_version_dir` resolves to. Nothing "restores" content by copying it
back over something else — restoring *is* pointing at bytes that were never
disturbed.

The version number `adopt` writes to is one past the highest version number
this store has ever used — not one past whichever version is merely
*current*, and not one past whichever version `history.jsonl` alone
remembers, but the higher of that *and* whatever `vNNNN` directory
`_highest_version_on_disk` finds actually present under `versions/`. The
disk source exists because `_write_snapshot` can finish writing a version's
directory and then the process can die before `adopt` appends the matching
`history.jsonl` line, leaving an orphaned snapshot `history.jsonl` has no
record of at all. Allocating from history alone would recompute that exact
same, now-occupied number on every subsequent call, bricking every `adopt`
after the crash, forever; reading both sources instead means the orphan is
simply skipped and the store self-heals on the very next call. The orphaned
directory itself is left on disk exactly as the crash left it — a recovery
path that deletes data on a guess is worse than one that leaves an inert
directory behind for forensics. It is an expected byproduct of this
mechanism, not damage: see `CONTEXT.md`'s `LearnedState` entry, where an
operator who finds one is told it is safe to remove once confirmed absent
from `history.jsonl`.

Self-healing only works when `_highest_version_on_disk` can actually see
the stray entry, and it counts one only when **both** of its tests pass:
`entry.is_dir()` is true, *and* the name matches `_VERSION_DIRNAME_RE`
case-sensitively. Anything failing either test is invisible to the scan and
therefore collides — and the durable statement of what defeats self-healing
is that property, not a list of the instances anyone happened to think of.
Three drafts of this paragraph enumerated instances and each undercounted;
the property cannot.

Worked examples of each half, all confirmed by running them: failing
`is_dir()` — a **file** named `v0001` under `versions/`, a symlink pointing
at a file, or a dangling symlink, any of which leaves `adopt` allocating
version 1 and colliding identically, forever (a symlink to a *directory*
passes `is_dir()` and is counted, so it does not collide). Failing the
regex — a directory named with different case, e.g. `V0001`, on a
case-insensitive filesystem (macOS's default), where the filesystem
resolves `V0001` and `v0001` to one path for `mkdir`'s purposes while the
case-sensitive pattern matches neither. All need a hand-created stray
entry, so severity is low, but each reproduces the exact permanent-brick
shape the self-heal above exists to prevent, entering by a side door.

Hand-made tampering that satisfies *both* tests is not among them: a
fabricated `v0009` directory is counted like any other, so `adopt`
allocates past it rather than colliding with it. The only way to reach the
collision without defeating the scan is a version-numbering bug in this
module itself. In every one
of these cases `_write_snapshot` never overwrites — a collision on `mkdir`
raises, and the raise is a `ValueError`, not a bare `FileExistsError`
naming an absolute path with no guidance.

What that `ValueError` must not do is tell the operator what the occupying
entry *is*. Under a numbering bug the fault's location is by definition
unknown — it could be in `_highest_version_on_disk`'s own scan — so the
occupant might be a real adopted snapshot or might be a stray one, and any
sentence asserting which is a guess dressed as a diagnosis. What the
message gives instead is the question the operator can actually answer, and
whose two answers partition the space: does `history.jsonl` name this
version? If it does, the occupant is adopted history and deleting it
destroys real data to work around a fault only a code change fixes — the
same "deletes data on a guess" the orphan case above is written to avoid.
If it does not, the occupant is a stray entry and removing it lets the
store allocate again. That is the same test `CONTEXT.md`'s `LearnedState`
entry already hands an operator who finds an orphan, so the two pieces of
guidance key on one question rather than two rules that could drift apart.
This module removes nothing on its own under either answer.

**Decision 3 — `roll_back` undoes the most recent adoption that has not
already been undone.** The tempting alternative, "restore version N-1",
oscillates: a second `roll_back` would restore N-2, re-applying whatever the
first rollback had just removed, rather than undoing anything further. What
"undo a learned mistake" has to mean instead is monotonic: each call moves
strictly further back in time than the one before it. The mechanism is a
backward walk over `history.jsonl` that skips adoptions a prior rollback
already consumed. Read the file in reverse and keep a `skip` counter: a
`"rollback"` entry increments it (one more adoption is now spoken for), an
`"adopt"` entry consumes one skip if any are owed and is otherwise the
target. This is exactly the bracket-matching a stack of live adoptions would
compute, expressed without building one, and it generalizes for free to
adopt-after-rollback (a fresh adoption pushed after an undo is itself live
and is the next thing a further rollback would undo) without a special case.

Rolling back the *first* adoption is refused with an explicit `ValueError`:
the state before it is the un-learned system, which this store does not
model, and `replaces=None` on that entry is precisely the record of "there
is nothing further back to go to."

**Decision 4 — the store holds only its own snapshots. It never writes
`knowledge/`, `routing-config.json`, or any other pre-existing repository
file.** Ticket 19 owns *versioning*; ticket 20 owns *application*. Keeping
them apart leaves this module a leaf with exactly one job — there is no
import of `learning_journal`, `learning_scoreboard`, or anything else in
this skill directory, and no function here takes a live-system path as an
argument at all. A caller cannot make this module damage a live file even by
misusing it, because there is no argument through which a live file's path
could arrive.

**Decision 5 — the target is named from a closed vocabulary
(`LearnedDocument`); no parameter anywhere accepts a filesystem path.** This
is what makes ticket 20's "no code path from the learner to the protocol
files at all, demonstrable by construction, not by a guard clause" true on
this module's side of that boundary: not that a guard rejects
`"protocol.md"`, but that there is no string parameter here through which
`"protocol.md"` could be *expressed*. `DocumentChange.document` and every
document-shaped field on `VersionEntry`/`DocumentDelta` are validated
against `LEARNED_DOCUMENTS` — `{"memory", "routing_table", "briefs"}` — and
nothing resembling a path ever crosses this module's boundary as a document
identifier.

**Seams.** One seam, matching `learning_journal.py`'s own stated seam:
`root_dir` — every path this module touches is derived from it, so it is
fully exercisable against a temporary directory. No second seam — see
Decision 4.

**This module owns no clock.** `now` is always injected, matching
`acceptance_gate.py`, `learning_scoreboard.py`, and `learning_report.py`'s
own guarantee — none of which count their injected `now` as a seam either.
Every `VersionEntry.timestamp` is stamped from the injected `now`, never
from a live clock read, so a caller testing with a fixed `now` gets back
exactly the timestamp it injected.

**Layout under `root_dir`** (see `LEARNED_STATE_RELATIVE_PATH`):

```
learned-state/
  versions/v0001/<document>     immutable snapshot of the whole state at that version
  versions/v0002/<document>
  history.jsonl                 append-only; the last entry names the current version
```

There is deliberately no `current/` directory. A second copy of the newest
snapshot would be a second source of truth that can drift from
`history.jsonl`'s own account of what is current; `current_version_dir`
resolves the current version from history instead, so there is only ever
one place that fact is recorded.

**`history.jsonl` has exactly one writer: this module.** That is a real
difference from `learning_journal.jsonl`, which many callers across this
skill directory append to and which therefore has to tolerate a torn line,
an unknown record family, or a line from a reader that predates it (see
`learning_journal.read_journal`'s extensive per-line tolerance). Nothing
outside `adopt` and `roll_back` ever appends to `history.jsonl`, so
`read_history` treats a malformed line as loud failure rather than quietly
skipping it — the same "malformed record raises" rule `learning_journal.py`
states for a record a *caller* builds wrong, applied here to a *file* only
this module ever produces. A hand-corrupted history is a bug worth stopping
on, not evidence to route around.

`_append_jsonl_locked` below duplicates `learning_journal._append_jsonl_locked`
byte-for-byte in spirit (an exclusive `fcntl` lock around one appended JSON
line) rather than importing it. These files are loaded by path rather than
as a package — every sibling module in this directory makes the same
choice, for the same reason (see `learning_journal.py`'s own docstring on
`_append_jsonl_locked`) — and Decision 4 above additionally forbids this
leaf module from importing `learning_journal` at all.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast, get_args

# The closed vocabulary of documents this store versions. See Decision 5:
# nothing outside this set is a valid `document`, ever — in particular,
# nothing path-shaped.
LearnedDocument = Literal["memory", "routing_table", "briefs"]
LEARNED_DOCUMENTS: frozenset[str] = frozenset(get_args(LearnedDocument))

# Where the whole store lives beneath an injected root. A relative `Path` so
# a caller (and `test_learned_state.py`'s `git check-ignore` check) can join
# it against either a real repository root or a temporary one.
LEARNED_STATE_RELATIVE_PATH = Path("learned-state")

_VERSIONS_DIRNAME = "versions"
_HISTORY_FILENAME = "history.jsonl"

# The two things a version transition can be. Not exported as a named
# `Literal` alias: nothing outside this module constructs a `VersionEntry`
# by hand today, so there is no second file to keep this vocabulary in step
# with the way `LearnedDocument` must stay in step with `DocumentChange`.
_VERSION_KINDS: frozenset[str] = frozenset({"adopt", "rollback"})

# A caller-supplied correlation id (ticket 20's own change identity, most
# likely). Mirrors `learning_journal.TASK_ID_RE` character-for-character —
# not imported, per Decision 4 and this module's own docstring on
# `_append_jsonl_locked` — because the shape both patterns enforce is the
# same fact stated twice: an identifier, never prose, never a path.
_CHANGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# The wire timestamp shape every sibling module in this directory emits.
# Mirrors `learning_journal.TIMESTAMP_RE`, not imported, for the same reason.
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# A sha256 hex digest — exactly what `_digest` below produces and nothing
# else, so a `DocumentDelta` built from a value this module did not itself
# compute (a truncated hash, an uppercase one, a digest from a different
# algorithm) is rejected rather than silently trusted.
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

# The exact shape `_version_dir` names a version directory with
# (`f"v{version:04d}"`, which pads to *at least* four digits and simply
# grows wider past version 9999). Used only to recognize a version directory
# on disk when computing the next version number to allocate — see
# `_highest_version_on_disk`.
_VERSION_DIRNAME_RE = re.compile(r"^v(\d{4,})$")


def _require_aware_now(now: datetime) -> None:
    """Refuse a naive `now`.

    Mirrors `learning_scoreboard._require_aware_now` /
    `acceptance_gate._require_aware_now` rather than importing either — see
    this module's docstring on why a leaf module in this directory never
    imports a sibling.
    """
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be a timezone-aware datetime, got a naive value")


def _wire_timestamp(now: datetime) -> str:
    """Render `now` in the exact wire shape every sibling module emits.

    Mirrors `acceptance_gate._wire_timestamp` / `learning_report._utc_format`.
    """
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require_str(value: object, field_name: str) -> str:
    """Reject a non-string before any further check can crash on it."""
    if not isinstance(value, str):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    return value


def _validate_choice(value: object, allowed: frozenset[str], field_name: str) -> None:
    """Enforce an enumerated vocabulary at runtime.

    `Literal` annotations are erased at runtime, so without this a
    `LearnedDocument` field is a free string wearing a type annotation.
    Mirrors `learning_journal._validate_choice`.
    """
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field_name} must be one of {sorted(allowed)}, got {value!r}")


def _validate_version_number(value: object, field_name: str) -> None:
    """A strictly positive, non-`bool` `int` — the shape of a real version.

    `bool` is an `int` subclass, so `isinstance` alone would admit
    `version=True` as version 1's evil twin.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1, got {value!r}")


def _validate_optional_version_number(value: object, field_name: str) -> None:
    """`None`, or the same shape `_validate_version_number` requires.

    `replaces=None` is not "version zero" — it is the specific, meaningful
    claim that nothing came before this version. See this module's docstring
    on Decision 3.
    """
    if value is None:
        return
    _validate_version_number(value, field_name)


def _validate_digest(value: object, field_name: str) -> None:
    """`None`, or a lowercase sha256 hex digest — the shape `_digest` produces."""
    if value is None:
        return
    text = _require_str(value, field_name)
    if not _DIGEST_RE.fullmatch(text):
        raise ValueError(f"{field_name} must match {_DIGEST_RE.pattern} or be None, got {text!r}")


def _validate_change_id(value: object, field_name: str = "change_id") -> None:
    """`None`, or a bare identifier — never prose, never a path.

    Every caller who wants a rejected value explained gets the pattern, not
    the value itself: a `change_id` is caller-composed (like
    `learning_journal.model_id`), so echoing it back is safe in principle,
    but this module has no use for that extra risk when the pattern alone
    already tells a caller exactly what is wrong.
    """
    if value is None:
        return
    text = _require_str(value, field_name)
    if not _CHANGE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} must match {_CHANGE_ID_RE.pattern}")


def _validate_timestamp(value: object, field_name: str = "timestamp") -> None:
    """The wire timestamp shape, and that it names a real instant.

    Mirrors `learning_journal._validate_timestamp`'s two-check shape: a
    string that matches `_TIMESTAMP_RE` but names no real calendar date
    (`"2026-99-99T99:99:99Z"`) is exactly as malformed as one that fails the
    regex outright.
    """
    text = _require_str(value, field_name)
    if not _TIMESTAMP_RE.fullmatch(text):
        raise ValueError(f"{field_name} must match {_TIMESTAMP_RE.pattern}, got {text!r}")
    try:
        # DTZ007: the wire format is always UTC ("Z"); parsed value discarded
        # — this function only validates.
        datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
    except ValueError as exc:
        raise ValueError(
            f"{field_name} matches {_TIMESTAMP_RE.pattern} but names no real instant: {text!r}"
        ) from exc


def _digest(content: str) -> str:
    """The sha256 hex digest over `content`'s UTF-8 bytes.

    The one place this module computes a digest, so `_DIGEST_RE` and this
    function can never drift apart.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DocumentChange:
    """One document's full new content, as a caller wants it adopted.

    Not a diff or a patch: `content` is the document's entire new text.
    `adopt` computes what actually changed (`DocumentDelta`) by comparing
    this against the document's current content — a caller never has to
    know or state that itself.
    """

    document: LearnedDocument
    content: str

    def __post_init__(self) -> None:
        _validate_choice(self.document, LEARNED_DOCUMENTS, "document")
        _require_str(self.content, "content")


@dataclass(frozen=True)
class DocumentDelta:
    """What changed for one document between the version this entry replaced
    and the version it produced.

    `before_digest` is `None` exactly when this version is the first to
    contain `document` at all — there is no "before" to digest. `after_digest`
    is `None` on a `"rollback"` entry for a document that existed in the
    version being undone but not yet in the version restored to — the
    document is not deleted, it simply has not been adopted at that point in
    history yet. A delta whose two digests are equal is not a delta at all
    (nothing changed) and is refused at construction: `adopt` and `roll_back`
    both build these only from an actual difference, so this rejection is a
    consistency check on this module's own arithmetic, not a plausible
    caller mistake.
    """

    document: LearnedDocument
    before_digest: str | None
    after_digest: str | None

    def __post_init__(self) -> None:
        _validate_choice(self.document, LEARNED_DOCUMENTS, "document")
        _validate_digest(self.before_digest, "before_digest")
        _validate_digest(self.after_digest, "after_digest")
        if self.before_digest == self.after_digest:
            raise ValueError(
                "a DocumentDelta must represent an actual change "
                f"(before_digest and after_digest were both {self.before_digest!r})"
            )


def _validate_document_deltas(value: object, field_name: str) -> None:
    """Reject anything but a tuple of real `DocumentDelta`s.

    Without this, `VersionEntry.documents` is annotated `tuple[DocumentDelta,
    ...]` but nothing at runtime stops a list of plain dicts from
    constructing — the same hole `learning_journal._validate_rounds` closes
    for `DialogueQualityRecord.rounds`.
    """
    if not isinstance(value, tuple):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"{field_name} must be a tuple, got {type(value).__name__}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, DocumentDelta):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
                f"{field_name}[{index}] must be a DocumentDelta, got {type(item).__name__}"
            )


@dataclass(frozen=True)
class VersionEntry:
    """One `history.jsonl` line: a version transition and what it changed.

    `version` is the snapshot that is current *after* this transition;
    `replaces` is the version that was current immediately before it
    (`None` only for the very first adoption ever). Both `"adopt"` and
    `"rollback"` entries use this same pair, symmetrically: an adoption
    moves the pointer forward to a version it just wrote, a rollback moves
    it backward to a version that already existed. "Every version records
    what changed and what it replaced" is exactly `documents` plus
    `replaces`.

    No field defaults to anything, `timestamp` included: this module owns
    no clock (see the module docstring's no-clock guarantee), so a default
    that silently read a live clock would be exactly the leak that
    guarantee forbids. Every `VersionEntry` this module builds passes all
    six fields explicitly, and the one field a clock could ever supply —
    `timestamp` — is always rendered from the caller's own injected `now`.
    """

    kind: Literal["adopt", "rollback"]
    version: int
    replaces: int | None
    documents: tuple[DocumentDelta, ...]
    change_id: str | None
    timestamp: str

    def __post_init__(self) -> None:
        _validate_choice(self.kind, _VERSION_KINDS, "kind")
        _validate_version_number(self.version, "version")
        _validate_optional_version_number(self.replaces, "replaces")
        _validate_document_deltas(self.documents, "documents")
        _validate_change_id(self.change_id)
        _validate_timestamp(self.timestamp)


# --- layout ---


def _learned_state_dir(root_dir: Path) -> Path:
    return root_dir / LEARNED_STATE_RELATIVE_PATH


def _versions_dir(root_dir: Path) -> Path:
    return _learned_state_dir(root_dir) / _VERSIONS_DIRNAME


def _version_dir(root_dir: Path, version: int) -> Path:
    return _versions_dir(root_dir) / f"v{version:04d}"


def _history_path(root_dir: Path) -> Path:
    return _learned_state_dir(root_dir) / _HISTORY_FILENAME


def _highest_version_on_disk(root_dir: Path) -> int:
    """The highest version number among `vNNNN` directories actually present
    under `versions/`, or `0` if there are none (including when `versions/`
    itself does not exist yet).

    Lets `adopt` allocate past an orphaned snapshot a crashed adoption left
    behind instead of colliding with it — see the module docstring's
    Decision 2 for the full reasoning and its known limits.
    """
    versions_dir = _versions_dir(root_dir)
    if not versions_dir.is_dir():
        return 0
    highest = 0
    for entry in versions_dir.iterdir():
        if not entry.is_dir():
            continue
        match = _VERSION_DIRNAME_RE.fullmatch(entry.name)
        if match is None:
            continue
        highest = max(highest, int(match.group(1)))
    return highest


# --- snapshot read/write ---


def _read_documents(directory: Path) -> dict[str, str]:
    """Every document present in `directory`, keyed by name.

    Iterates the closed `LEARNED_DOCUMENTS` vocabulary rather than the
    directory's own listing, so a stray file (a `.DS_Store`, a hand-dropped
    editor swap file) is silently invisible to this store instead of being
    read as document content or crashing on a decode error. `directory` may
    not exist at all — every `Path.is_file()` check below is simply `False`
    for a missing directory, no exception to catch.
    """
    documents: dict[str, str] = {}
    for name in sorted(LEARNED_DOCUMENTS):
        path = directory / name
        if path.is_file():
            documents[name] = path.read_bytes().decode("utf-8")
    return documents


def _read_snapshot_contents(root_dir: Path, version: int) -> dict[str, str]:
    return _read_documents(_version_dir(root_dir, version))


def _write_snapshot(root_dir: Path, version: int, documents: Mapping[str, str]) -> None:
    """Write `documents` as version `version`'s immutable snapshot, raising
    `ValueError` — never a bare `FileExistsError` — when that version's
    directory name is already occupied; see the module docstring's Decision
    2 for why that is reachable and why the occupying entry is never removed
    automatically.
    """
    directory = _version_dir(root_dir, version)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(
            f"cannot adopt version {version}: {directory} already exists. "
            "Something already occupies this version's directory name, so "
            "the store cannot allocate past it and a retry will collide "
            "identically. Nothing is removed automatically. To decide what "
            f"to do, check whether history.jsonl names version {version}. "
            "If it does, the occupant is a real adopted snapshot: do not "
            "delete it — the fault is in this module's version numbering, "
            "and only a code change fixes it. If it does not, the occupant "
            "is a stray entry (a file, or a name differing only in case) "
            "and removing it lets the store allocate again."
        ) from exc
    for document, content in documents.items():
        (directory / document).write_bytes(content.encode("utf-8"))


def _diff_snapshots(before: Mapping[str, str], after: Mapping[str, str]) -> tuple[DocumentDelta, ...]:
    """The `DocumentDelta`s between two full document maps, sorted by document.

    One function serves both `adopt` (comparing the prior version to the
    one just written) and `roll_back` (comparing the version being undone to
    the one restored to) — the diff is symmetric, and the two callers differ
    only in which snapshot they hand in as `before` and which as `after`. A
    document present in only one side yields a delta with `None` on the
    other; a document present in both with identical content yields no
    delta at all, which is exactly how an `adopt` call that carries a
    document forward unchanged produces zero entries for it.
    """
    deltas: list[DocumentDelta] = []
    for document in sorted(set(before) | set(after)):
        before_content = before.get(document)
        after_content = after.get(document)
        if before_content == after_content:
            continue
        deltas.append(
            DocumentDelta(
                document=cast("LearnedDocument", document),
                before_digest=_digest(before_content) if before_content is not None else None,
                after_digest=_digest(after_content) if after_content is not None else None,
            )
        )
    return tuple(deltas)


# --- history read/write ---


def _append_jsonl_locked(path: Path, record: dict[str, object]) -> None:
    """Append one JSON record to `path` under an exclusive advisory lock.

    Duplicates `learning_journal._append_jsonl_locked`'s locking discipline
    rather than importing it. See this module's docstring for why.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(line)
            stream.flush()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


# The wire keys `_entry_to_mapping` always writes for a history line, and
# for each item in its nested `documents` list — always, whether or not a
# value is `None`. Used only to check a raw wire mapping *has* a key before
# `_mapping_to_entry` reads it; a `None` value is a legitimate answer for
# several of these fields (`replaces`, `change_id`, a delta's own digests)
# and is not what this checks for.
_ENTRY_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"kind", "version", "replaces", "documents", "change_id", "timestamp"}
)
_DOCUMENT_DELTA_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"document", "before_digest", "after_digest"}
)


def _check_required_keys(mapping: Mapping[str, object], required: frozenset[str], kind: str) -> None:
    """Raise if `mapping` — a raw wire object, before any subscript touches
    it — lacks a key `_entry_to_mapping` always writes for `kind`.

    Mirrors `learning_journal._check_required_keys` in spirit, not by
    import — see this module's docstring on why a leaf module in this
    directory never imports a sibling. `history.jsonl` has exactly one
    writer (this module itself), so a missing key here means a hand-
    corrupted line, and this module's one rejection contract is `ValueError`
    — not whatever incidental `KeyError`/`TypeError` a bare `mapping["..."]`
    happens to raise once the missing or wrong-shaped key is reached.
    """
    missing = required - mapping.keys()
    if missing:
        raise ValueError(f"{kind} missing required wire key(s): {sorted(missing)}")


def _entry_to_mapping(entry: VersionEntry) -> dict[str, object]:
    return {
        "kind": entry.kind,
        "version": entry.version,
        "replaces": entry.replaces,
        "documents": [
            {
                "document": delta.document,
                "before_digest": delta.before_digest,
                "after_digest": delta.after_digest,
            }
            for delta in entry.documents
        ],
        "change_id": entry.change_id,
        "timestamp": entry.timestamp,
    }


def _mapping_to_entry(mapping: Mapping[str, object]) -> VersionEntry:
    """Rehydrate one raw wire object (one parsed `history.jsonl` line) into
    a `VersionEntry`.

    `_check_required_keys` runs first, on `mapping` itself and then on each
    item of its `documents` list, before any `[...]` subscript below can
    raise the wrong exception type for a hand-corrupted line — a `KeyError`
    for a missing top-level key, or a `TypeError` for a `documents` item
    that is not an object at all (`item["document"]` on a bare string).
    Both are wrong under this module's one rejection contract: every
    validator in this file raises `ValueError`, and a caller catching that
    contract should not have to also catch two more exception types this
    reader alone happens to produce.
    """
    _check_required_keys(mapping, _ENTRY_REQUIRED_KEYS, "history entry")

    raw_documents = mapping["documents"]
    if not isinstance(raw_documents, list):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"documents must be a list on the wire, got {type(raw_documents).__name__}"
        )
    documents = []
    for index, item in enumerate(raw_documents):
        if not isinstance(item, Mapping):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
                f"documents[{index}] must be an object on the wire, got {type(item).__name__}"
            )
        _check_required_keys(item, _DOCUMENT_DELTA_REQUIRED_KEYS, f"documents[{index}]")
        documents.append(
            DocumentDelta(
                document=cast("LearnedDocument", item["document"]),
                before_digest=cast("str | None", item["before_digest"]),
                after_digest=cast("str | None", item["after_digest"]),
            )
        )

    return VersionEntry(
        kind=cast('Literal["adopt", "rollback"]', mapping["kind"]),
        version=cast(int, mapping["version"]),
        replaces=cast("int | None", mapping["replaces"]),
        documents=tuple(documents),
        change_id=cast("str | None", mapping["change_id"]),
        timestamp=cast(str, mapping["timestamp"]),
    )


def _append_history(root_dir: Path, entry: VersionEntry) -> None:
    _append_jsonl_locked(_history_path(root_dir), _entry_to_mapping(entry))


def read_history(root_dir: Path) -> tuple[VersionEntry, ...]:
    """Every version transition ever recorded beneath `root_dir`, in order.

    A missing `history.jsonl` reads as `()`, not an error — an empty store
    is a legitimate starting state, not damage. A shared advisory lock is
    held for the read, mirroring `_append_jsonl_locked`'s exclusive one, so a
    concurrent write can never be read as a torn line.

    Every other failure — malformed JSON, a line failing `VersionEntry`'s
    own validation — raises. This file has exactly one writer (this module,
    via `adopt` and `roll_back`); see the module docstring for why that
    makes loud failure the right call here where `learning_journal.py`
    instead tolerates damage line by line.
    """
    path = _history_path(root_dir)
    try:
        stream = open(path, "rb")  # noqa: SIM115 - narrow except below needs the two-step form
    except FileNotFoundError:
        return ()
    with stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            raw_lines = stream.readlines()
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    entries = []
    for raw_line in raw_lines:
        text = raw_line.decode("utf-8").strip()
        if not text:
            continue
        entries.append(_mapping_to_entry(json.loads(text)))
    return tuple(entries)


def current_version_dir(root_dir: Path) -> Path | None:
    """The directory of whichever version is current beneath `root_dir`.

    Resolved from `history.jsonl`'s last entry, never from a `current/`
    directory — see the module docstring's "Layout" section on why there is
    no such directory to resolve. `None` when no version has ever been
    adopted.
    """
    history = read_history(root_dir)
    if not history:
        return None
    return _version_dir(root_dir, history[-1].version)


def read_current(root_dir: Path) -> dict[LearnedDocument, str]:
    """The full content of every document the current version holds.

    `{}` when no version has ever been adopted. The `cast` below is sound
    rather than merely convenient: every key this function can possibly
    return came from `_read_documents` iterating `LEARNED_DOCUMENTS` itself,
    so every key is already a member of that vocabulary by construction.
    """
    directory = current_version_dir(root_dir)
    if directory is None:
        return {}
    return cast("dict[LearnedDocument, str]", _read_documents(directory))


def _validate_changes(changes: object) -> tuple[DocumentChange, ...]:
    """A non-empty sequence of `DocumentChange`, naming each document once.

    `list`/`tuple` only — not "anything iterable" — for the same reason
    `learning_journal._validate_tuple` gives: a bare `str` is iterable, and a
    per-item loop over it would inspect characters instead of rejecting the
    value outright. Two documents named in one call is rejected too: nothing
    downstream defines which of the two would win, and silently picking
    "last one wins" would make that ambiguity a caller's problem to
    rediscover instead of this module's problem to refuse.
    """
    if not isinstance(changes, (list, tuple)):
        raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
            f"changes must be a list or tuple, got {type(changes).__name__}"
        )
    if len(changes) == 0:
        raise ValueError("changes must not be empty — adopting nothing is not a version")
    seen: set[str] = set()
    for index, change in enumerate(changes):
        if not isinstance(change, DocumentChange):
            raise ValueError(  # noqa: TRY004 - one rejection contract; see learning_journal.py
                f"changes[{index}] must be a DocumentChange, got {type(change).__name__}"
            )
        if change.document in seen:
            raise ValueError(
                f"changes names document {change.document!r} more than once in one call"
            )
        seen.add(change.document)
    return tuple(changes)


def adopt(
    changes: Sequence[DocumentChange],
    *,
    root_dir: Path,
    now: datetime,
    change_id: str | None = None,
) -> VersionEntry:
    """Adopt `changes`, writing a new version and returning its `VersionEntry`.

    Every document not named in `changes` is carried forward into the new
    version byte-for-byte from whichever version was current — see the
    module docstring's Decision 2 on why that carry-forward is what makes a
    later `roll_back` exact rather than approximate. `documents` on the
    returned entry names only what actually differs, which is only ever the
    documents this call touched (see `_diff_snapshots`).

    All validation — `now`'s awareness, `changes`' shape, `change_id`'s
    shape, and whether `changes` would actually change anything — runs
    before anything reaches disk, so a rejected call writes nothing: no new
    version directory, no new `history.jsonl` line. Content identical to
    what the current version already holds is refused for the same reason
    an empty `changes` list is (see `_validate_changes`): adopting nothing
    is not a version, whether that nothing is expressed as zero changes or
    as changes that cancel out against what is already current.

    The new version number is one past the highest version number this
    store has ever used, on disk or in history (see
    `_highest_version_on_disk`) — see the module docstring's Decision 2 for
    why it is computed that way and what it guards against.
    """
    _require_aware_now(now)
    validated_changes = _validate_changes(changes)
    _validate_change_id(change_id)

    history = read_history(root_dir)
    current_version = history[-1].version if history else None
    highest_version_used = max(
        max((entry.version for entry in history), default=0),
        _highest_version_on_disk(root_dir),
    )

    previous_documents = (
        _read_snapshot_contents(root_dir, current_version) if current_version is not None else {}
    )
    new_documents = dict(previous_documents)
    for change in validated_changes:
        new_documents[change.document] = change.content

    deltas = _diff_snapshots(previous_documents, new_documents)
    if not deltas:
        raise ValueError(
            "changes describe no actual difference from the current version — "
            "adopting nothing is not a version"
        )

    new_version = highest_version_used + 1
    _write_snapshot(root_dir, new_version, new_documents)

    entry = VersionEntry(
        kind="adopt",
        version=new_version,
        replaces=current_version,
        documents=deltas,
        change_id=change_id,
        timestamp=_wire_timestamp(now),
    )
    _append_history(root_dir, entry)
    return entry


def roll_back(*, root_dir: Path, now: datetime, change_id: str | None = None) -> VersionEntry:
    """Undo the most recent adoption that has not already been undone.

    See the module docstring's Decision 3 for the backward-walk algorithm
    and why "restore version N-1" was rejected in its favor. Three distinct
    refusals, each naming why:

    - No version has ever been adopted: nothing to roll back at all.
    - Every adoption this history records has already been undone by a
      prior rollback (unreachable through this module's own two public
      writers — a successful rollback can never itself consume the first
      adoption, so at least one live adoption always remains — but
      `history.jsonl` has exactly one writer only as long as nothing else
      ever hand-edits it; this refusal is what a corrupted file gets
      instead of an `IndexError` or a silent wrong answer).
    - The one live adoption left is the very first one ever made: refused
      per Decision 3, naming the reason rather than a bare index error.

    Creates no new version directory — only `current_version_dir`'s answer
    changes, by appending one more `history.jsonl` entry.

    `change_id` gets no early validation call here the way `adopt` gives it
    one: unlike `adopt`, nothing in `roll_back` writes anything before the
    `VersionEntry` below is constructed, and that construction already
    validates `change_id` (`VersionEntry.__post_init__` calls
    `_validate_change_id` unconditionally). A second call up here would
    produce the identical exception, from the identical function, with the
    identical message — a duplicate with no `writes nothing` guarantee left
    for it to protect, since `_append_history` is the first and only write
    and it happens strictly after that construction succeeds.
    """
    _require_aware_now(now)

    history = read_history(root_dir)
    if not history:
        raise ValueError("cannot roll back: no version has ever been adopted")

    current_version = history[-1].version

    skip = 0
    target_entry: VersionEntry | None = None
    for entry in reversed(history):
        if entry.kind == "rollback":
            skip += 1
            continue
        if skip > 0:
            skip -= 1
            continue
        target_entry = entry
        break

    if target_entry is None:
        raise ValueError("cannot roll back: every adoption has already been undone")
    if target_entry.replaces is None:
        raise ValueError(
            "cannot roll back the first adoption — the state before it is the "
            "un-learned system, which this store does not model"
        )

    target_version = target_entry.replaces
    current_documents = _read_snapshot_contents(root_dir, current_version)
    target_documents = _read_snapshot_contents(root_dir, target_version)

    entry = VersionEntry(
        kind="rollback",
        version=target_version,
        replaces=current_version,
        documents=_diff_snapshots(current_documents, target_documents),
        change_id=change_id,
        timestamp=_wire_timestamp(now),
    )
    _append_history(root_dir, entry)
    return entry


__all__ = [
    "LEARNED_DOCUMENTS",
    "LEARNED_STATE_RELATIVE_PATH",
    "DocumentChange",
    "DocumentDelta",
    "LearnedDocument",
    "VersionEntry",
    "adopt",
    "current_version_dir",
    "read_current",
    "read_history",
    "roll_back",
]
