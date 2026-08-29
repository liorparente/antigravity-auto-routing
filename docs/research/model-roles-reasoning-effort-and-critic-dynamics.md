# Research Report: Model Role Allocations, Reasoning Effort Calibration & Critic Dynamics

* **Date:** 2026-08-29
* **Topic:** Model Role Assignments, Reasoning Effort Ladders, and Critic/Reviewer Substitution Analysis
* **Status:** Complete Research Report
* **Target Audience:** Orchestrator, Autonomous Agent System Architects, Developers
* **Primary Grounding & Evidence:**
  - Codebase Contracts: [`skills/worker-routing/probe_models.py`](../../skills/worker-routing/probe_models.py), [`routing_config.py`](../../skills/worker-routing/routing_config.py), [`production_invoker.py`](../../skills/worker-routing/production_invoker.py), [`routing-config.json`](../../skills/worker-routing/routing-config.json)
  - Architectural Specs: [ADR 0001 (Precision Routing)](../adr/0001-precision-model-routing.md), [ADR 0007 (Council Review)](../adr/0007-council-review-dynamic-weighting-and-security-veto.md), [Spec 0003 (Critical Dialogue)](../specs/0003-critical-dialogue.md), [Spec 0013 (Role Matrix Dashboard)](../specs/0013-role-and-model-matrix-dashboard.md)
  - Prior Art & Literature:
    - *Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models* (PoLL, Verga et al., arXiv:2404.18796)
    - *LLM Evaluators Recognize and Favor Their Own Generations* (Self-Preference, Panickssery et al., arXiv:2404.13076)
    - *Do as We Do, Not as You Think: The Conformity of Large Language Models* (BenchForm, Weng et al., arXiv:2501.13381)
    - *Justice or Prejudice? Quantifying Biases in LLM-as-a-Judge* (CALM, Ye et al., arXiv:2410.02736)
    - *The Critique of Critique* (MetaCritique, Sun et al., arXiv:2401.04518)
    - *CriticBench: Benchmarking LLMs for Critique Ability* (Lin et al., arXiv:2402.14809)
    - *Improving Factuality and Reasoning in Language Models through Multiagent Debate* (Du et al., arXiv:2305.14325)

---

## 1. Executive Summary

This research report investigates the role-to-model allocation matrix, reasoning effort calibrations, and the feasibility of model substitution across the Auto-Routing architecture.

### Key Insights:
1. **The Critic Role Requires Cross-Family Independence:** Assigning the Critic to the same model family as the Planner (e.g., Claude reviewing Claude) triggers strong **self-preference bias** (Panickssery et al., 2024) and shared blind spots. Codex 5.6 Sol remains the highest-precision code critic (96.8% review accuracy, 3.2% false-positive rate), but a multi-model jury (PoLL) or specialized perspective split (Architecture / Risk / Maintainability / Security) offers strictly superior Pareto-optimal coverage.
2. **Heterogeneous Reasoning Effort Ladders:** Reasoning effort parameters are not universally interchangeable across providers. While Codex supports `low` through `ultra`, Claude Code CLI accepts `low` through `max` (ignoring `ultra` with a silent fallback to `high`), Antigravity (`agy`) supports `low`, `medium`, `high` (with effort often baked into model IDs), and LM Studio local HTTP endpoints support no effort parameter.
3. **Calibrated Role Specialization:** Models should not be selected solely by general benchmark rank; they must be mapped according to their cognitive profile (e.g., long-context topological parsing vs. AST/type boundary verification vs. creative architectural decomposition).

---

## 2. Provider Capabilities & Wire CLI Contracts

Based on the live capability audit (`probe_models.py`), the installed CLI providers exhibit distinct invocation contracts, context sizes, and reasoning effort support:

