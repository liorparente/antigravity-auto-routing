# antigravity-auto-routing

**Auto Routing & Collaboration Protocol v3.6.0 (Quality-First Standard)** — Deterministic multi-model orchestration, multi-agent advisory council, audit tooling, and continuous learning engine for the Antigravity CLI ecosystem (Gemini `agy`, Claude Code, Codex, LM Studio).

The core philosophy: **Quality Over Token Frugality — 100% correctness and zero defects**. Antigravity acts as a **pure orchestrator**, never executing state-modifying code directly. Instead, it calibrates worker reasoning effort (`low`, `medium`, `high`, `ultra`) across specialized foundation models to conduct deep research, architectural debate, calibrated execution, and strict zero-defect verification.

---

## 📦 What this repository contains

```
antigravity-auto-routing/
├── LICENSE
├── README.md
├── pyproject.toml                     # Standard package configuration for worker_routing
├── test_suite.py                     # Unified test suite discovery runner (900+ tests)
├── install.sh                        # Atomic multi-harness installer and protocol injector
├── uninstall.sh                      # Idempotent uninstaller and cleanup
├── .github/workflows/test.yml        # CI: unit/integration tests + ruff + mypy + shellcheck
├── docs/
│   ├── adr/                          # 11 Architecture Decision Records (ADRs)
│   ├── specs/                        # 11 Functional and technical specifications
│   └── research/                     # 9 In-depth research papers, telemetry & benchmarks
├── knowledge/
│   └── institutional-memory.md       # Long-term domain lessons and historical context
└── skills/
    ├── council-review/               # Multi-agent peer review skill
    │   ├── SKILL.md                  # Council Review specification and trigger rules
    │   ├── references/               # Manifest schemas & member review contracts
    │   └── tests/                    # Council review test suite
    └── worker-routing/               # Core routing, debate, and learning engine
        ├── protocol.md               # Single source of truth for the injected protocol block
        ├── SKILL.md                  # Canonical protocol specification (roles, lifecycle, rules)
        ├── REFERENCE.md              # CLI command reference, REST APIs & learning journal calls
        ├── routing-config.json       # Worker roles, provider mappings, and council policy
        ├── routing-audit.sh          # Wrapper: locates conversation logs and invokes audit
        ├── routing_check.py          # Log audit engine: step-bounded parsing & violation checks
        ├── agent_council.py          # Deterministic 3-tier task routing decision engine & HMAC signer
        ├── critical_dialogue.py      # Unified debate and council-review engine
        ├── debate_state_machine.py   # Pure debate state machine, consensus table & quorum evaluator
        ├── debate_transport.py       # Isolated worker transport & recurring failure notifications
        ├── dialogue_contracts.py     # Pure contract types, quotes/objections parser & verdicts
        ├── dialogue_degradation.py   # Dialogue budget degradation ladder & fallback mechanisms
        ├── dialogue_transcript.py    # Transcript formatting, telemetry & journal persistence
        ├── executive_dialogue_report.py # Executive markdown summary and degradation alerts
        ├── learned_state.py          # Atomic CAS versioned store for adopted rules & memory
        ├── learner_worker.py         # Background worker for weekly & session-end learning cycles
        ├── learning_journal.py       # Append-only JSON Lines journal for task execution telemetry
        ├── learning_outcomes.py      # Ground-truth recorder (tests, review, plan, stalemates)
        ├── learning_report.py        # Structured markdown learning report generator
        ├── learning_report_html.py   # Pure standalone HTML learning metrics dashboard
        ├── learning_scoreboard.py    # Empirical scoring engine for provider routing & replay
        ├── acceptance_gate.py        # Anti-ratchet acceptance gate for proposed learning lessons
        ├── probe_models.py           # Live model catalog probe & CLI capability audit
        ├── production_invoker.py     # Subprocess runner, timeout wrapper & prompt assemblers
        ├── provider_adapters.py      # Transport adapters for Claude, Codex, agy, LM Studio
        ├── prompt_assembler.py       # Prompt templates for planners, critics, and adjudicators
        ├── risk_tiered_application.py# Atomic memory lesson accumulation & risk tiering
        ├── routing_config.py         # Typed parser and validator for routing-config.json
        ├── sensitivity_redactor.py   # Zero-leakage token redactor and secret detector
        └── test_*.py                 # Exhaustive offline unit and integration test suites
```

---

## 👥 The Agent Mesh & Roles

