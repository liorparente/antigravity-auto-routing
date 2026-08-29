# 02 — Expand: Implement the Deep CriticalDialogue Module

**What to build:** Introduce `skills/worker-routing/critical_dialogue.py` as the consolidated deep module encapsulating all debate orchestration, turn execution, multi-perspective council reviews, quorum reduction, HMAC signing, transcript rendering, and degradation ladder rungs behind a clean, high-leverage interface.

**Blocked by:** 01 — Extract Pure SensitivityRedactor Delegation in agent_council.py

**Status:** ready-for-agent

- [ ] Consolidate the debate engine and council review capabilities into `skills/worker-routing/critical_dialogue.py`
- [ ] Expose the canonical public interface (`run_critical_dialogue`, `request_council_review`, `run_canary_dialogue`, `ReviewCouncil`)
- [ ] Encapsulate internal submodules (state machine, contracts, transcripts, degradation ladder) behind private seams
- [ ] Add dedicated unit tests exercising the new module directly
