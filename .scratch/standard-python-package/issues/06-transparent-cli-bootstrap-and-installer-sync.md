# 06 — Transparent CLI Bootstrap & Multi-Harness Installer Sync

**GitHub Issue:** [#15](https://github.com/liorparente/antigravity-auto-routing/issues/15)
**What to build:** Add transparent CLI bootstrap to `routing_check.py`, update `install.sh` and `uninstall.sh` to package and clean `__init__.py` across active harnesses, and verify the full 1,010+ test suite and Council Review.

**Blocked by:** [05 — Learning Journal & State Store Sibling Loader Elimination (#14)](https://github.com/liorparente/antigravity-auto-routing/issues/14)

**Status:** ready-for-agent

- [ ] Add transparent package resolution fallback to `routing_check.py`.
- [ ] Verify `routing_check.py` and `routing-audit.sh` run directly from CLI without errors.
- [ ] Update `install.sh` and `uninstall.sh` to synchronize `__init__.py`.
- [ ] Execute complete suite `python3 -m unittest discover` (1,010+ tests passing) and verify Council Review gate.
