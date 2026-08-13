# 23 — All three harnesses learn as one

**What to build:** Adopted learned state propagates across harnesses through the existing install
mechanism, exactly like every other piece of shared configuration today. A lesson learned in one
environment must not be a lesson only that environment has.

`install.sh` already stages and atomically synchronizes shared configuration with marker preflight,
one-time backups, and rollback of every touched file on failure. Learned state joins that mechanism;
it does not get a second, parallel one.

**Blocked by:** 20

**Status:** ready-for-agent

- [ ] Adopted learned state is synchronized by the existing install mechanism.
- [ ] The install's atomicity holds: a failure mid-sync rolls back every touched file, learned state
      included.
- [ ] Existing install behavior for the protocol and skill files is unchanged.
- [ ] A test covers a successful sync and a failing one that rolls back.
