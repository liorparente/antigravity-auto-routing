# 52 — Automated Unit Tests & AST Invariants

**GitHub Issue:** [#28](https://github.com/liorparente/antigravity-auto-routing/issues/28)

**What to build:** A comprehensive suite of unit tests in `test_learning_report_html.py` and `test_learning_report.py` ensuring Role Matrix markup, model capability escaping, and server endpoints are 100% verified.

**Blocked by:** 51 — Local Dashboard Server & Atomic Save API

**Recommended Worker:** Tier 0 (LM Studio Local $0) / Tier 1 (Fast Flash)

**Status:** done

- [x] Add unit tests verifying tab navigation and Role Matrix HTML markup generation.
- [x] Add unit tests asserting dynamic model capability injection and strict escaping (`_escape`).
- [x] Add unit tests for `--serve` argument parsing and API endpoint request validation.
- [x] Enforce the no-live-clock AST guard invariant.
- [x] Run and pass full test suite: `python3 -m unittest discover skills/worker-routing`.

## Delivered

No new code or tests were needed — every checklist item was already
satisfied by prior tickets' own TDD passes, closed out here rather than
duplicated:

- **Tab navigation & Role Matrix markup:** `RoleMatrixSectionTests` in
  `test_learning_report_html.py` (empty-state, primary-vs-all-roles
  grids, capability pills, unrecognized-role fallback) plus
  `test_tab_bar_and_role_matrix_heading_are_present`. The tab bar itself
  is CSS-only (radio-driven `:checked` selectors, no JS state machine to
  exercise), so markup assertions are the complete test surface for it.
- **Dynamic capability injection & escaping:** `ModelCapabilitiesPayloadTests`
  (provider::model keying, real audited-registry embedding) and
  `ScriptInjectionTests` (`test_a_role_value_carrying_a_script_tag_adds_no_script_tag_to_the_document`,
  `test_a_capability_model_id_cannot_break_out_of_the_json_payload`),
  plus the direct `_escape` unit tests
  (`test_the_escape_helper_neutralizes_html_metacharacters`).
- **`--serve` parsing & API endpoint validation:** `ServeCliTests` (flag
  dispatch, default port, `--now` exemption) and `ConfigApiServerTests`
  in `test_learning_report.py` — real HTTP round trips
  (`http.client.HTTPConnection` against an OS-assigned port) covering
  `POST /api/config` (valid save, malformed JSON, schema-invalid
  payload, no stray temp file) and `GET /api/model-capabilities`
  (provider/model-keyed, tier fill-in, non-blocking launch probe,
  read-only). These were built out across ticket 51's own commit and
  three subsequent review-driven polish commits
  (`afa8102`, `803eee7`, `55ee5bc`, `dd167dc`).
- **No-live-clock AST guard:** `NoClockTests` exists in both
  `test_learning_report.py` (guards `learning_report.py`) and
  `test_learning_report_html.py` (guards `learning_report_html.py`),
  each parsing the module's source with `ast.parse` and asserting
  `_find_forbidden_clock_reads` returns nothing.
- **Full suite:** `python3 -m unittest discover skills/worker-routing`
  passes 1636 tests. The one pre-existing failure
  (`test_institutional_memory_matches_golden_rules`) is unrelated —
  tracked separately as the institutional-memory/GOLDEN_RULES drift fix
  (spec 0014).