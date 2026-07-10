# antigravity-worker-routing

**Auto Routing & Collaboration Protocol v3.0** — a multi-model orchestration protocol, audit tooling, and installer for the Antigravity CLI ecosystem (Gemini `agy`, Claude Code, Codex).

The core idea: the orchestrator model (Antigravity) should never spend its own expensive tokens writing code or running commands. Instead, it assesses task complexity and **routes** every unit of work — context gathering, planning, implementation, and QA — to the cheapest model capable of doing it correctly. Tokens saved on the orchestrator are cost saved across the whole session.

---

## What this repository contains

```
antigravity-worker-routing/
├── LICENSE
├── README.md
├── install.sh                        # idempotent installer
├── uninstall.sh                      # removes everything install.sh added
├── .github/workflows/test.yml        # CI: unit tests + shellcheck
└── skills/
    └── worker-routing/
        ├── protocol.md                # single source of truth for the enforced protocol text
        ├── SKILL.md                   # full protocol specification (roles, lifecycle, CLI reference)
        ├── routing-audit.sh           # thin wrapper: locates the log, delegates to routing_check.py
        ├── routing_check.py           # audit engine: log parsing + all routing metrics + violations
        ├── routing-config.json        # worker role → model name + CLI pattern mapping (user-customizable)
        ├── test_routing.py            # unit + integration tests
        └── tests/fixtures/            # sample logs (plain text, JSON, JSON Lines) used by the tests
```

