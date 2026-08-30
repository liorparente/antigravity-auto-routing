# 01 — Extract Pure SensitivityRedactor Delegation in agent_council.py

**What to build:** Clean up `agent_council.py` so that all sensitivity detection (`detect_sensitive_data`, `evaluate_sensitivity`) delegates directly to `sensitivity_redactor.py` and removes duplicated `SENSITIVE_PATTERNS`.

**Blocked by:** None — can start immediately

**Status:** done

- [x] Remove duplicate `SENSITIVE_PATTERNS` constant definition from `agent_council.py`
- [x] Refactor `detect_sensitive_data` and `evaluate_sensitivity` to delegate directly to `sensitivity_redactor.scan_sensitivity_markers`
- [x] Verify that all existing unit tests in `test_routing.py` and `test_sensitivity_redactor.py` continue to pass