| Role | Primary Model | CLI / Interface | Operational Purpose |
| :--- | :--- | :--- | :--- |
| **Orchestrator** | Antigravity / Claude Code | Active Workspace CLI | Parses user requests, plans workflows, and orchestrates workers. **Prohibited from direct code edits.** |
| **Deep Context Specialist** | **Gemini 3.6 Flash** / **Gemini 3.1 Pro** | `agy -p` (PTY wrapped) | Deep semantic code search, dependency mapping, and 1,000–2,000 token context distillation. |
| **Planner / Deep Thinker** | **Claude Opus 5 (Thinking)** / Claude Fable 5 | `claude -p --model` | High-precision architectural planning, interface design, and invariant reasoning. |
| **Critic / Peer Reviewer** | **Codex 5.6 Sol** / **GPT-OSS 120B** | `codex exec` | Deep reasoning review of drafts, edge-case analysis, and invariant verification. |
| **Heavy Doer** | **Claude Sonnet 5 (Thinking)** | `claude -p` | Multi-file code modifications, refactorings, and logic implementation. |
| **Light Doer** | **Codex 5.6 Terra / Luna** / **Gemini 3.6 Flash (Low)** | `codex exec` / `agy -p` | Focused fixes, single-file edits, unit tests, and mechanical boilerplate. |
| **Local / Sensitive Doer** | **LM Studio (Local Model)** | `http://127.0.0.1:1234/v1` | Isolated execution for credentials, secrets, and private data. Fails closed if offline. |
| **QA / Auditor** | **Codex 5.6 Sol** / **Claude Opus 5** | `codex review` | Pre-commit zero-defect diff audits (`codex review --uncommitted` with `high` effort). |
| **Adjudicator** | **LM Studio (Local Model)** / Codex | Local API | Resolves stalemates in council debates using deterministic heuristics and local inference. |

---

## 🔄 Task Lifecycle & Collaboration Pipeline

For every non-trivial task, the Orchestrator enforces a 4-phase sequential workflow:

```
[Phase 0: Deep Research] ➔ [Phase 1: Deep Thinking & Debate] ➔ [Phase 2: Calibrated Execution] ➔ [Phase 3: Zero-Defect QA]
         (agy)                   (Opus 5 + Codex Sol)             (Sonnet 5 / Terra / Luna)            (Codex Sol Review)
```

### Phase 0: Deep Research & Context Distillation
Before any plan is drafted, codebase structure and dependencies are investigated with `agy`:
```bash
IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: NESTED-EXEC] Perform deep research on {TOPIC}. Map affected files, interfaces, and breaking changes." < /dev/null
```

### Phase 1: Deep Thinking & Planner–Critic Debate
For Medium and Complex tasks, planning undergoes peer review and critique:
1. **Drafting:** The **Planner** writes an interface-first plan to `.claude/plan_draft.md`.
2. **Autonomous Debate Loop:** The **Critic** inspects the draft with `high` reasoning effort:
   ```bash
   cat .claude/plan_draft.md | IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" "[WORKER-MODE: NESTED-EXEC] Review this plan for race conditions, type safety, and edge cases." < /dev/null
   ```
3. **Consensus Delivery:** Up to 3 debate rounds until consensus is reached, then saved to `implementation_plan.md` for human approval.

### Phase 2: Task Decomposition & Calibrated Execution
Sub-tasks are executed by calibrated workers based on complexity and reasoning effort:
- **Trivial (1 file):** Codex Luna (`gpt-5.6-luna`, effort `low`)
- **Simple (1–2 files):** Codex Terra (`gpt-5.6-terra`, effort `medium`)
- **Medium (3–4 files):** Claude Sonnet 5 (`claude-sonnet-5`, effort `high`)
- **Complex (5+ files):** Claude Opus 5 / Codex Sol with Deep Research (`agy`)

### Phase 3: Zero-Defect Verification & QA Audit
1. The **Doer** runs local test suites to verify behavior.
2. The **QA Auditor** audits uncommitted workspace changes before marking completion:
   ```bash
   IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: NESTED-EXEC] Perform zero-defect audit on uncommitted changes." < /dev/null
   ```

---

## 🏛️ Multi-Agent Council Review (`skills/council-review`)

