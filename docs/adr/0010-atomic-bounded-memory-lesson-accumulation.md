# ADR 0010: Atomic, Bounded Accumulation of Learned Memory Lessons

## Status
Accepted (2026-08-17)

## Context
Ticket 22 implemented two learning cadences — `learner_worker.run_session_end_light` (per session)
and `run_weekly_deep` (per week) — each of which folds every memory lesson *one call* produces into a
single `risk_tiered_application.apply_memory_lesson` call before adopting it via `learned_state.adopt`.
That consolidation is intra-run only: `learned_state.adopt` replaces the `"memory"` document's content
wholesale, so a second cadence run's `apply_memory_lesson` call overwrote the first run's lessons
rather than merging with them. Because both cadences are meant to run indefinitely over a project's
lifetime, only the most recent run's lessons ever survived — everything institutional memory was
supposed to accumulate from earlier sessions or weeks was silently dropped the next time either
cadence fired. Ticket 33 (`.scratch/routing-backlog/issues/33-accumulate-memory-lessons-across-runs.md`)
tracked this as `needs-design`, blocked on three open questions: where accumulation should live, how
growth should be bounded, and whether `learned_state`'s version history already made an explicit merge
unnecessary (it does not — it lets a human roll back, but nothing reconstructs the union of all prior
lessons from it automatically).

A Planner-Critic advisory consultation (`.scratch/planning_debate.md`, "Ticket 33" section) resolved the
design across two rounds. Round 1 proposed ownership and a first-cut merge/dedup/prune scheme with a
separate `preview_memory_lesson_merge` function for anti-flapping. Round 2 Critic review found the
read-merge-write sequence unprotected against concurrent writers, the preview function racy by
construction, the parsing grammar underspecified for multiline and legacy content, and the bound
leaking retention policy through a per-call keyword; all four were revised and the debate reached
consensus.

## Decision

