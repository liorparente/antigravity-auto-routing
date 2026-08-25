# 40 — Universal Python Module Globbing in Installer (`install.sh` & `uninstall.sh`)

**GitHub Issue:** [#16](https://github.com/liorparente/antigravity-auto-routing/issues/16)

* GitHub Issue: [#9](https://github.com/liorparente/antigravity-auto-routing/issues/9)
* Spec: [docs/specs/0008-debate-engine-modular-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0008-debate-engine-modular-decomposition.md)

**Blocked by:** 39 — Slim Facade Integration & 100% Test Compatibility

**Status:** complete

- [x] Update `install.sh` to copy all `skills/worker-routing/*.py` files into target directories dynamically.
- [x] Update `uninstall.sh` to cleanly remove all installed python modules without affecting other tools.
- [x] Run `shellcheck install.sh uninstall.sh skills/worker-routing/routing-audit.sh` with 0 warnings.
- [x] Test `./install.sh .` and `./uninstall.sh .` ensuring clean multi-harness synchronization.