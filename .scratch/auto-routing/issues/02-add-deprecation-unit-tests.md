# 02 — Add Deprecation Unit Tests for Claude Models

**What to build:** Add explicit unit tests to `skills/worker-routing/test_routing.py` that verify `claude-opus-5` and `claude-sonnet-5` are valid routing targets, and explicitly verify that retired model identifiers (`claude-sonnet-4.6`, `claude-opus-4.6`) trigger warnings or violations as expected.

**Blocked by:** 01 — Cleanup Legacy Model Identifiers in Routing Config.

**Status:** completed

- [x] Add unit test case verifying `claude-opus-5` routing step is recognized as valid.
- [x] Add unit test case verifying deprecated `claude-sonnet-4.6` and `claude-opus-4.6` model declarations trigger model drift or deprecation flags.
- [x] Execute `python3 -m unittest skills/worker-routing/test_routing.py` and confirm 100% pass.
