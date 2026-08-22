# 05 — Multi-Harness Protocol Synchronization in install.sh

**What to build:** Update install.sh and test_routing.py to synchronize the generalized protocol sentinel across AGENTS.md and CLAUDE.md without duplicating global rules, ensuring full test closure on all managed files.

**Blocked by:** 04 — Generalize Worker Mode Token & Harness-Neutral Invocations

**Status:** ready-for-agent

- [ ] Update install.sh to stage and synchronize the updated protocol.md across AGENTS.md and CLAUDE.md.
- [ ] Update MANAGED_FILES in install.sh if any new modules are added.
- [ ] Verify test_routing.py's ManagedFileClosureTests passes cleanly.
- [ ] Run ./install.sh and verify clean atomic synchronization across all target directories.
