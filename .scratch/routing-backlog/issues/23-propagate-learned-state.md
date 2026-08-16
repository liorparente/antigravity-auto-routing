# 23 — All three harnesses learn as one

**What to build:** Adopted learned state propagates across harnesses through the existing install
mechanism, exactly like every other piece of shared configuration today. A lesson learned in one
environment must not be a lesson only that environment has.

`install.sh` already stages and atomically synchronizes shared configuration with marker preflight,
one-time backups, and rollback of every touched file on failure. Learned state joins that mechanism;
it does not get a second, parallel one.

**Blocked by:** 20

**Status:** ready-for-agent. `learned_state.current_version_dir` returns a `pathlib.Path | None` —
`None` when nothing has ever been adopted, which the sync must handle rather than assume — and
`install.sh` is pure bash with nothing bridging Python to shell yet; this ticket owns resolving the
current version from a shell script.

**Note:** this ticket propagates whatever `learned_state`'s *current* memory version holds across
harnesses; it does not decide how that version accumulates across separate runs in the first place
(each `apply_memory_lesson` call today replaces memory wholesale rather than merging with a prior
run's lessons — see issue 33). Issue 33's resolution changes what content this sync ships, not
whether or how it ships it.

- [ ] Adopted learned state is synchronized by the existing install mechanism.
- [ ] The install's atomicity holds: a failure mid-sync rolls back every touched file, learned state
      included.
- [ ] Existing install behavior for the protocol and skill files is unchanged.
- [ ] A test covers a successful sync and a failing one that rolls back.
