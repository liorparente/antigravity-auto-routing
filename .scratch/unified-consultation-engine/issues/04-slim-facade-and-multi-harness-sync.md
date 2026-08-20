# 04 — Slim Facade for `council_review.py` & Multi-Harness Sync

**What to build:** Reduce `skills/council-review/scripts/council_review.py` to a thin delegating facade (<25 lines) wrapping the unified debate orchestrator while preserving all existing public exports (`ReviewCouncil`, `ReviewRequest`, `ReviewOutcome`, `PrivacyMode`), and synchronize the codebase across `.agents/`, `.codex/`, and `~/.gemini/` via `install.sh .`.

**Blocked by:** 03 — Universal Security Veto & Selective HMAC Manifest Signing in `debate_orchestrator.py`

**Status:** completed

- [x] Reduce `skills/council-review/scripts/council_review.py` to a thin facade under 30 lines.
- [x] Ensure all 12 tests in `skills/council-review/tests/test_council_review.py` pass.
- [x] Ensure all 1,010 tests in `skills/worker-routing/` pass.
- [x] Run `./install.sh .` and verify clean multi-harness sync.