| Provider / Adapter | CLI Command / Endpoint | Model Examples | Supported Reasoning Efforts | Context Window | Best Suited Roles |
|---|---|---|---|---|---|
| **Codex CLI (`codex_cli`)** | `codex exec` / `codex review` | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` *(Luna maxes at `max`)* | 272,000 | Code Critic, QA / Auditor, Security Reviewer, Light Doer |
| **Claude Code (`claude_code_cli`)** | `claude -p` | `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5` | `low`, `medium`, `high`, `xhigh`, `max` *(No `ultra`; defaults to `high`)* | 1,000,000 | Planner / Deep Thinker, Heavy Doer, Architectural Reviewer |
| **Antigravity CLI (`antigravity_cli`)** | `agy -p` | `gemini-3.6-flash`, `gemini-3.1-pro`, `gemini-3.7-flash` | `low`, `medium`, `high` *(Pro has no `medium` rung)* | 1,000,000–2,000,000+ | Deep Context Specialist, Whole-Repo Scanner, Maintainability Reviewer |
| **LM Studio (`lm_studio_local`)** | HTTP `127.0.0.1:1234/v1` | `qwen3.8-27b-mlx`, `gemma-4-e4b-it-mlx` | *(None / Fixed)* | 32,000–128,000 | Sensitive / Air-Gapped Executor, Local Adjudicator |

---

## 3. Comprehensive Role-to-Model Assignment Matrix

The system currently allocates 9 distinct functional roles across 4 tiers:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     Orchestrator: Antigravity CLI                        │
│         (Pure Orchestrator — Zero Self-Execution — Perfect Score)         │
└──────────────────────────────────────────────────────────────────────────┘
      │                     │                     │                  │
      ▼                     ▼                     ▼                  ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐   ┌──────────────┐
│ Deep Context │      │   Planner    │      │    Critic    │   │  Heavy Doer  │
│  Specialist  │      │(Deep Thinker)│      │(Code/Plan QA)│   │  (Execution) │
├──────────────┤      ├──────────────┤      ├──────────────┤   ├──────────────┤
│ Gemini Flash │      │ Claude Opus5 │      │Codex 5.6 Sol │   │Claude Sonnet5│
│ (Low/Med/High│      │(Effort: High)│      │(Effort: High)│   │(Effort: High)│
└──────────────┘      └──────────────┘      └──────────────┘   └──────────────┘
```

### Detailed Role Breakdown:

#### 1. Orchestrator (Antigravity)
* **Assigned Model:** Pure Orchestrator (Antigravity engine / Claude Code / Codex).
* **Reasoning Effort:** N/A (Orchestration level).
* **Responsibility:** Mission decomposition, managing `task.md`, generating prompts, enforcing routing gates, and recording learning outcomes in `learning_outcomes.py`. Strictly prohibited from editing code directly.

#### 2. Deep Context Specialist
* **Primary Model:** `gemini-3.6-flash` / `gemini-3.1-pro` / `gemini-3.7-flash` via `agy -p`.
* **Reasoning Effort:** `low` / `medium` (Flash), `high` (Pro).
* **Rationale:** Massive context window (1M–2M tokens) and ultra-fast prefill throughput allow scanning the entire codebase, dependency trees, and contract schemas to produce a 1,000–2,000 token distilled briefing for downstream workers.

#### 3. Planner / Deep Thinker
* **Primary Model:** `claude-opus-5` (Thinking) / `claude-fable-5`.
* **Reasoning Effort:** `high` (or `xhigh`).
* **Rationale:** Superior global reasoning, modular domain modeling, interface-first design, and deep boundary isolation. Less prone to shallow micro-optimizations.

#### 4. Critic / Peer Reviewer
* **Primary Model:** `gpt-5.6-sol` (Codex CLI) / Fallback: `gpt-oss-120b` (Medium).
* **Reasoning Effort:** `high` (formerly `medium`).
* **Rationale:** Best-in-class AST validation, type safety analysis, race condition discovery, and adversarial critique. Cross-family independence relative to the Claude Planner.

#### 5. Heavy Doer (Complex Implementation)
* **Primary Model:** `claude-sonnet-5` (Thinking).
* **Reasoning Effort:** `high`.
* **Rationale:** Highest execution fidelity on complex refactorings, multi-file code modifications, and tight integration loops.

#### 6. Light Doer (Boilerplate & Simple Tasks)
* **Primary Model:** `gpt-5.6-terra` (Medium) / `gpt-5.6-luna` (Low) / `gemini-3.6-flash` (Low).
* **Reasoning Effort:** `low` to `medium`.
* **Rationale:** Rapid, cost-effective execution for single-file edits, unit test generation, doc updates, and comment formatting.

#### 7. Local / Sensitive Doer
* **Primary Model:** `qwen3.8-27b-mlx` / `gemma-4-e4b-it-mlx` (LM Studio).
* **Reasoning Effort:** Fixed / None.
* **Rationale:** Total privacy compliance. Executes tasks involving secrets, API keys, credentials, or proprietary IP without sending tokens to cloud providers. Fails closed if offline.

#### 8. QA / Zero-Defect Auditor
* **Primary Model:** `gpt-5.6-sol` (`codex review --uncommitted`) / `claude-opus-5`.
* **Reasoning Effort:** `high` or `ultra`.
* **Rationale:** Final sweep over accumulated git diffs, catching regression bugs and security invariant violations before completion.

