# Prior Art: Self-Improving LLM Orchestrators

* **Ticket:** 03 of wayfinder map `self-improving-orchestrator`
* **Date:** 2026-08-11
* **Scope:** Which continuous-learning architectures are *documented* (measured, primary-source) to improve orchestrator behavior, and what guardrails practice puts on self-modification. Context: ADR 0005's four pillars, specifically Pillar 2's periodic benchmark job proposing updates to `routing_config.json`.

---

## 1. Reflection / episodic-memory loops

Verbal self-reflection stored in episodic memory measurably improves later attempts — **when an external feedback signal exists**.

* [Reflexion: Language Agents with Verbal Reinforcement Learning](https://arxiv.org/abs/2303.11366) — agents "verbally reflect on task feedback signals" and keep reflections in an episodic memory buffer for subsequent trials; no weight updates. Measured: **91% pass@1 on HumanEval vs 80% for baseline GPT-4**, with gains across decision-making, coding, and reasoning tasks. The feedback driving reflection is external (test results, environment signals).
* [ExpeL: LLM Agents Are Experiential Learners](https://arxiv.org/abs/2308.10144) — extends the pattern across tasks: the agent autonomously distills natural-language insights from a *collection* of training tasks and retrieves them at inference, reporting "consistent enhancement in its performance as it accumulates experiences" — all without parametric updates, i.e., viable over API-only models.

**Where it plateaus:** [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) (ICLR 2024) shows that *intrinsic* self-correction — reflection with no external feedback — fails: "LLMs struggle to self-correct their responses without external feedback, and at times, their performance even degrades." The documented gains of Reflexion-style loops are bounded by the quality of the external signal (tests, oracles, environment rewards); pure self-assessment is not a learning signal.

## 2. Skill / knowledge libraries accumulated across tasks

What transfers is the **procedural, executable unit** — code skills and induced workflows — not raw episode transcripts.

* [Voyager: An Open-Ended Embodied Agent with Large Language Models](https://arxiv.org/abs/2305.16291) — maintains "an ever-growing skill library of executable code"; skills are "temporally extended, interpretable, and compositional" and prevent catastrophic forgetting. Measured: **3.3x more unique items, 2.3x longer traversal, 15.3x faster tech-tree milestones** than prior SOTA; the learned skill library transfers to a *new* Minecraft world to solve novel tasks "while other techniques struggle to generalize."
* [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) — induces reusable workflows from past trajectories, offline or online. Measured: **+24.6% relative success on Mind2Web, +51.1% on WebArena**, with fewer steps per success; generalization *improves* as train–test distribution gaps widen (surpassing baselines by 8.9–14.0 absolute points across task/site/domain shifts), over 1,000 tasks and 200+ domains.

**Takeaway:** libraries of verified, executable routines are the best-documented transferable learned state; episodic logs are input material, not the asset itself.

## 3. Learned routing between cheap and strong models

Trained routers and bandit-style selectors show the strongest *cost* evidence in the whole literature, at near-parity quality.

* [RouteLLM: Learning to Route LLMs with Preference Data](https://arxiv.org/abs/2406.18665) — routers trained on human preference data + augmentation cut costs "by over 2 times in certain cases" without compromising quality, and — critically for a multi-provider matrix — routers "demonstrate significant transfer learning capabilities, maintaining their performance even when the strong and weak models are changed at test time."
* [FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance](https://arxiv.org/abs/2305.05176) — learned LLM cascades "match the performance of the best individual LLM (e.g. GPT-4) with up to 98% cost reduction," or **beat GPT-4 accuracy by 4% at equal cost**.
* [MixLLM: Dynamic Routing in Mixed Large Language Models](https://arxiv.org/abs/2502.18482) (NAACL 2025) — **contextual-bandit** routing with tag-enhanced query embeddings, per-candidate quality/cost predictors, and a meta decision-maker over quality/cost/latency. Measured: **97.25% of GPT-4 quality at 24.18% of the cost**. Continual training explicitly handles the two failure modes of a static matrix: query drift and a *changing model roster* ("new LLM addition or old LLM removal").

**Takeaway:** the bandit framing (MixLLM) is the closest published analogue to ADR 0005 Pillar 2 — an online loop that keeps a routing policy calibrated as models and workloads change, fed by graded outcome feedback.

## 4. Prompt optimization as learning (DSPy family and successors)

"Learning the prompt" against a metric is a documented, artifact-producing form of self-improvement — and it is **offline by design**.

* [DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines](https://arxiv.org/abs/2310.03714) — pipelines as declarative modules compiled against a metric. Measured: **>25% gains (GPT-3.5) and >65% (llama2-13b)** over standard few-shot prompting; beats expert-written demonstrations by 5–46%; compiling takes minutes and small compiled models compete with expert-prompted GPT-3.5.
* [GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning](https://arxiv.org/abs/2507.19457) (ICLR 2026 oral) — reflects on full trajectories in natural language, proposes prompt updates, and combines lessons from a Pareto frontier. Measured: beats GRPO RL by **6% avg / up to 20% with up to 35x fewer rollouts**, and MIPROv2 by >10%. Natural-language reflection is thus a *more sample-efficient* learning operator than policy-gradient RL at this scale.
* [DSPy documentation](https://dspy.ai/) — the official workflow: give "examples and a scoring function," run `optimizer.compile(...)`, then **save the optimized program as a versioned JSON artifact** (`optimized.save('extract_v2.json')`). Optimization is a compile-time step you rerun when code, data, or models change — not a live mutation. Optimizer roster: GEPA, MIPROv2, BootstrapFewShot, BootstrapFinetune, SIMBA, et al.

**Offline vs online:** the DSPy family optimizes offline against a labeled trainset + metric; online variants exist only as AWM-style workflow induction (§2) or bandit policy updates (§3). No serious framework hot-edits its own prompts in production without an eval gate.

## 5. Safety practice for self-modifying agent systems

What practitioners *actually do* — not just recommend — reduces to four controls: sandbox, benchmark-gated acceptance, versioned learned state, human oversight.

* [Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents](https://arxiv.org/abs/2505.22954) — the flagship self-modifying system: agents rewrite their own code, but every variant is (a) **empirically validated on coding benchmarks** before joining the archive (SWE-bench 20.0%→50.0%, Polyglot 14.2%→30.7%), (b) kept in an **archive** (lineage/rollback, not in-place mutation), and (c) run "with safety precautions (e.g., sandboxing, human oversight)."
* [A Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228) (SICA) — an agent editing its own codebase gains **17%–53%** on a SWE-bench Verified subset; improvement is driven and *accepted* solely via benchmark performance, not self-assessment.
* [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts) — production practice for learned/prompt state: prompts and datasets are **versioned first-class assets**; changes are promoted (not hot-patched), with tagged dataset versions targeted in CI "to ensure dataset updates don't break workflows"; **online evaluation** runs on live traffic (no reference outputs) as the shadow/canary layer, while offline eval gates promotion.
* [promptfoo CI/CD integration](https://www.promptfoo.dev/docs/integrations/ci-cd/) — quality gates as build failures: assertions plus thresholds ("Fail the build when quality thresholds aren't met," e.g. pass rate < 95% → non-zero exit → merge blocked).

**Takeaway:** in every documented system the learner *proposes*; a benchmark + gate *disposes*. Self-modification without an external acceptance test appears only in position pieces, never in systems reporting results.

## 6. Proving improvement: eval harnesses and regression gates

* **Offline regression suite as the acceptance test.** [LangSmith evaluation concepts](https://docs.langchain.com/langsmith/evaluation-concepts): curated datasets with reference outputs support *benchmarking* (compare versions), *regression testing* ("ensure new versions don't degrade quality"), and *backtesting* ("test new versions against historical data" — i.e., replay real past traffic under the candidate change). *Pairwise* evaluation (LLM- or human-judged A vs B) is the documented fallback when absolute scoring is hard.
* **CI mechanics.** [promptfoo CI/CD](https://www.promptfoo.dev/docs/integrations/ci-cd/): every prompt/config change runs the assertion suite in CI; JSON/JUnit outputs, PR-comment diffs, and hard exit-code gates make regressions block merges — the same shape as a unit-test gate.
* **Repeated trials, not single runs.** [τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/abs/2406.12045) introduces **pass^k** (success on *all* k i.i.d. trials) and measures that even GPT-4o solves <50% of tasks with pass^8 **below 25%** in retail. Agent runs are high-variance; a learned change "winning" one run is noise. Acceptance criteria must aggregate repeated trials.

---

## Implications for tickets 07/08/09

* **Telemetry is the training signal (07):** MixLLM/RouteLLM-style learned routing works only on graded outcomes — every consultation record should carry task tags, complexity, chosen worker, cost, and a success grade so the benchmark job can fit routing updates from it.
* **Learn artifacts, not weights (08):** all documented orchestrator-level gains come from diffable text/JSON state — Reflexion insights, Voyager-style skill entries, DSPy-compiled configs. The learning loop should emit a *proposed diff* to `routing_config.json` (ADR 0005 Pillar 2), never mutate it in place.
* **No self-grading (08):** intrinsic self-correction degrades performance (arXiv:2310.01798); a proposed change is scored exclusively by the periodic benchmark suite against reference outcomes.
* **DGM guardrail set (09):** sandbox the learner, keep every accepted config version in an archive with one-step rollback, and put a human approval gate on promotion — the pattern every measured self-modifying system (DGM, SICA) actually uses.
* **Acceptance = repeated-trial regression gate (09):** promote a learned change only if pass^k-style repeated runs on the regression suite meet threshold and no tracked metric regresses — promptfoo-style hard CI gate, LangSmith-style backtesting on historical consultations.
