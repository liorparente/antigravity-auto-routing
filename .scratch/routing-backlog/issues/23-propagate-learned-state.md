# 23 — All three harnesses learn as one

**What to build:** Adopted learned state propagates across harnesses through the existing install
mechanism, exactly like every other piece of shared configuration today. A lesson learned in one
environment must not be a lesson only that environment has.

`install.sh` already stages and atomically synchronizes shared configuration with marker preflight,
one-time backups, and rollback of every touched file on failure. Learned state joins that mechanism;
it does not get a second, parallel one.

**Blocked by:** 20

**Status:** done — implemented in `install.sh` (preflight resolution via
`learned_state.current_version_dir`, staging, and atomic sync of `learned-state/` alongside
`MANAGED_FILES`) and tested in `test_routing.py`'s `LearnedStatePropagationTests` (4/4 tests
passing). `install.sh` resolves the current version by shelling out to `python3` with `SRC_DIR` on
`sys.path`, importing `learned_state`, and calling `current_version_dir(root_dir=Path(SCRIPT_DIR))`
— `SCRIPT_DIR` (not `SRC_DIR`) because `learned-state/` is a git-tracked sibling of `install.sh`
itself, not part of `skills/worker-routing/`. A `None` result (nothing ever adopted) proceeds
cleanly with nothing staged; a `ValueError` (a damaged store) fails preflight before any target is
touched. When a version is resolved, `history.jsonl` and every version directory under `versions/`
are staged and then synced into each `TARGET_DIRS` entry's `learned-state/` through the same
`atomic_copy`/`write_count`/`AUTO_ROUTING_FAIL_AFTER_WRITES` machinery `MANAGED_FILES` already uses,
so a fault mid-sync rolls back through the identical `snapshot_file`/`rollback` transaction.

**Note:** this ticket propagates whatever `learned_state`'s *current* memory version holds across
harnesses; it does not decide how that version accumulates across separate runs in the first place
(each `apply_memory_lesson` call today replaces memory wholesale rather than merging with a prior
run's lessons — see issue 33). Issue 33's resolution changes what content this sync ships, not
whether or how it ships it.

- [x] Adopted learned state is synchronized by the existing install mechanism.
- [x] The install's atomicity holds: a failure mid-sync rolls back every touched file, learned state
      included.
- [x] Existing install behavior for the protocol and skill files is unchanged.
- [x] A test covers a successful sync and a failing one that rolls back.
