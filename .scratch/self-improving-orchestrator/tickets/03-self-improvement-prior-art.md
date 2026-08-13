# 03 — Prior art: self-improving orchestrator loops

**Type:** Research (AFK)
**Blocked by:** none
**Status:** landed — verified at commit `5207608`
**Branch / findings:** `research/self-improvement-prior-art` → `docs/research/self-improvement-prior-art.md`

**Question:** Which continuous-learning architectures for LLM orchestrators are documented to
actually improve behavior — and what guardrails does practice put on self-modification?

**Why it matters:** Tickets 07 and 08 pick the learning mechanism and its safety gate; ticket 09
picks the proof-of-improvement. All three should start from what is known to work, not from
intuition.

**Must cover:**

- Reflection / episodic-memory loops and their documented gains.
- Skill/knowledge libraries accumulated by agents — what transfers across tasks.
- Learned routing: router training / bandit-style model selection — evidence of benefit.
- Prompt optimization as learning (offline optimizers vs online adaptation).
- Safety practice for self-modifying agent systems: approval gates, canary/shadow evaluation,
  rollback, versioned learned state.
- How serious systems *prove* improvement: eval-harness and regression-suite patterns for agents.

**Resolution:** Findings at `docs/research/self-improvement-prior-art.md` (branch
`research/self-improvement-prior-art`, commit `5207608`). Essence: (1) learning works only against
an *external* signal — intrinsic self-correction degrades performance (arXiv:2310.01798), while
Reflexion-style episodic reflection on test feedback reaches 91% pass@1 on HumanEval
(arXiv:2303.11366). (2) Learned routing has the strongest cost evidence — MixLLM: 97.25% of GPT-4
quality at 24.18% of cost, online-adaptive (arXiv:2502.18482); RouteLLM routers transfer across
model pairs; FrugalGPT cascades. (3) What transfers is executable/procedural state — skill
libraries (Voyager), workflow memories (AWM, +51.1% relative on WebArena) — and offline-compiled
prompt configs (DSPy/GEPA) shaped as versioned JSON artifacts. (4) Every measured self-modifying
system (Darwin Gödel Machine, SICA) uses the same guardrails: sandboxed learner, benchmark-gated
acceptance, version archive with rollback, human oversight — the learner proposes, an external
gate disposes. (5) Acceptance needs repeated trials (τ-bench pass^k — single-run wins are noise)
plus CI-style fail-below-threshold gates and backtesting on historical traffic.
