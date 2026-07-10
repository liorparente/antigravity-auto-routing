# antigravity-worker-routing

**Auto Routing & Collaboration Protocol v3.2** — a multi-model orchestration protocol, audit tooling, and installer for the Antigravity CLI ecosystem (Gemini `agy`, Claude Code, Codex).

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
        └── tests/fixtures/            # sample logs (plain text and JSON Lines) used by the tests
```

- **`skills/worker-routing/protocol.md`** — the single source of truth for the hard-enforced protocol (the gate, response template, complexity matrix, escalation triggers). `install.sh` injects it verbatim between sentinel markers in `AGENTS.md` and `CLAUDE.md` at the target project root, and in `~/.gemini/GEMINI.md`, preserving any other custom content already in those files. Edit only this file — the injected copies are refreshed on every install.
- **`skills/worker-routing/SKILL.md`** — the canonical protocol document. Defines the agent mesh (Orchestrator, Context Specialist, Planner, Critic, Heavy/Light Doers, Local/Sensitive Doer, QA/Auditor), the task lifecycle, and CLI command references for `agy`, `claude`, `codex`, and LM Studio — and points to `protocol.md` for the enforced complexity/routing matrix.
- **`skills/worker-routing/routing-audit.sh`** — locates a conversation's log (auto-detecting `overview.txt` or `transcript.jsonl` under `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/`) and hands it to `routing_check.py`, relaying its exit code directly. Accepts `--strict` and relays it through.
- **`skills/worker-routing/routing_check.py`** — the audit engine. Parses the log — plain text or JSON Lines (including Antigravity's own `overview.txt`, which is JSON Lines wearing a `.txt` extension) — into per-step tool calls, then computes every metric (total writes, code-file writes, `[ROUTING:]` declarations, worker CLI calls, and unrouted code edit violations) strictly within each step's own boundaries, so a worker mention in one step can never clear a violation in another. A step that writes a source code file with zero worker calls of its own is a violation regardless of what its `[ROUTING:]` label says. Worker-CLI detection only looks at the actual `CommandLine` of a `run_command` tool call — never at surrounding prose — and code-file detection matches file extensions exactly (via `Path(filename).suffix`), so `.html`/`package.json`/`.pyc` can't be mistaken for `.h`/`.js`/`.py`.
- **`skills/worker-routing/routing-config.json`** — the source of truth for which models/CLIs count as "workers." See [Configuring workers](#configuring-workers) below.
- **`install.sh [target_project_dir]`** — copies the skill files into every supported agent target directory, and injects the protocol block from `protocol.md` between sentinel markers into `AGENTS.md`/`CLAUDE.md` in the target project (defaulting to the current directory) and into `~/.gemini/GEMINI.md` (Antigravity's global instruction file) — preserving any other custom content in those files, and backing up any pre-existing file the first time it's touched.
- **`uninstall.sh [target_project_dir]`** — removes the installed skill directories and strips the protocol block back out of `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` in place, preserving any other custom content. `AGENTS.md`/`CLAUDE.md` are deleted entirely only if nothing but the block was ever there.

---

## The Agent Mesh

The full Agent Mesh table (every role, its primary model, CLI interface, and operational purpose) lives in [`skills/worker-routing/SKILL.md`](skills/worker-routing/SKILL.md#-the-agent-mesh--roles) — this README no longer keeps its own copy, so the two can't drift out of sync.

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

The authoritative Trivial → Sensitive routing matrix is defined once, in [`skills/worker-routing/protocol.md`](skills/worker-routing/protocol.md) — the same hard-enforced text that `install.sh` injects into `AGENTS.md`, `CLAUDE.md`, and `GEMINI.md`. This README and `SKILL.md` used to each carry their own slightly-diverging copy of this table; now both just point here.

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

`routing_check.py <log-file>` reads this file at runtime — it is never hardcoded. It parses the log into steps, uses the configured patterns to decide whether each step's `run_command` calls actually invoked a recognized worker CLI, and prints the complete metrics report. Pass `--strict` to also fail (exit 1) when only a 🟡 warning was emitted, not just on a 🔴 violation. `routing-audit.sh` is a thin wrapper around this mode — it only locates the right log file and relays the flags and exit code.

### Customizing for your own stack

To swap in different models or tools, edit `routing-config.json` — no changes to the shell script or Python are needed:

- **Different CLI for an existing role** — change `patterns`, e.g. point `heavy_doer` at a different command.
- **Local models via Ollama** — add a pattern matching your invocation, e.g. `"patterns": ["ollama run"]`.
- **A custom in-house script** — add its invocation string, e.g. `"patterns": ["./scripts/my-worker.sh"]`.
- **New role** — add a new top-level key with `name` and `patterns`; it's picked up automatically by every log-file check.

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

1. Creates each supported target directory if it doesn't already exist (`~/.gemini/config/skills/worker-routing/`, `~/.codex/skills/worker-routing/`, and local `.agents/`, `.codex/` copies inside the target project).
2. Copies `SKILL.md`, `routing-audit.sh`, and `routing_check.py` into each directory (always overwritten).
3. Marks `routing-audit.sh` executable (`chmod +x`).
4. Copies `routing-config.json` into each directory **only if it doesn't already exist**, so any customizations you've made to your installed copy (see [Configuring workers](#configuring-workers)) survive re-running the installer.
5. Backs up `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` to `<file>.bak` the first time each is touched, and that backup is never overwritten on subsequent runs.
6. Injects the protocol block from `skills/worker-routing/protocol.md` — the single source of truth for the enforced protocol text — between two versionless sentinel markers (`# === ANTIGRAVITY WORKER ROUTING PROTOCOL START ===` / `... END ===`) in `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`. Any other content already in those files is left untouched. If a block already exists between those markers, it's replaced in place. If a legacy v3.0 block (from older versions of this installer) is found instead, it's removed and replaced with the new versionless block automatically.