- **`skills/worker-routing/protocol.md`** — the single source of truth for the hard-enforced protocol (the gate, response template, complexity matrix, escalation triggers). `install.sh` copies it verbatim into `AGENTS.md` and `CLAUDE.md` at the target project root, and injects it into `~/.gemini/GEMINI.md`. Edit only this file — the generated copies are overwritten on every install.
- **`skills/worker-routing/SKILL.md`** — the canonical protocol document. Defines the agent mesh (Orchestrator, Context Specialist, Planner, Critic, Heavy/Light Doers, Local/Sensitive Doer, QA/Auditor), the task lifecycle, the difficulty-aware routing matrix, and CLI command references for `agy`, `claude`, `codex`, and LM Studio.
- **`skills/worker-routing/routing-audit.sh`** — locates a conversation's log (auto-detecting `overview.txt` or `transcript.jsonl` under `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/`) and hands it to `routing_check.py`, relaying its exit code directly.
- **`skills/worker-routing/routing_check.py`** — the audit engine. Parses the log — plain text, JSON, or JSON Lines — into per-step tool calls, then computes every metric (total writes, code-file writes, `[ROUTING:]` declarations, worker CLI calls, and `[ROUTING: Direct] → code edit` violations) strictly within each step's own boundaries, so a worker mention in one step can never clear a violation in another. Worker-CLI detection only looks at the actual `CommandLine` of a `run_command` tool call — never at surrounding prose — and code-file detection matches file extensions exactly (via `Path(filename).suffix`), so `.html`/`package.json`/`.pyc` can't be mistaken for `.h`/`.js`/`.py`.
- **`skills/worker-routing/routing-config.json`** — the source of truth for which models/CLIs count as "workers." See [Configuring workers](#configuring-workers) below.
- **`install.sh [target_project_dir]`** — copies the skill files into every supported agent target directory, generates `AGENTS.md`/`CLAUDE.md` in the target project (defaulting to the current directory) from `protocol.md`, and injects the enforced protocol block into `~/.gemini/GEMINI.md` (Antigravity's global instruction file) — backing up any pre-existing file the first time it's touched.
- **`uninstall.sh [target_project_dir]`** — removes the installed skill directories, restores/removes the generated `AGENTS.md`/`CLAUDE.md`, and strips the protocol block back out of `~/.gemini/GEMINI.md`.

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

Full command syntax for `agy`, `claude`, `codex`, and the LM Studio REST API is in [`skills/worker-routing/SKILL.md`](skills/worker-routing/SKILL.md).

---

## Configuring workers

`skills/worker-routing/routing-config.json` is the single source of truth for what counts as a "worker" during auditing. It maps each role in the agent mesh to a display `name` and a list of `patterns` — substrings that identify that worker's CLI invocation in a conversation log:

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
- `routing_check.py --regex` flattens every role's `patterns` into one regex-escaped alternation (e.g. `(claude -p|codex|agy|...)`) and prints it to stdout. Kept for backwards compatibility with external tooling that shells out to it directly.
- `routing_check.py <log-file>` is the full audit: it parses the log into steps, uses the same patterns to decide whether each step's `run_command` calls actually invoked a recognized worker CLI, and prints the complete metrics report. `routing-audit.sh` is a thin wrapper around this mode — it only locates the right log file and relays the exit code.

### Customizing for your own stack

To swap in different models or tools, edit `routing-config.json` — no changes to the shell script or Python are needed:

- **Different CLI for an existing role** — change `patterns`, e.g. point `heavy_doer` at a different command.
- **Local models via Ollama** — add a pattern matching your invocation, e.g. `"patterns": ["ollama run"]`.
- **A custom in-house script** — add its invocation string, e.g. `"patterns": ["./scripts/my-worker.sh"]`.
- **New role** — add a new top-level key with `name` and `patterns`; it's picked up automatically by both `--regex` and log-file checks.

Patterns are treated as literal substrings (regex-escaped internally), so no special quoting is needed — just list the exact text that appears in your logs when that worker is invoked.

After editing the repo's copy, re-run `bash install.sh` — the installer only copies `routing-config.json` into `~/.gemini/config/skills/worker-routing/` if it isn't already there, so your installed customizations are preserved across upgrades. To force-refresh an installed config, delete the installed copy first, then re-run `install.sh`.

---

## Setup

```bash
git clone https://github.com/liorparente/antigravity-worker-routing.git
cd antigravity-worker-routing
bash install.sh
```

Running `install.sh` again is safe — it does not duplicate the protocol block in `~/.gemini/GEMINI.md`, and file copies are simple overwrites.

By default the installer targets the current directory as the project it installs into. Pass a different path to install into (or dogfood against) another project without `cd`-ing there first:

```bash
bash install.sh /path/to/some/other/project
```

### What the installer does

1. Creates each supported target directory if it doesn't already exist (`~/.gemini/config/skills/worker-routing/`, `~/.codex/skills/worker-routing/`, and local `.agents/`, `.agent/`, `.codex/` copies inside the target project).
2. Copies `SKILL.md`, `routing-audit.sh`, and `routing_check.py` into each directory (always overwritten).
3. Marks `routing-audit.sh` executable (`chmod +x`).
4. Copies `routing-config.json` into each directory **only if it doesn't already exist**, so any customizations you've made to your installed copy (see [Configuring workers](#configuring-workers)) survive re-running the installer.
5. Generates `AGENTS.md` and `CLAUDE.md` at the target project root from `skills/worker-routing/protocol.md` — the single source of truth for the enforced protocol text. Any pre-existing file is backed up to `<file>.bak` the first time it's touched, and that backup is never overwritten on subsequent runs.
6. Backs up `~/.gemini/GEMINI.md` to `~/.gemini/GEMINI.md.bak` the first time it's touched.
7. Injects the protocol block between two versionless sentinel markers (`# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ===` / `... END ===`) — the hard gate, mandatory response template, complexity matrix, and escalation triggers that Antigravity reads on every session start. If a block already exists between those markers, it's replaced in place. If a legacy v3.0 block (from older versions of this installer) is found instead, it's removed and replaced with the new versionless block automatically.

### Uninstalling

```bash
bash uninstall.sh [target_project_dir]
```

This removes the installed skill directories, cleans up the generated `AGENTS.md`/`CLAUDE.md` (restoring the `.bak` backup if one exists, or simply deleting the file if it was purely generated), and, after backing up `~/.gemini/GEMINI.md` to `~/.gemini/GEMINI.md.bak`, strips the protocol block back out (recognizing both the current versionless markers and the legacy v3.0 heading). Everything else in `GEMINI.md` is left untouched.

---

## Usage

Once installed, Antigravity's global instructions (`~/.gemini/GEMINI.md`) enforce the routing gate on every state-modifying action: before writing a file or running a non-read-only command, it must declare `[ROUTING: {worker} — complexity: {level} — reason: ...]` or `[ROUTING: Direct — reason: {allowed exception}]` as the first line of its response. Allowed direct exceptions are read-only operations, documentation/`.md`/`.html` edits, MCP tool calls, and QA routing itself — never source code.

### Verifying compliance

Run the audit script against the most recent Antigravity conversation:

```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh
```

Or target a specific conversation by ID:

```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh <conversation-id>
```

The audit log can be plain text (`overview.txt`, split on `Step N:` markers), a single JSON document (`steps` array), or JSON Lines (`transcript.jsonl`, one step per line) — `routing-audit.sh` auto-detects which one exists for the conversation. All metrics are computed strictly within each step's own boundaries, so a worker call in one step can never be mistaken for routing in another, and only an actual `run_command` tool call's `CommandLine` counts as a worker invocation — a conversational mention of a worker's name in prose never does.

The script reports:
- Total file-write tool calls and how many targeted source code files (matched by exact extension against `code_extensions` in `routing-config.json`)
- Number of `[ROUTING:]` declarations and worker CLI invocations found in the log
- `[ROUTING: Direct] → code edit` violations (direct routing declared for a step that also wrote a source code file)
- A 🔴 violation if source code was edited with zero worker calls
- A 🟡 warning if code edits outnumber worker calls, or no routing declarations were found at all
- A breakdown of which source files were touched, by edit count

Exit codes: `0` — audit ran, no violations. `1` — audit ran, violations found. `2` — the audit itself couldn't run (no conversation/log found, or the log/config failed to parse) — it fails closed rather than silently treating an unreadable log as clean. Wire it into CI or a pre-commit hook if you want enforcement at that layer.

---

## License

MIT — see [LICENSE](LICENSE).
