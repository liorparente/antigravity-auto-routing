# 19 — Versioned learned state, one-step rollback

**What to build:** Every adopted change becomes a git-tracked version of the learned state, so that
undoing a learned mistake is always one step and never an archaeology exercise. This is what makes it
safe for the system to change itself: mistakes are cheap.

The prior version must be recoverable exactly — a rollback that "mostly" restores the previous state
is the failure mode this ticket exists to prevent.

**Blocked by:** 12

**Status:** done — commit `b0a8946` (`learned_state.py`, `test_learned_state.py`; the five settled
design decisions are restated in the module's own docstring), plus nine fix commits from a seventeen-
round `/iterative-fix-review` loop that ends at `5921ab2`. Those commits are why the module is far
larger than this ticket's six criteria suggest, and the reason is worth stating here rather than
leaving to be reverse-engineered: the loop kept reproducing **silent** failures of criterion 4 ("the
state after a rollback matches the prior version exactly, byte for byte"). Two concurrent `adopt`
calls lost a committed write in six trials of six; a snapshot deleted while its history line survived
made `read_current` answer `{}` and the next `adopt` carry that emptiness forward; a version
directory the process could not list did the same. Each was a rollback that "mostly" restored the
previous state — the failure mode this ticket's opening paragraph names — arriving by a route the
criteria do not enumerate. The resulting rule, and the one to read before adding code here, is in the
module docstring: every question this store asks the filesystem is asked in a form that can *fail*,
because `Path.is_dir()`/`.exists()` swallow `PermissionError` and answer `False`, and a call that
cannot fail is an assumption rather than a question. "Git-tracked" resolved to "sits under a
tracked directory and is never gitignored" — checked against the repository's real `.gitignore`, not
merely asserted — with the module never
shelling out to `git` itself, since a worker sandbox that locks `.git/` would deadlock a `git commit`
on the adoption path. A version, once written by `adopt`, is never rewritten: the next version number
is always one past the highest ever used (not merely the current one), so an adoption made after a
rollback can never collide with — and, by construction (`Path.mkdir(..., exist_ok=False)`), can never
silently overwrite — a version a prior rollback only stopped pointing at. `roll_back` undoes the most
recent adoption not already undone via a backward walk over `history.jsonl` that skips adoptions a
prior rollback consumed, refusing outright to undo the very first adoption (the un-learned system is
not a state this store models). The store is a leaf: no import of any sibling module in this skill
directory, and no parameter through which a live repository file — `routing-config.json`, a protocol
file — could ever be named; ticket 20 owns applying a version, this ticket owns only versioning it.

- [x] Adopting a change writes a new version of the learned state, tracked in git.
- [x] Every version records what changed and what it replaced.
- [x] Rolling back is a single operation.
- [x] The state after a rollback matches the prior version exactly, byte for byte.
- [x] Version writes happen beneath the injected root directory; tests never touch the real
      repository state.
- [x] Tests cover adopt, roll back, and adopt-adopt-roll-back-once.
