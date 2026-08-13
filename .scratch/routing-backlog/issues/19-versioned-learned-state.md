# 19 — Versioned learned state, one-step rollback

**What to build:** Every adopted change becomes a git-tracked version of the learned state, so that
undoing a learned mistake is always one step and never an archaeology exercise. This is what makes it
safe for the system to change itself: mistakes are cheap.

The prior version must be recoverable exactly — a rollback that "mostly" restores the previous state
is the failure mode this ticket exists to prevent.

**Blocked by:** 12

**Status:** ready-for-agent

- [ ] Adopting a change writes a new version of the learned state, tracked in git.
- [ ] Every version records what changed and what it replaced.
- [ ] Rolling back is a single operation.
- [ ] The state after a rollback matches the prior version exactly, byte for byte.
- [ ] Version writes happen beneath the injected root directory; tests never touch the real
      repository state.
- [ ] Tests cover adopt, roll back, and adopt-adopt-roll-back-once.
