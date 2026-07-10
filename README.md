# antigravity-auto-routing

**Auto Routing & Collaboration Protocol v3.0** — a multi-model orchestration protocol, audit tooling, and installer for the Antigravity CLI ecosystem (Gemini `agy`, Claude Code, Codex).

The core idea: the orchestrator model (Antigravity) should never spend its own expensive tokens writing code or running commands. Instead, it assesses task complexity and **routes** every unit of work — context gathering, planning, implementation, and QA — to the cheapest model capable of doing it correctly. Tokens saved on the orchestrator are cost saved across the whole session.

---

## What this repository contains

```
antigravity-auto-routing/
├── LICENSE
├── README.md
├── install.sh                        # idempotent installer
└── skills/
    └── auto-routing/
        ├── SKILL.md                  # full protocol specification
        ├── routing-audit.sh          # post-session violation scanner
        ├── routing_check.py          # helper: dynamic worker-pattern regex + [ROUTING: Direct] → code-edit violations
        └── routing-config.json       # worker role → model name + CLI pattern mapping (user-customizable)
```

- **`skills/auto-routing/SKILL.md`** — the canonical protocol document. Defines the agent mesh (Orchestrator, Context Specialist, Planner, Critic, Heavy/Light Doers, Local/Sensitive Doer, QA/Auditor), the task lifecycle, the difficulty-aware routing matrix, and CLI command references for `agy`, `claude`, `codex`, and LM Studio.
- **`skills/auto-routing/routing-audit.sh`** — scans Antigravity conversation logs (`~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/overview.txt`) for protocol violations: source code edits with zero worker CLI calls, and `[ROUTING: Direct]` declarations that precede a source code edit.
- **`skills/auto-routing/routing_check.py`** — Python helper invoked by the audit script. In `--regex` mode it builds a worker-detection regex from `routing-config.json`; in log-file mode it does the line-window pattern matching for the `[ROUTING: Direct] → code edit` check, using the same config to recognize worker CLI invocations.
- **`skills/auto-routing/routing-config.json`** — the source of truth for which models/CLIs count as "workers." See [Configuring workers](#configuring-workers) below.
- **`install.sh`** — copies the skill files into `~/.gemini/config/skills/auto-routing/` and appends the enforced protocol block to `~/.gemini/GEMINI.md` (Antigravity's global instruction file), if not already present.

---

## The Agent Mesh

| Role | Primary Model | Interface | Purpose |
| :--- | :--- | :--- | :--- |
| Orchestrator | Claude Code / Codex | Active workspace CLI | Decomposes requests into `task.md`, routes every sub-task, never self-executes |
| Context Specialist | Gemini 3.5 Flash | `agy -p` | Semantic code search, large-repo/document parsing, distilled context briefs |
| Planner / Thinker | Claude Fable 5 / Opus 4.8 | `claude -p --model <model>` | Architectural specs and implementation plans from distilled context |
| Critic / Peer Reviewer | Codex 5.6 Sol | `codex exec` | Peer-reviews plans for edge cases and architectural violations |
| Heavy Doer | Claude Sonnet 5 | `claude -p` | Complex, multi-file implementation |
| Light Doer | Codex 5.6 Terra / Luna | `codex exec` | Boilerplate, simple logic, unit tests |
| Local / Sensitive Doer | LM Studio (Qwen 30B / Gemma) | Local REST API | PII/credential-touching work, offline fallback |
| QA / Auditor | Codex 5.6 Sol | `codex review` | Final audit of uncommitted diffs |

### Planner–Critic Consensus Loop

For every **Medium** and **Complex** task, the protocol enforces a peer-review step before any code is written:

1. **Draft** — the Planner (Claude Fable 5, or Opus 4.8 for architectural-tier work) writes a proposed plan to `.claude/plan_draft.md`.
2. **Review** — the Critic (Codex 5.6 Sol) is handed that draft and asked to flag edge cases, performance bottlenecks, and architectural inconsistencies:
   ```bash
   cat .claude/plan_draft.md | codex exec "Review this plan. Check for edge cases, performance bottlenecks, and architectural violations."
   ```
3. **Refine** — the Planner folds the Critic's feedback into a final `implementation_plan.md`, which goes to the user for approval before execution begins.

This loop exists so the orchestrator never commits expensive-model tokens to speculative planning that a cheaper reviewer would have caught — disagreements surface *before* the Heavy Doer starts editing files.

### Context Specialist (`agy` / Gemini 3.5 Flash)

Before any planning happens, the orchestrator delegates codebase understanding to `agy`, wrapped in a PTY (`script -q /dev/null`) to prevent CLI hangs on long-running scans:

```bash
script -q /dev/null agy -p "Scan the codebase and locate all references to {TOPIC}. Output a distilled context summary."
```

The goal is a 1,000–2,000 token distilled brief — not raw file dumps — so the Planner's context window stays clean and focused on decision-making rather than search.

### Difficulty-Aware Routing Matrix

| Complexity | Signs | Worker | Strategy |
| :--- | :--- | :--- | :--- |
| Trivial | Single-file edits, formatting, quick Q&A | Codex 5.6 Luna / local Gemma 4 E4B | Direct generation, no CoT |
| Simple | Boilerplate, unit tests, 1–2 files | Codex 5.6 Terra / local Qwen3 Coder 30B | System 1 few-shot |
| Medium | New features, 3–4 files, API integration | Planner + Executor: Claude Sonnet 5 | ICoT (think then implement) |
| Complex | Architectural shifts, 5+ files | Planner: Fable 5/Opus 4.8, Critic: Codex Sol, Executor: Sonnet 5 | Consensus loop + Hi-CoT |
| Sensitive | PII, credentials, secrets | LM Studio (local only) | Zero-leakage offline flow |

Full command syntax for `agy`, `claude`, `codex`, and the LM Studio REST API is in [`skills/auto-routing/SKILL.md`](skills/auto-routing/SKILL.md).

---

## Configuring workers

`skills/auto-routing/routing-config.json` is the single source of truth for what counts as a "worker" during auditing. It maps each role in the agent mesh to a display `name` and a list of `patterns` — substrings that identify that worker's CLI invocation in a conversation log:

```json
{
  "heavy_doer": {
    "name": "Claude Sonnet 5",
    "patterns": ["claude -p"]
  },
  "sensitive_doer": {
    "name": "LM Studio (Local Model)",
    "patterns": ["127.0.0.1:1234/v1/chat", "localhost:1234/v1/chat"]
  }
}
```

`routing_check.py` reads this file at runtime — it is never hardcoded:
- `routing_check.py --regex` flattens every role's `patterns` into one regex-escaped alternation (e.g. `(claude -p|codex|agy|...)`) and prints it to stdout. `routing-audit.sh` calls this to build `WORKER_REGEX` for its `grep -cE` worker-call count, falling back to a hardcoded pattern only if the script call fails.
- `routing_check.py <log-file>` uses the same patterns to decide whether a `[ROUTING: Direct]` step that touches a code file actually invoked a recognized worker CLI, or is a genuine violation.

### Customizing for your own stack

To swap in different models or tools, edit `routing-config.json` — no changes to the shell script or Python are needed:

- **Different CLI for an existing role** — change `patterns`, e.g. point `heavy_doer` at a different command.
- **Local models via Ollama** — add a pattern matching your invocation, e.g. `"patterns": ["ollama run"]`.
- **A custom in-house script** — add its invocation string, e.g. `"patterns": ["./scripts/my-worker.sh"]`.
- **New role** — add a new top-level key with `name` and `patterns`; it's picked up automatically by both `--regex` and log-file checks.

Patterns are treated as literal substrings (regex-escaped internally), so no special quoting is needed — just list the exact text that appears in your logs when that worker is invoked.

After editing the repo's copy, re-run `bash install.sh` — the installer only copies `routing-config.json` into `~/.gemini/config/skills/auto-routing/` if it isn't already there, so your installed customizations are preserved across upgrades. To force-refresh an installed config, delete the installed copy first, then re-run `install.sh`.

---

## Setup

### Option 1: Clone and install

```bash
git clone https://github.com/liorparente/antigravity-auto-routing.git
cd antigravity-auto-routing
bash install.sh
```

### Option 2: One-line remote install

```bash
curl -fsSL https://raw.githubusercontent.com/liorparente/antigravity-auto-routing/main/install.sh | bash
```

Both options are idempotent — running `install.sh` again will not duplicate the protocol block in `~/.gemini/GEMINI.md`, and file copies are simple overwrites.

### What the installer does

1. Creates `~/.gemini/config/skills/auto-routing/` if it doesn't already exist.
2. Copies `SKILL.md`, `routing-audit.sh`, and `routing_check.py` into that directory (always overwritten).
3. Marks `routing-audit.sh` executable (`chmod +x`).
4. Copies `routing-config.json` into that directory **only if it doesn't already exist**, so any customizations you've made to your installed copy (see [Configuring workers](#configuring-workers)) survive re-running the installer.
5. Checks `~/.gemini/GEMINI.md` for the marker `## Worker Routing Protocol (HARD ENFORCED — v3.0)`. If absent, appends the full enforced protocol block — the hard gate, mandatory response template, complexity matrix, and escalation triggers that Antigravity reads on every session start.

---

## Usage

Once installed, Antigravity's global instructions (`~/.gemini/GEMINI.md`) enforce the routing gate on every state-modifying action: before writing a file or running a non-read-only command, it must declare `[ROUTING: {worker} — complexity: {level} — reason: ...]` or `[ROUTING: Direct — reason: {allowed exception}]` as the first line of its response. Allowed direct exceptions are read-only operations, documentation/`.md`/`.html` edits, MCP tool calls, and QA routing itself — never source code.

### Verifying compliance

Run the audit script against the most recent Antigravity conversation:

```bash
~/.gemini/config/skills/auto-routing/routing-audit.sh
```

Or target a specific conversation by ID:

```bash
~/.gemini/config/skills/auto-routing/routing-audit.sh <conversation-id>
```

The script reports:
- Total file-write tool calls and how many targeted source code (`.ts`, `.tsx`, `.js`, `.jsx`, `.css`)
- Number of `[ROUTING:]` declarations and worker CLI invocations found in the log
- `[ROUTING: Direct] → code edit` violations (direct routing declared immediately before a source code write)
- A 🔴 violation if source code was edited with zero worker calls
- A 🟡 warning if code edits outnumber worker calls, or no routing declarations were found at all
- A breakdown of which source files were touched, by edit count

Exit and output are informational — the script does not block or modify anything; it's a post-hoc compliance check you run after a session (or wire into CI/a pre-commit hook if you want enforcement at that layer).

---

## License

MIT — see [LICENSE](LICENSE).
