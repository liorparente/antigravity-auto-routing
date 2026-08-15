# 19 — Versioned learned state, one-step rollback

**What to build:** Every adopted change becomes a git-tracked version of the learned state, so that
undoing a learned mistake is always one step and never an archaeology exercise. This is what makes it
safe for the system to change itself: mistakes are cheap.

The prior version must be recoverable exactly — a rollback that "mostly" restores the previous state
is the failure mode this ticket exists to prevent.

**Blocked by:** 12

**Status:** done — commit `b0a8946` (`learned_state.py`, `test_learned_state.py`; the five settled
design decisions are restated in the module's own docstring). "Git-tracked" resolved to "sits under a
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