The **Council Review** skill coordinates a panel of distinct models evaluating proposals along 4 specialized perspective lenses:
1. `reviewer_architecture` (Deep module boundaries, loose coupling, interface clarity)
2. `reviewer_risk` (Edge cases, race conditions, failure recovery, regression risk)
3. `reviewer_maintainability` (Clean code, documentation accuracy, testability)
4. `reviewer_security` (Veto authority on secret exposure, unsafe commands, auth flaws)

Council reviews generate signed decision manifests stored in `.ralph/decisions/<task_id>.json` with a 24-hour cache.

---

## 🧠 Continuous Learning Loop & Learned State

The system continuously self-improves through empirical ground-truth tracking:

```
Telemetry Stream (.ralph/learning_journal.jsonl)
       │
       ▼
Ground Truth Recording (learning_outcomes.py: tests, review, plan, stalemates)
       │
       ▼
Acceptance Gate & Replay Benchmark (acceptance_gate.py: anti-ratchet validation)
       │
       ▼
Atomic CAS Versioning (learned_state.py: adopt rules/memory, one-step rollback)
       │
       ▼
Visual HTML Dashboard (learning_report_html.py: standalone browser telemetry)
```

- **Ground-Truth Recording:** Record real outcomes via `learning_outcomes.record_test_result`, `record_review_verdict`, `record_plan_outcome`, and `record_stalemate_resolution`.
- **Learned State Management:** Learned memory lessons and routing policies accumulate atomically with CAS precondition checks in `.ralph/learned-state/`.
- **HTML Dashboard:** Generates a zero-dependency standalone HTML dashboard displaying TTFT, TPS, compliance rates, and degradation events.

---

## ⚙️ Configuring Workers

`skills/worker-routing/routing-config.json` is the central configuration for models, CLI patterns, roles, and council policy.

```json
{
  "roles": {
    "planner": {
      "capability_requirements": {
        "reasoning_tier": "high",
        "tool_access": "read",
        "min_context": 200000
      },
      "preferred_providers": ["claude_opus_5", "claude_fable_5", "codex_sol"]
    },
    "builder_heavy": {
      "capability_requirements": {
        "reasoning_tier": "high",
        "tool_access": "workspace-write",
        "min_context": 128000
      },
      "preferred_providers": ["claude_sonnet_5", "gemini_flash_high"]
    }
  }
}
```

Custom configurations are preserved during re-installations, with missing managed keys merged automatically.

---

## 🚀 Setup & Installation

### Quick Install

```bash
git clone https://github.com/liorparente/antigravity-auto-routing.git
cd antigravity-auto-routing
bash install.sh
```

To install into another workspace without changing directories:
```bash
bash install.sh /path/to/target/project
```

### What `install.sh` Does

1. **Deploys 25+ Production Modules:** Copies `worker-routing` and `council-review` to all supported harnesses:
   - `~/.gemini/config/skills/worker-routing/` & `~/.gemini/config/skills/council-review/`
   - `~/.codex/skills/worker-routing/` & `~/.codex/skills/council-review/`
   - `<target_project>/.agent/skills/` & `<target_project>/.agents/skills/` & `<target_project>/.codex/skills/`
2. **Injects Protocol Block:** Injects the enforced protocol from `protocol.md` between sentinel markers into:
   - `<target_project>/AGENTS.md`
   - `<target_project>/CLAUDE.md`
   - `~/.gemini/GEMINI.md` (Antigravity global instructions)
3. **Deploys Claude Code Rules:** Copies `worker-routing.md` to `<target_project>/.claude/rules/`.
4. **Propagates Learned State:** Synchronizes `.ralph/learned-state/history.jsonl` and snapshot versions.
5. **Preserves Custom Configurations:** Never overwrites custom user rules, existing docs, or custom `routing-config.json` parameters.

### Uninstalling

```bash
bash uninstall.sh [target_project_dir]
```

Removes installed skill directories, cleans up Claude Code rules, and strips protocol blocks while leaving all custom project content intact.

---

## 🔍 Audit & Compliance Verification

The audit engine verifies that the orchestrator never modified source code directly and correctly declared routing on every step.

Run audit on the latest conversation:
```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh
```

Run audit on a specific conversation with strict warning enforcement:
```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh --strict <conversation-id>
```

### Test Suite

Run the complete test suite:
```bash
.venv/bin/python -m unittest test_suite.py
```

Run linter and type-checker:
```bash
ruff check skills/worker-routing/ test_suite.py
skills/worker-routing/typecheck.sh
```

---

## 📄 License

MIT — see [LICENSE](LICENSE).
