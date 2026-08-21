# 07 — Test Routing & Invoker Legacy Loaders

**GitHub Issue:** not yet filed
**What to build:** Eliminate the remaining test-file by-path `importlib.util.spec_from_file_location`
loaders in `test_routing.py` (lines 72, 78, 86, 94, 1400, 5568, 7065, 7367) and
`test_production_invoker.py` (lines 383, 452), replacing them with standard hybrid
package/standalone imports of the now-package modules they load dynamically (`routing_check`,
`agent_council`, `advisory_consultation`, `learned_state`, `learning_journal`, `learning_outcomes`,
`production_invoker`). `debate_orchestrator.py:425` is out of scope: it is an external cross-skill
adapter, not a test-file loader, and is not touched by this ticket.

**Blocked by:** [06 — Transparent CLI Bootstrap & Multi-Harness Installer Sync (#15)](06-transparent-cli-bootstrap-and-installer-sync.md)

**Status:** ready-for-agent

- [ ] Replace each `spec_from_file_location`/`module_from_spec`/`exec_module` triple in
      `test_routing.py` with the file's standard `if __package__: from . import x else: import x`
      header, importing the target module once at file scope rather than by path per test.
- [ ] Do the same for the two by-path loaders in `test_production_invoker.py`.
- [ ] Confirm no test in either file relies on a *fresh* module object per test (a side effect of
      `spec_from_file_location` re-executing the module) before collapsing to a single shared import —
      if any do, note them explicitly rather than silently changing their isolation.
- [ ] Run the full suite (`python3 -m unittest discover`) and confirm no regression.