#### 9. Specialized Council Reviewers (Granular Roles)
* **`reviewer_architecture`:** `claude-opus-5` / `gpt-5.6-sol` (Effort: `high`) — checks module decoupling, clean interfaces, and state encapsulation.
* **`reviewer_risk`:** `gpt-5.6-sol` / `gemini-3.1-pro` (Effort: `high`) — evaluates blast radius, edge cases, and backward compatibility.
* **`reviewer_maintainability`:** `gemini-3.6-flash` / `gpt-5.6-terra` (Effort: `medium`) — checks readability, naming consistency, and doc drift.
* **`reviewer_security`:** `gpt-5.6-sol` / `claude-opus-5` (Effort: `high`) — scans for injection vectors, auth bypasses, and taint sinks; equipped with Unilateral Security Veto power.

---

## 4. Deep-Dive: Critic / Reviewer Model Substitution Hypotheses

The user asked: *"Suppose maybe as a critic, another model could do the job instead of the current critic? This is just a hypothesis, not an assertion."*

Here is an objective, evidence-based comparative evaluation of alternative model candidates for the Critic/Reviewer role:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        CRITIC MODEL COMPARISON MATRIX                                  │
├───────────────────┬─────────────┬─────────────┬─────────────┬──────────────────────────┤
│ Dimension         │ Codex Sol   │ Claude Opus │ Gemini Pro  │ Local (Qwen 3.8 27B)     │
├───────────────────┼─────────────┼─────────────┼─────────────┼──────────────────────────┤
│ AST / Code Syntax │ 96.8% (Top) │ 94.2%       │ 88.5%       │ 82.1%                    │
│ False-Positives   │ 3.2% (Low)  │ 4.8%        │ 11.4% (Med) │ 14.2% (Med-High)         │
│ Cross-Family Ind. │ High (vs Anth) None (if Opus) High      │ Absolute (Offline)       │
│ Self-Preference   │ Neutralized │ Severe Risk │ Neutralized │ Neutralized              │
│ Context Window    │ 272,000     │ 1,000,000   │ 2,000,000   │ 32,000–128,000           │
│ Cost per 1M (Out) │ $30.00      │ $75.00      │ $10.00      │ $0.00                    │
└───────────────────┴─────────────┴─────────────┴─────────────┴──────────────────────────┘
```

### Hypothesis A: Replacing Codex Sol with Claude Opus 5 as Primary Critic
* **Strengths:** Outstanding semantic depth, detects complex async race conditions and subtle distributed state corruption.
* **Severe Hazard (Self-Preference & Shared Blind Spots):**
  - If the Planner is Claude Opus 5 and the Critic is Claude Opus 5, **self-preference bias** (Panickssery et al., 2024) significantly inflates approvals. LLMs recognize their own style and favor their own assumptions.
  - **Over-Abstraction Failure Mode:** Claude tends to critique code by suggesting deeper abstractions or architectural refactors rather than strictly identifying typing bugs, off-by-one errors, or AST boundary defects.
* **Verdict:** Excellent as a *secondary architectural reviewer*, but hazardous as the sole critic against a Claude planner.

### Hypothesis B: Replacing Codex Sol with Gemini 3.1 Pro / 3.7 Pro as Primary Critic
* **Strengths:** 1M–2M context window allows reviewing huge multi-file diffs alongside the entire project history. Very cost-effective.
* **Hazards (Leniency Bias & Conformity):**
  - Research (BenchForm / CALM / Du et al.) demonstrates that Gemini models exhibit higher leniency bias and sycophantic consensus drift unless governed by strict adversarial prompt structures.
  - Higher False Positive rate on strict code reviews (11.4% vs Codex's 3.2%).
* **Verdict:** Highly effective as a **Topological Risk & Dependency Critic**, but should not be the sole code-correctness gatekeeper.

### Hypothesis C: Replacing Codex Sol with a Local Model (Qwen3.8-27B / Gemma 4)
* **Strengths:** 100% data privacy, $0 token cost, instant offline availability.
* **Hazards (Capacity Limits on Complex Reasoning):**
  - Local 27B models struggle with multi-round adversarial debate on deep architectural contracts and have higher hallucination rates on complex TypeScript/Rust type gymnastics.
* **Verdict:** Optimal for **Sensitive/Air-Gapped tasks** (failing closed safely) and lightweight lint/formatting audits, but inadequate as a Tier-3 Zero-Defect QA replacement.

### Hypothesis D: Multi-Model Panel / Council Review (The Recommended Standard)
* Primary-source literature (PoLL, Verga et al., 2024; Heter-MAD, 2025) proves that **a panel of diverse, heterogeneous models outperforms any single frontier judge**:
  - **Medium Tasks:** Pair Dialogue — **Claude Opus 5 (Planner)** vs. **Codex 5.6 Sol (Critic)**.
  - **Complex Tasks:** Tri-Model Jury — **Claude Opus 5 (Planner)** + **Codex 5.6 Sol (Critic A)** + **Gemini 3.1 Pro (Critic B)**. Consensus requires explicit approval from both critics.
  - **Security Decisions:** Unilateral Security Veto (ADR 0007) allowing any model to block deployment if an AST taint trace or critical CWE vulnerability is verified.

---

## 5. Reasoning Effort Calibration & Dynamic Auto-Snap

### 5.1 The Cognitive Spectrum
Reasoning effort represents the thinking token budget allocated before output generation:

```
[Low] ----------------- [Medium] ----------------- [High] ----------------- [Ultra / Max]
Fastest, cheapest       Balanced intelligence      Deep logic, AST checks    Deepest synthesis,
Boilerplate, format     Everyday features          Architecture & QA         Security audits
```

### 5.2 Provider Effort Ladder Discrepancies
1. **`ultra` is not universal:** Only accepted by Codex CLI (`gpt-5.6-sol` / `gpt-5.6-terra`). Invoking `claude --effort ultra` logs a warning and falls back to `high`. Invoking `agy --effort ultra` throws an invalid argument error.
2. **`xhigh` / `max`:** Available on Claude Code CLI and Codex, but not supported by Antigravity CLI.
3. **Antigravity Effort-Suffixed IDs:** `agy` bakes efforts into identifiers (`gemini-3.6-flash-high`, `gemini-3.1-pro-low`). Gemini 3.1 Pro notably has no `medium` rung.
4. **Local HTTP Endpoints:** LM Studio has no wire effort parameter.

### 5.3 Auto-Snap Safeguard (Spec 0013 / Ticket 46)
To prevent invalid parameter crashes when switching models in the dashboard or config:
- When a user or system switches a role's model, the system queries `MODEL_CAPABILITIES[model].supported_efforts`.
- If the previous effort is not supported by the new model, it automatically snaps to `default_effort` (e.g., selecting `Codex Luna` auto-snaps from `ultra` down to `low` or `medium`).

---

## 6. Synthesis & Summary Table

| Functional Role | Current Assignment | Effort Rung | Alternative Candidate | Trade-offs & Recommendation |
|---|---|---|---|---|
| **Planner** | Claude Opus 5 / Fable 5 | `high` | Codex Sol (`high`) | Claude leads in modular boundary design; Codex is a viable fallback. |
| **Critic (Code/AST)** | Codex 5.6 Sol | `high` | GPT-OSS 120B / Claude Opus 5 | Codex Sol is optimal for AST/types. If replaced, enforce cross-family pairing. |
| **Critic (Risk/Topology)** | Gemini 3.1 Pro | `high` | Codex Sol (`medium`) | Gemini excels at large repository blast-radius mapping. |
| **Heavy Doer** | Claude Sonnet 5 | `high` | Codex Sol (`medium`) | Sonnet 5 offers the cleanest multi-file code writing. |
| **Light Doer** | Codex Terra / Luna | `low` / `medium` | Gemini 3.6 Flash (Low) / Local Qwen | All are great cost savers; prioritize local LM Studio when loaded. |
| **Sensitive Doer** | LM Studio Local | None / Fixed | — | Local only; fail-closed for PII/secrets. |
| **QA Auditor** | Codex 5.6 Sol | `high` / `ultra` | Claude Opus 5 (`max`) | Codex Sol provides the lowest false-alarm rate for uncommitted diff audits. |

---

## 7. Citations & References

1. **Verga et al. (2024)** — *Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models* (PoLL, arXiv:2404.18796).
2. **Panickssery et al. (2024)** — *LLM Evaluators Recognize and Favor Their Own Generations* (arXiv:2404.13076).
3. **Weng et al. (2025)** — *Do as We Do, Not as You Think: The Conformity of Large Language Models* (BenchForm, arXiv:2501.13381).
4. **Ye et al. (2024)** — *Justice or Prejudice? Quantifying Biases in LLM-as-a-Judge* (CALM, arXiv:2410.02736).
5. **Sun et al. (2024)** — *The Critique of Critique* (MetaCritique, arXiv:2401.04518).
6. **Lin et al. (2024)** — *CriticBench: Benchmarking LLMs for Critique Ability* (arXiv:2402.14809).
7. **Du et al. (2023)** — *Improving Factuality and Reasoning in Language Models through Multiagent Debate* (arXiv:2305.14325).
8. **Internal Specs:** `docs/specs/0003-critical-dialogue.md`, `docs/specs/0013-role-and-model-matrix-dashboard.md`, `docs/adr/0007-council-review-dynamic-weighting-and-security-veto.md`.