1. **Ownership: `risk_tiered_application.apply_memory_lesson`.** Not `learned_state.py`, which must stay
   content-agnostic — it never learns what a lesson is, only ever compares and stores opaque strings
   (see that module's Decision 4). Not `learner_worker.py`, which is structurally forbidden from
   importing `learned_state` (`test_learner_worker.py`'s
   `test_module_never_imports_learned_state`) and would need to read current state before proposing
   regardless. `apply_memory_lesson` already sits at the one seam both cadences call through, so
   extending it needs zero caller-side changes beyond passing one new optional keyword.

2. **Atomicity via a content-agnostic compare-and-swap precondition on `learned_state.adopt`.**
   `adopt` gains `expected_current: Mapping[LearnedDocument, str | None] | None = None`. Inside the
   existing `_exclusive_store_lock` critical section, immediately after `previous_documents` is read,
   `adopt` checks that every document named in `expected_current` still holds exactly the content
   given (`None` meaning "must be absent"); a mismatch raises a `ValueError` with a message distinct
   from the existing "no actual difference" refusal, so a caller can tell a stale-read conflict apart
   from a genuine no-op. `expected_current=None` (the default) skips the check entirely, so every
   existing caller of `adopt` is unaffected. The comparison is opaque string equality — never lesson
   parsing — so this stays inside `learned_state`'s content-agnostic contract.

   `apply_memory_lesson` wraps its read-merge-write in a bounded retry loop (`_MAX_MERGE_RETRIES = 8`):
   each iteration reads `learned_state.read_current`, computes a merge candidate, and adopts it with
   `expected_current={"memory": existing}`. A writer that adopts or rolls back between the read and the
   adopt call is detected as a `ValueError` (translated internally to a private `_StaleReadConflict`)
   rather than silently overwritten; the loop retries against a fresh read. Eight retries is generous
   for real contention, which is expected to be rare — both cadences run per-session and per-week
   against a given `root_dir`, not concurrently in normal operation. Exceeding the budget raises
   `RuntimeError` rather than silently dropping a lesson.

3. **`reject_if_candidate_digest` replaces a two-call preview function.** `apply_memory_lesson` gains
   `reject_if_candidate_digest: str | None = None` instead of a separate read-only
   `preview_memory_lesson_merge` function. A two-call preview-then-apply interface is shallow and racy:
   a concurrent change could make the preview stale by the time the real call adopted. The rejection
   check instead runs against the actual merged candidate, inside the same atomic step: if
   `reject_if_candidate_digest` is given and matches the merged candidate's digest, `apply_memory_lesson`
   returns a `rejected` `TierOutcome` without adopting anything. `run_weekly_deep` passes
   `reverted_before_digests.get("memory")` straight through — the digest of whatever memory content its
   own attributable-regression revert just undid — so a run can never re-accumulate, by merge, exactly
   the content it just proved regressive. The `routing_table`/`briefs` tiers are untouched: they are
   wholesale-replace tiers where the proposed content already is the adoption candidate, so their
   existing pre-adoption `_flapping_guard` digest comparison in `learner_worker.py` remains correct as
   is.

4. **Parsing grammar: canonical bullets, indented continuations, opaque legacy fallback, and rejection
   of malformed mixtures.** A memory document is a sequence of entries. An entry is one `"- "`-prefixed
   line, optionally followed by indented continuation lines (any line whose first character is
   whitespace), concatenated by `"\n"` into that entry's text. Universal newlines are handled by
   `str.splitlines()` before any grammar check, so CRLF input round-trips identically to LF. Parsing
   either the stored document or newly proposed content runs the same strict scanner, with three
   outcomes:
   - Strict parse succeeds → a tuple of entries.
   - Strict parse fails, and no line anywhere starts with `"- "` → the whole text is one opaque entry,
     verbatim. This covers both today's plain-string callers and any pre-ticket stored content —
     nothing existing is lost or corrupted, and it is never split at internal newlines.
   - Strict parse fails, but at least one line does start with `"- "` → `ValueError`, naming the
     offending line. Partial bullet intent with a structurally invalid remainder (e.g. `"- A\nunindented
     continuation"`) is a bug to surface, not to silently reinterpret.

   Blank content (empty after trim) always raises `ValueError` before any read happens — never creates
   an empty version. Serialization always emits canonical form (`"- "` first line, `"  "`-indented
   continuation lines) for every entry, whether it originated as a canonical bullet or an opaque legacy
   blob — so a legacy entry, once merged once, round-trips as exactly one entry on every subsequent
   parse. Merge order is existing entries first, then new, deduped by exact case-sensitive string
   equality on each entry's full (possibly multiline) text, first occurrence wins position.

5. **A fixed, document-wide bound: `DEFAULT_MAX_MEMORY_LESSONS = 200`.** A module constant, not a
   per-call keyword — a per-call override would leak retention policy through the interface and let two
   callers apply inconsistent caps to the same document. FIFO eviction of the oldest-positioned entries
   once the merged, deduped count exceeds the bound — plain FIFO, not move-to-end-on-reaffirmation, for
   v1; a twice-taught lesson is not retained longer than a once-taught one. `TierOutcome.reason` reports
   both merge and eviction through its existing free-form field (e.g. `"merged 2 new lesson(s); pruned 1
   oldest to stay within max_lessons=200"`).

Idempotency still falls out for free: if merge, dedup, and prune together produce a string
byte-identical to what is already current, `_adopt_with_idempotency`'s existing no-op path fires exactly
as it does today — no special case is needed, since the comparison is on the final candidate string
regardless of how the retry loop arrived at it.

## Consequences

- **Positive**:
  - Memory lessons genuinely accumulate across sessions and weeks instead of the most recent run
    silently discarding every prior one — the gap Ticket 33 exists to close.
  - The read-merge-write is race-safe under real (low) contention without `learned_state` importing
    anything that understands lesson structure, preserving that module's content-agnostic guarantee.
  - Growth is bounded by a single, predictable, document-wide policy rather than left unbounded or
    governed inconsistently per caller.
  - The anti-flapping check now examines what is actually adopted (the merged candidate), closing a gap
    where a raw-content comparison could have missed a flapping candidate reconstructed by merge.
- **Negative / Trade-offs**:
  - `apply_memory_lesson` is no longer a single filesystem round trip in the contended case; sustained
    concurrent contention (more than `_MAX_MERGE_RETRIES` collisions) raises `RuntimeError` rather than
    blocking indefinitely, which is deliberate but means a caller under real contention must handle that
    failure rather than assume this call always succeeds.
  - FIFO eviction with no reaffirmation-based retention means a lesson taught once and never repeated
    ages out of the bound exactly as fast as one repeatedly reaffirmed; a future ticket could revisit
    move-to-end semantics if that proves to matter in practice.
  - A pre-ticket stored memory document with no `"- "` bullets is preserved as one opaque entry rather
    than retroactively split — correct for round-trip safety, but it means a legacy blob's internal
    structure (if any was implied by its formatting) is never recovered as multiple entries.