### Uninstalling

```bash
bash uninstall.sh [target_project_dir]
```

This removes the installed skill directories and strips the protocol block back out of `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` in place — recognizing both the current versionless markers and the legacy v3.0 heading — while preserving any other custom content in those files. `AGENTS.md`/`CLAUDE.md` are deleted entirely only if nothing but the block (and surrounding blank lines) was ever there; `GEMINI.md` is never deleted outright, only the block is removed.

---

## Usage

Once installed, Antigravity's global instructions (`~/.gemini/GEMINI.md`) enforce the routing gate on every state-modifying action: before writing a file or running a non-read-only command, it must declare `[ROUTING: {worker} — complexity: {level} — reason: ...]` or `[ROUTING: Direct — reason: {allowed exception}]` as the first line of its response. Allowed direct exceptions are read-only operations, documentation/`.md`/`.html` edits, MCP tool calls, and QA routing itself — never source code.

### Verifying compliance

Run the audit script against the most recent Antigravity conversation:

```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh
```

Or target a specific conversation by ID, and add `--strict` to also fail on warnings, not just violations:

```bash
~/.gemini/config/skills/worker-routing/routing-audit.sh --strict <conversation-id>
```

The audit log can be plain text (`overview.txt`, split on `Step N:` markers) or JSON Lines (one step object per line) — `routing-audit.sh` auto-detects which one exists for the conversation, and `routing_check.py` auto-detects JSON Lines content regardless of file extension, since Antigravity's own `overview.txt` is written as JSON Lines. All metrics are computed strictly within each step's own boundaries, so a worker call in one step can never be mistaken for routing in another, and only an actual `run_command` tool call's `CommandLine` counts as a worker invocation — a conversational mention of a worker's name in prose never does.

The script reports:
- Total file-write tool calls and how many targeted source code files (matched by exact extension against `code_extensions` in `routing-config.json`)
- Number of `[ROUTING:]` declarations and worker CLI invocations found in the log
- Unrouted code edit violations — a step wrote a source code file but made zero worker calls of its own, regardless of what its `[ROUTING:]` label says
- A 🔴 violation if source code was edited with zero worker calls anywhere in the log
- A 🟡 warning if code edits outnumber worker calls, or no routing declarations were found at all
- A breakdown of which source files were touched, by edit count

Exit codes: `0` — audit ran, no violations (and, with `--strict`, no warnings). `1` — audit ran, violations found (or, with `--strict`, warnings found). `2` — the audit itself couldn't run (no conversation/log found, no steps parsed from a non-empty log, or the log/config failed to parse) — it fails closed rather than silently treating an unreadable log as clean. Wire it into CI or a pre-commit hook if you want enforcement at that layer.

---

## License

MIT — see [LICENSE](LICENSE).
