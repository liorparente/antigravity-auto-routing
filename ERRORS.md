# Worker Routing Fallbacks

## 2026-07-25 — `agy` deep-research worker unavailable

- Mission: ultra-high-effort review of the external `implementation_plan.md`.
- Failure: `agy` 1.1.7 could not create its logs or bind `127.0.0.1:0` in the managed sandbox (`operation not permitted`).
- Fallback: use the protocol-approved read-only Codex 5.6 Sol path for repository research, followed by a separate ultra-effort Codex 5.6 Sol critic pass.
- Scope impact: `agy` exited before reading the plan or repository; no research result was lost or partially trusted.

## 2026-07-25 — Codex CLI fallback unavailable

- Mission: read-only repository research for the same plan review.
- Failure: `codex-cli` 0.144.1 could not initialize its in-process app-server client in the managed sandbox (`operation not permitted`).
- Fallback: delegate independent read-only research and review passes to built-in worker agents, then synthesize only their evidence-backed findings.
- Scope impact: the CLI exited before producing a review; no partial output was trusted.

## 2026-07-25 — CLI research workers unavailable for calibration hardening

- Mission: deep research for HMAC calibration verification, unsafe-chain metrics suppression, and the multi-pass council debate.
- Failure: `agy` could neither create its runtime logs nor bind `127.0.0.1:0`; the protocol-approved Codex 5.6 Sol fallback could not initialize its in-process app-server client. Both failed with `operation not permitted`.
- Fallback: use a built-in read-only research worker, followed by delegated implementation and independent QA.
- Scope impact: both CLIs exited before reading or modifying repository files; no partial output was trusted.

## 2026-07-25 — Claude implementation worker unavailable

- Mission: implement the reviewed calibration HMAC, metrics suppression, council debate, and unit-test changes.
- Failure: Claude Sonnet 4.6 could not reach its API endpoint (`ENOTFOUND`) and exited without yielding implementation output.
- Fallback: use a built-in implementation worker with the same three-file scope, followed by an independent QA worker.
- Scope impact: the Claude CLI produced no file changes or partial result.

## 2026-07-25 — CLI research fallback unavailable for lint/type repair

- Mission: inspect the three worker-routing Python files before Ruff and mypy repair.
- Failure: `agy` failed before inspection due sandbox log/bind permissions; the Codex CLI fallback then failed before inspection due in-process app-server permissions.
- Fallback: use a delegated built-in research and execution worker.

## 2026-07-25 — Definitive Resolution for CLI Worker Socket Permission Errors

- Issue: CLI workers (`codex exec`, `claude -p`, `agy -p`) failed with `Operation not permitted (os error 1) - failed to initialize in-process app-server client` when invoked inside `run_command` in standard IDE sandbox mode (`BypassSandbox: false`).
- Root Cause: IDE sandbox process isolation blocks local loopback socket binding (`127.0.0.1:0`) and IPC pipes required by in-process app-servers. macOS "Full Disk Access" (TCC) settings have no effect on IDE subprocess sandbox rules.
- Permanent Resolution: Mandated Rule 4.7 in `protocol.md` requiring `BypassSandbox: true` on `run_command` for all external CLI worker invocations. Synchronized across `AGENTS.md`, `CLAUDE.md`, `~/.gemini/GEMINI.md`, and project skill rules via `./install.sh`.

## 2026-07-27 — CLI research fallback for BypassSandbox cleanup

- Mission: research the code-review loose ends in the BypassSandbox documentation and synchronization tests.
- Failure: `agy` could not write its runtime logs or bind `127.0.0.1:0` in the managed sandbox.
- Fallback: used independent built-in read-only workers to inspect the canonical sources and tests.
- Scope impact: `agy` exited before repository analysis; no partial output was trusted.

## 2026-07-27 — CLI fallbacks for RoutingAuditEngine plan critique

- Mission: deep research and final Critic review of `.scratch/plan_draft.md`.
- Failures: `agy` 1.1.7 failed before repository access because the managed sandbox denied `~/.gemini` log/crash writes and localhost `127.0.0.1:0` binding; the Codex 5.6 Sol CLI then failed before repository access because its in-process app-server could not initialize (`Operation not permitted`).
- Fallback: used three built-in read-only research workers; after two bounded documentation workers stalled without writing, materialized the Markdown artifacts under the protocol's documentation-only exception and sent them to a returning worker for independent read-only QA.
## 2026-07-27 — Deprecated Claude 4.6 Model Retirements & V5 Standardization

- Mission: Update retired Claude 4.6 model identifiers (`claude-sonnet-4.6`, `claude-opus-4.6`) to active models (`claude-sonnet-5`, `claude-opus-5`).
- Issue: Anthropic retired `claude-sonnet-4.6` and `claude-opus-4.6` CLI endpoints on June 15, 2026, causing external CLI calls using those parameters to fail.
- Resolution: Standardized `protocol.md`, `routing-config.json`, `SKILL.md`, and `test_routing.py` on active v5 models (`claude-sonnet-5`, `claude-opus-5`). Enhanced `routing_check.py` with numeric version matching (`re.search(r'\b\d+(?:\.\d+)?\b', declared_worker)`) to strictly detect version drift against declared routing headers.
- Verification: Ran `./install.sh` to update system-wide targets and verified all 76 unit tests pass (`OK`).

## 2026-08-04 — Claude CLI Positional Argument & Context Leak

- Issue: The Claude CLI (`claude -p -c ... "Prompt" < /dev/null`) ignored the positional prompt argument due to the flag chain and `/dev/null` redirection.
- Consequence: Treating the prompt as empty, the CLI defaulted to loading its stateful project history (e.g., from a `.claude` directory) and answered based on an old conversation context (e.g., Phase 3 Auth) instead of the intended prompt.
- Resolution: Pipe the prompt strictly through `stdin` using `echo "..." | claude -p -`. This forces the CLI to read the exact input and prevents it from falling back to cached stateful history.
- Correction (2026-08-10): the diagnosis above was wrong, and its prescribed remedy was never applied to any command template. `< /dev/null` was not the cause — `-c` was. In the Claude CLI, `-c` is `--continue`, a boolean flag that resumes the most recent conversation in the current directory; it is not a config-override flag as it is in `codex`. The "flag chain" therefore did not swallow the prompt, it explicitly asked the CLI to reload prior conversation state, which is exactly the reported symptom. Root cause fixed on 2026-08-10 by replacing `-c model_reasoning_effort="high"` with `--effort high` across all templates. The stdin-pipe remedy is superseded: the project standard remains the `< /dev/null` guard of Rule 4.6, and `skills/worker-routing/REFERENCE.md` was brought into compliance with it on the same date (six examples were missing the guard entirely).

## 2026-08-06 — Worker Sandbox Blocks Git Operations

- Issue: Delegating basic version control operations (`git branch`, `git checkout`) to CLI workers (`codex exec`, `claude -p`) failed because the worker sandbox locks the `.git/` directory (`Operation not permitted`).
- Consequence: Orchestrator workflows that require branching or reverting were blocked because the routing protocol strictly forbade the Orchestrator from running these commands directly.
- Resolution: This entry originally claimed `skills/worker-routing/protocol.md` had been updated to add version control to the "Allowed Direct Actions" list. **That update was never actually applied** — `protocol.md` continued to permit only read-only diagnostics (`git status`, `git log`, `curl` health checks), so the deadlock this entry describes persisted for over a month. The fix was actually applied on 2026-08-10, with a narrower command list than originally claimed: `git add`, `git commit`, `git branch`, `git checkout`, `git revert`, `git stash`, `git tag` are direct-allowed; `git push`, `git reset --hard`, `git clean -fd`, and any `--force` variant remain explicitly forbidden without user approval (see ADR 0006). Lesson: a Resolution line in this file is a claim, not a guarantee — verify the target file actually changed before trusting an entry's account of its own fix.

## 2026-08-06 — Background Worker Collision & Assumed Codebase State

- Issue: The orchestrator launched a nested worker (`codex exec`) in the background to implement Phase 2, but incorrectly assumed the worker failed due to a misread log tail, and simultaneously misjudged the codebase state by assuming a "Phase 0 Restoration" commit had reverted the source files when it had only reverted documentation.
- Consequence: The orchestrator almost duplicated the worker's effort, experiencing cognitive dissonance when viewing files that were being actively mutated by the background worker, and when discovering Phase 3-5 implementations that were never actually reverted.
- Resolution: Always use the `manage_task` tool with `status` to ensure background workers have fully terminated before inspecting their output. Additionally, never assume a codebase was cleanly reverted based on a commit message alone without verifying `git diff` or `git log --stat` on the source files.

## 2026-08-07 — Claude Code Session State Leak in Background Workers

- Issue: The CLI worker (`claude -p`) was continuing conversations from the last active session in the workspace instead of starting a fresh, isolated context for each new task. This resulted in workers acting on unrelated context (e.g., from old tasks) and creating cognitive dissonance.
- Consequence: Worker tasks in new sessions incorrectly referenced plans or context from prior, completed tasks in the same project directory, causing hallucinations and incorrect implementations.
- Resolution: Added the `--no-session-persistence` flag to all `claude -p` invocations in the worker routing protocol (`protocol.md`, `SKILL.md`, `REFERENCE.md`). This flag prevents Claude Code from saving or resuming disk-based session history, guaranteeing a stateless, clean slate for every worker invocation.

## 2026-08-07 — Brittle Test Assertions on Protocol Commands

- Issue: After updating `protocol.md` to include `--no-session-persistence`, the CI tests failed because `test_routing.py` contained hardcoded strings expecting the exact previous command structure.
- Consequence: The `unit-tests` GitHub Action failed, blocking the pipeline despite the logic being correct.
- Resolution: Updated all hardcoded `claude -p` assertion strings in `test_routing.py` to match the new protocol command exactly. Moving forward, any change to CLI command shapes in the documentation or protocol must be accompanied by synchronous updates to the test suite's expected string assertions.

## 2026-08-10 — Protocol Self-Reference: Workers Self-Blocking on AGENTS.md

- Issue: `codex exec` workers routed inside this repo returned `[ROUTING: BLOCKED]` without touching code, three consecutive times. Codex CLI auto-loads the repo-root `AGENTS.md` as project instructions, and `install.sh` injects the full orchestrator protocol there. The worker therefore received "you are a pure orchestrator, self-execution is a protocol violation" as its highest-priority instruction and correctly obeyed it.
- Root Cause: The only exemption was `IN_WORKER_ROUTING=true`, an environment variable. A model cannot read environment variables — they are not in its context — and verifying one requires running a shell command, which is the very action behind the gate. Circular. Independently, Codex strips non-core variables from the sandboxed shell under its default `shell_environment_policy.inherit = "core"`, so the variable would have read empty even if checked. An unobservable exemption always resolves to "not exempt".
- Consequence: Every worker routed to Codex in an installed repo halted. The documented fallback chain masked the defect as a per-vendor outage (logged against Codex Terra) rather than a protocol design fault, which is why it recurred instead of being diagnosed. Not a vendor failure: nothing was unreachable, and the worker did exactly what the document told it to do.
- Resolution: Protocol v3.5 adds a `## 🚦 READ THIS FIRST — Worker Mode Override` section at the very top of `protocol.md`, ahead of the orchestrator identity statement. The exemption now keys on the token `[WORKER-MODE: AGY-NESTED-EXEC]` carried *inside the worker's prompt* — an observable value — instead of an invisible environment variable. All worker command templates in `protocol.md`, `SKILL.md`, and `REFERENCE.md` now embed the token in their prompt argument (`codex review` is flagless, so it takes the token as its positional `[PROMPT]`). The token is emitted only by the orchestrator's own command templates and must never be self-issued, which preserves the gate for a primary-session agent (ADR 0005, Pillar 1) while exempting genuine nested workers.
- Rejected Alternative: Stripping the protocol block from `AGENTS.md` would have fixed the symptom without touching Antigravity (its gate lives in `~/.gemini/GEMINI.md`), but would have silently removed gating for Codex CLI and Claude Code as primary session agents — a regression against ADR 0005, Pillar 1.
- Verification: `expected_commands` in `test_routing.py` synchronized in the same change (per the 2026-08-07 brittle-assertion lesson); 82 tests pass. End-to-end: a Terra worker carrying the token executed its mission instead of blocking, and a worker without the token still blocks.

## 2026-08-10 — Invalid CLI Flags Copied Across Worker Templates

- Issue: Two invalid-flag defects had shipped into every documented `codex review` and `claude -p` command template. (1) `codex review --uncommitted -s workspace-write ...` — `codex review` (unlike `codex exec`) has no `-s`/`--sandbox` flag at all (confirmed against `codex review --help`, codex-cli 0.144.1); the flag was silently ignored or rejected depending on parser strictness. (2) `claude -p ... -c model_reasoning_effort="high" ...` — `claude`'s `-c` is short for `--continue` (resume the most recent conversation), not a config override; the real reasoning-effort flag is `--effort <low|medium|high|xhigh|max>` (confirmed against `claude --help`).
- Consequence: The `codex review` QA command carried a dead flag with no effect on sandboxing. The `claude -p` command was worse: passing `-c` silently set `--continue` on every worker invocation, re-introducing the exact stateful-session-leak risk that `--no-session-persistence` (2026-08-07 entry above) was added to eliminate — a worker could resume a prior conversation's context via `-c` even with `--no-session-persistence` present, because `-c` and `--no-session-persistence` are independent flags with no interaction check. `--allow-dangerously-skip-permissions` alone was also insufficient to actually bypass permissions: per `claude --help`, it only *enables* the bypass option without activating it — the activating flag is `--permission-mode bypassPermissions`.
- Root Cause: Codex CLI's `-c key=value` config-override syntax was copied onto the `claude` and `codex review` command templates without checking each subcommand's own `--help` output. `-c` happens to be a valid short flag on both CLIs but means something completely different on each — a coincidental syntax collision that went unnoticed because both commands appeared to run.
- Resolution: Replaced `-s workspace-write` on `codex review` with `-c sandbox_mode="workspace-write"` (empirically verified honored: the resolved sandbox mode reflected the override, and an invalid value was rejected by the config-value enum). Replaced `-c model_reasoning_effort="high"` with `--effort high` on all `claude -p` commands, and added `--permission-mode bypassPermissions` alongside `--allow-dangerously-skip-permissions`. Updated all four documented locations (`protocol.md`, `SKILL.md`, `REFERENCE.md`, `test_routing.py` `expected_commands`) plus the two additional `claude -p` examples in `REFERENCE.md` that shared the permission-mode defect. Rule 4 in `protocol.md` now states explicitly that `codex exec` selects sandbox mode via `-s`/`--sandbox` while `codex review` has no such flag and uses `-c sandbox_mode=` instead, to prevent this from reading as a blanket "-s" rule again.
- Verification: `./install.sh` re-rendered `AGENTS.md`, `CLAUDE.md`, and `.claude/rules/worker-routing.md` from the corrected `protocol.md`; `.venv/bin/python skills/worker-routing/test_routing.py` reports OK. Lesson: a flag's presence in one CLI's `--help` output is not evidence it exists on a *different* CLI's subcommand — verify per-subcommand, not per-vendor.

## 2026-08-10 — Contradictory Sandbox Guidance: `TMPDIR`/`GIT_OPTIONAL_LOCKS` vs Protocol Rule 4.7

- Issue: `knowledge/institutional-memory.md` claimed setting `TMPDIR=/tmp` and `GIT_OPTIONAL_LOCKS=0` fully resolved worker socket initialization errors (`Operation not permitted (os error 1)`), contradicting `protocol.md` Rule 4.7 which states `BypassSandbox: true` is strictly required on `run_command`.
- Empirical Test Command: `TMPDIR=/tmp GIT_OPTIONAL_LOCKS=0 IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] echo test" < /dev/null`
- Empirical Test Result: Failed immediately with `Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)`. Re-running the exact same invocation with `BypassSandbox: true` succeeded (`exit code 0`). Environment variables do not resolve macOS IDE process socket isolation.
- Resolution: Updated `knowledge/institutional-memory.md` to remove the false claim and explicitly defer to `protocol.md` Rule 4.7 (`BypassSandbox: true`).
- Systemic Pattern: Third instance of documented resolutions found not matching empirical reality. Always require empirical test evidence before writing workarounds into institutional memory.

## 2026-08-10 — `codex review` Positional Prompt Argument Syntax

- Issue: Invocations of `codex review --uncommitted "Prompt"` failed with `error: the argument '--uncommitted' cannot be used with '[PROMPT]'`.
- Cause: Codex CLI positional `[PROMPT]` argument is mutually exclusive with `--uncommitted` flag in `codex review`.
- Resolution: Omit `--uncommitted` when providing a explicit positional prompt string (e.g. `codex review -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null`).

## 2026-08-10 — Protocol `install.sh` Requires `BypassSandbox: true` for Home Directory Writes

- Issue: Running `install.sh` inside standard IDE sandbox (`BypassSandbox: false`) failed with `cp: ~/.gemini/config/... Operation not permitted`.
- Cause: `install.sh` synchronizes protocol files to global home directory targets (`~/.gemini/config/skills/worker-routing/`, `~/.codex/skills/worker-routing/`, `~/.gemini/GEMINI.md`) outside the workspace.
- Resolution: Always invoke `install.sh` via `run_command` with `BypassSandbox: true` to permit global home directory target synchronization.

## 2026-08-10 — Post-Review Maintenance Backlog Execution (Tickets 08 & 09)

- Mission: Settle ADR 0002 debt (Ticket 08) and triage unreferenced helpers (Ticket 09) via worker CLI routing.
- Key Learnings:
  1. Standard Sandbox Worktrees: `git worktree add` to directories outside the workspace (`../auto-routing-backlog`) is blocked by IDE sandbox boundaries; creating worktrees inside workspace subdirectories (`.worktrees/backlog`) works cleanly inside standard sandbox mode.
  2. Pure Frozen Dataclass Pattern: `SecurityContext` in `@dataclass(frozen=True)` avoids `__post_init__` `object.__setattr__` mutation by resolving secrets in factory methods (`SecurityContext.create()`), ensuring pure immutability and simple assignment error testing.
  3. Telemetry and Fail-Closed Routing: `AgentCouncil.route_task` combines sensitivity classification (`evaluate_sensitivity`), endpoint probing (`check_local_model_endpoint`), and telemetry logging (`log_routing_telemetry`), failing closed via `record_local_model_failure`.
- Verification: 100 tests pass in `skills/worker-routing/test_routing.py` (`OK`).

## 2026-08-10 — CI Mypy Type Checking Resolution on Dynamic JSON Dictionary Lookups

- Mission: Resolve GitHub Actions CI workflow failure on commit `61d41c1`.
- Root Cause: `_valid_debate_rounds` parameter annotations were typed as `list[dict[str, Any]]` and `int`. Callers passing `manifest.get("debate_rounds")` and `manifest.get("consensus_round")` (which return `Any | None`) triggered `mypy` error `Argument 1 to "_valid_debate_rounds" has incompatible type "Any | None"; expected "list[dict[str, Any]]"`.
- Resolution: Typing dynamic dictionary validation helpers to accept `Any` parameter types allows `manifest.get(...)` calls to pass without `mypy` type friction while internal `isinstance()` runtime guards ensure strict validation.



## 2026-08-11 — Ticket 07 Production Worker Invoker, Code Review & Deployment

- Mission: Implement Ticket 07 (`Production Worker Invoker`), execute Two-Axis Code Review (Standards + Spec) via Codex 5.6 Sol, fix code review findings, and deploy/synchronize via `install.sh`.
- Key Learnings & Failure Patterns:
  1. **`uninstall.sh` File List Synchronization**: Adding new managed files (`advisory_consultation.py`, `production_invoker.py`) to `install.sh`'s `MANAGED_FILES` array without updating `uninstall.sh`'s `rm -f` file list breaks uninstallation tests (`test_uninstall_sh_removes_generated_docs`). `rmdir` fails to delete target directories because non-deleted files remain. *Rule: Always update `uninstall.sh` file cleanup list whenever modifying `install.sh` `MANAGED_FILES`.*
  2. **Strict Worker Token Prefix Guard (`startswith`)**: Using substring match (`WORKER_MODE_TOKEN in prompt`) allows prompts discussing the token mid-prompt to bypass prepending, causing workers to self-block at the routing gate. *Rule: Always check `prompt.startswith(WORKER_MODE_TOKEN)`.*
  3. **Display Model Name Normalization (`MODEL_ALIASES`)**: High-level orchestrators use display labels (`"Claude Opus 5 (Thinking)"`, `"Codex 5.6 Sol"`, `"Gemini 3.6 Flash (High)"`), whereas CLI workers require strict model identifiers (`claude-opus-5`, `gpt-5.6-sol`, `gemini-3.6-flash`). *Rule: Use explicit `MODEL_ALIASES` normalization dictionary that fails closed (`ValueError`) on unmapped names.*
- Verification: 10/10 invoker tests OK, 127/127 routing tests OK (`test_routing.py`), `ruff` and `mypy` 0 errors. Committed `ae76189`.



## 2026-08-11 — Spec Status vs Git Commit History Drift

- Mission: Verify open tasks and spec status against repository state.
- Root Cause: `docs/specs/0001-advisory-consultation.md` and `docs/specs/0002-post-review-maintenance-backlog.md` retained `Status: Ready for agent` header after their underlying tickets had been implemented and committed to Git. This caused false-positive reports of open tasks.
- Resolution: When implementing specs, update the status header in `docs/specs/` to `Status: Implemented` upon completion.
- Correction (same day): the status flip on spec 0001 was premature when made, and its original Verification line was wrong. It claimed commits `dc91a72` through `ae76189` "implemented all tickets" while ticket 06 (transcript and telemetry) was still being written in a parallel session; ticket 06 landed afterwards in `816b3c8`. The claimed count of 137 passing tests was also stale — it was the pre-ticket-06 figure (127 in `test_routing.py` plus 10 in `test_production_invoker.py`). Spec 0001 became genuinely complete only once `816b3c8` landed, at which point `test_routing.py` reports 144.
- Lesson: verifying a spec's status against `git log` alone is not sufficient when another session holds uncommitted work — the working tree of every active session is part of the repository state. Cross-check `git status` and in-flight tickets before declaring a spec implemented. This is a second instance of the pattern already recorded on 2026-08-06: a Verification line in this file is a claim, not a guarantee.

## 2026-08-11 — A Truncated Digest Is Not Redaction (Ticket 06)

- Mission: give every AdvisoryConsultation a telemetry record carrying a task identity, without breaching the module's documented redaction boundary ("nothing derived from `task_description` reaches a reason beyond the matched marker constant").
- Issue: the orchestrator's mission brief specified a truncated SHA-256 digest of the task description as a "stable, non-revealing" default identity, and the implementing worker's docstring then asserted it "must carry no recoverable information". Both were wrong. On the `sensitivity_halt` path the task text is known to contain a credential, and a 64-bit digest over guessable text is a confirmation oracle: anyone who guesses the task can verify the guess against the logged identity.
- Why it survived implementation: the guarding test asserted only that the secret *substring* was absent from the artifacts. A derived value is structurally invisible to a substring assertion, so the test could never have failed on this.
- Detection: the Standards axis of the two-axis review. The Spec axis reviewed the same code in parallel and reported the boundary intact — accurately, because it asked whether the secret *appears*, while Standards asked whether anything *derived* escapes. The disagreement between the axes was the finding.
- Resolution: `_resolve_task_id` now keys on outcome. A caller-supplied `task_id` wins on every path and is the production route; non-halt outcomes keep the digest; `sensitivity_halt` uses `secrets.token_hex(8)`, unrelated to the task text. The transcript and the telemetry record for the same halt carry the same random id, so correlation survives. The guarding test now also asserts the emitted identity is not equal to the digest of the task text.
- Lesson: when a documented rule says "nothing *derived* from X", it means derived — hashing is a transformation, not a redaction. Assert the property, not the absence of one literal string.

## 2026-08-11 — A Worker's Report About Files It Does Not Own Is a Guess

- Issue: a CLI worker resolving code-review findings reported that four documentation files were "untouched by me — their diffs are unchanged from before this session, confirmed by inspecting `git diff --stat`". `knowledge/institutional-memory.md` had in fact grown from 7 to 12 changed lines during that same window, written by a different Claude Code session working on the repository concurrently.
- Consequence: none this time, because the orchestrator diffed the file independently. Taken at face value it would have hidden the existence of the parallel session, which was material to the commit decision.
- Root Cause: the worker was asked to report `git status` and did so honestly for the files it changed, then extended the same confidence to files it never opened. A statement about a file the worker did not write is an inference from a stale snapshot, not an observation.
- Resolution: treat a worker's file-state claims as covering only the files it edited. Verify everything else with `git status` / `git diff` in the orchestrator session, on the same footing as re-running the test and lint gates rather than trusting the reported output. Related: the 2026-08-06 entry above, where assuming a clean revert from a commit message alone nearly caused duplicated work.


## 2026-08-11 — LM Studio Resource Safety Guardrail Failure on Large Default Context Lengths

- Issue: Attempting to on-demand load MLX models in LM Studio (such as `qwen3-coder-next-mlx`) via OpenAI-compatible API (`/v1/chat/completions`) failed with HTTP 400 (`Failed to load model... Error: Model loading was stopped due to insufficient system resources.`).
- Root Cause: By default, `qwen3-coder-next-mlx` declares a `max_context_length` of 262,144 tokens (256K). On JIT auto-load, LM Studio attempts to allocate KV cache memory for the full default context size, triggering LM Studio's pre-load resource guardrails even when sufficient RAM exists for smaller context windows.
- Resolution: Restricted `Context Length` in LM Studio's load parameters/presets to `8,192` or `16,384` tokens (via GUI `+ Load Model` or `Model Defaults`), reducing RAM allocation requirements by >70% and allowing the model to load and serve API requests cleanly.

## 2026-08-12 — Two Writers, One Tree

- Mission: land spec 0003's remaining tickets and reconcile with spec 0004.
- Issue: two separate Claude Code sessions wrote to `advisory_consultation.py`/`test_routing.py` on the same `main` working tree at the same time, on 2026-08-12. Neither session was aware of the other until the resulting confusion forced a coordination handoff.
- Consequence: a session had to spend real effort re-establishing ground truth (whose edits were whose, what was actually committed vs. still in flight) before any further work could safely proceed. A stale worktree (`review-snapshot`, detached at `23a138c`) was left behind from that period and was only cleaned up on 2026-08-13, after confirming its one commit was already reachable from the real branch history and nothing would be lost.
- Resolution: single-writer, sequential discipline for the rest of the session — one working tree per active session, never two sessions editing the same branch's tree concurrently. Before deleting an orphaned worktree, always confirm its HEAD commit is already an ancestor of a real branch (`git branch --contains <sha>`) rather than assuming it is safe to discard.
- Lesson: a shared working tree is shared mutable state. Two agents (or two sessions of the same agent) writing to it without coordination is the same class of bug as two threads writing to memory without a lock — the fix is the same too: one writer at a time, enforced structurally, not by convention alone.

## 2026-08-13 — Checked Is Not Run

- Mission: reconcile main into spec/0004-learning-loop; verify the merged CI configuration.
- Issue: `.github/workflows/test.yml` can list a `test_*.py` file in its ruff/mypy module list without that file ever appearing in the step that actually executes tests. This already happened once, silently, to `test_production_invoker.py` (sixteen tests never run despite CI staying green) — spec 0004 later added `test_ci_runs_every_test_file_it_checks` specifically to catch a repeat.
- Root Cause: linting and executing are two independent CI steps reading two independently-maintained lists; nothing forces them to agree except discipline, until a test asserts it.
- Detection: the new guard test itself failed immediately after this merge, because `test_lmstudio.py` (a live-LM-Studio-server smoke suite, checked but deliberately never executed — its own module docstring says so) was newly added to the checked list without being added to the executed list.
- Resolution: narrowed the guard test's assertion with one named, commented exception (`_CHECKED_BUT_NOT_EXECUTED_BY_DESIGN = frozenset({"skills/worker-routing/test_lmstudio.py"})`) rather than loosening it for every file — a second, accidental gap (a new `test_production_invoker.py` incident) is still caught.
- Lesson: "CI checks this file" and "CI runs this file's tests" are different claims. Either assert their equality as a test, with named exceptions for genuinely unrunnable files, or expect the gap to reopen silently the next time a file is added to one list and not the other.

## 2026-08-13 — A Canary Probe Deleted a Real Mission's Plan

- Mission: implement spec 0003 ticket 11 (the sensitive-task path); caught while reviewing the surrounding budget-degradation code this ticket touches.
- Issue: budget rung 3 (full session exhaustion) returns before any worker is contacted, and its early-return branch unconditionally called `_remove_stale_plan_artifact`, deleting `root_dir / "implementation_plan.md"` — even when the triggering run was a seeded-flaw canary probe (`is_canary=True`), not a real mission. A canary neither creates nor deletes that file, by the canary invariant documented elsewhere in the same module; this path silently violated it.
- Root Cause: the early-return path was written to match "what a real budget-exhausted mission should do" (clean up any stale plan) without checking whether the current run was a mission at all.
- Resolution: fixed in `aa118f1` — the cleanup call is now guarded on `not is_canary`, so the preemption itself stays unconditional (a rung-3 canary still returns `budget_skipped` with zero worker calls) but the file-deleting side effect does not.
- Lesson: an early-return / preemption shortcut inherits none of the guarantees its "normal path" sibling implicitly relies on. Every side effect a shortcut performs (file writes, deletions, telemetry) needs its own check against what kind of run is actually in flight — "this path always means a real mission" is an assumption that needs to be stated and verified, not inherited by association.

## 2026-08-13 — A New Seam Doesn't Reach a Downstream Dispatcher on Its Own

- Mission: implement spec 0003 ticket 11, extending the sensitivity gate's local-only roster resolution across all four dialogue occasions, including post-mortem.
- Issue: `run_advisory_consultation_debate` gained a new `reachability_check`/`roster_config_path` seam so a sensitive task could resolve a local-only roster instead of always halting. `dispatch_post_mortem_consultation` — the actual production entry point for the post-mortem occasion — has its own, separate, narrower keyword-only parameter list and did not expose either new parameter. Its own docstring had explicitly, and previously correctly, declared the roster seam "deliberately not exposed: none has a post-mortem consumer today."
- Consequence: a sensitive task dispatched for post-mortem through the real production API could only ever halt — it could never reach the local-only dialogue the ticket required for that occasion, even though the core function fully supported it.
- Detection: not caught by the implementation's own tests, which called the core function directly. Caught by the Spec-axis code review, which specifically read the dispatch entry point rather than assuming a fix to the core function was sufficient.
- Resolution: threaded `reachability_check`/`roster_config_path` through `dispatch_post_mortem_consultation` and its background-thread target, and rewrote the stale "deliberately not exposed" docstring paragraph to state what became true.
- Lesson: in a system with multiple entry points to shared core logic, a new capability added to the core function does not automatically reach a dispatcher, wrapper, or background-thread target with its own parameter surface. Enumerate every entry point explicitly when wiring through a new seam — don't assume propagation.

## 2026-08-13 — Two Independently-Declared Vocabularies Will Drift

- Mission: verify spec 0004's LearningJournal schema against spec 0003's final telemetry shape, as part of the main → spec/0004-learning-loop reconciliation merge.
- Issue: `advisory_consultation.Occasion` (`Literal["ambiguity", "plan-review", "code-review", "post-mortem"]`, shipped, tested) and `learning_journal.DialogueOccasion` (`Literal["ambiguity", "plan_review", "code_review", "post_mortem"]`, schema-only) are meant to describe the same four-value vocabulary. Three of the four values used hyphens on one side and underscores on the other.
- Root Cause: each module's own test suite was internally self-consistent — `learning_journal.py`'s test helper always hand-supplied its own `"occasion"` string in isolation — and nothing ever constructed a `DialogueQualityRecord` from a real `Occasion` value. Python's type system does not enforce agreement between two separately-declared `Literal` aliases; nothing would have raised until a real writer eventually passed a live `Occasion` value through and hit `ValueError` in `_validate_choice`.
- Resolution: aligned `DialogueOccasion` to `Occasion`'s shipped spelling (the established vocabulary, not the schema-only one), and added `test_cross_spec_vocabularies_agree`, asserting `set(get_args(Occasion)) == set(get_args(DialogueOccasion))` (and the same for the topology vocabularies), so a future drift is caught immediately rather than waiting for a real writer to hit it.
- Lesson: when two modules or two specs are meant to share a vocabulary, don't rely on both authors getting the spelling right independently — pin the agreement with an explicit equality test the moment the second vocabulary is declared.

## 2026-08-13 — A Task Brief Describing Future Work As Present Tense

- Mission: the same reconciliation merge — its own non-negotiable checklist item read "learning_journal.py/learning_outcomes.py consume the FINAL AdvisoryTelemetryRecord shape ... and filter canary records before any aggregation."
- Issue: no code in either file reads `.ralph/routing_telemetry.jsonl` or constructs an `AdvisoryTelemetryRecord`/`DialogueQualityRecord` translation at all. `learning_journal.py`'s own `DialogueQualityRecord` docstring says so plainly: "Spec 0003's machinery writes these; this module owns the contract so both specs agree on the shape before either has a writer." The checklist item's present-tense phrasing described a future capability as if it already existed.
- Consequence: none this time — caught before the checklist item was marked done, by reading the actual code path instead of trusting the item's wording.
- Resolution: recorded the schema-compatibility work that genuinely was done (the occasion/topology vocabulary fix above) and explicitly flagged the consumption/filtering requirement as deferred future work, in the merge commit message, rather than checking off something that doesn't exist.
- Lesson: a "verify X does Y" checklist item can describe intent, not current fact. Before marking it done, verify Y's implementation exists at all — read the actual code path — rather than inferring from the item's own phrasing that the described behavior is live.

## 2026-08-13 — A Process List Answers the Wrong Question About Concurrent Writers

- Mission: satisfy the single-writer rule (see "Two Writers, One Tree", 2026-08-12) before merging `spec/0004-learning-loop` into `main` — the rule existed as a principle but had no verification procedure attached.
- Issue: `ps` was wrong in both directions at once. It showed two live `codex` processes whose command lines carried `[WORKER-MODE: AGY-NESTED-EXEC]` and `gpt-5.6-sol` — the exact signature of this repo's routing protocol — which read as "another session is mid-flight here, halt." It simultaneously showed nothing at all for the session that *was* writing to this tree.
- Root Cause: a command line describes what a process was asked to do, not where it does it. Two different repositories on one machine run the same protocol and therefore produce indistinguishable `ps` output. Conversely, a peer Claude Code session is not a separately identifiable process at the `ps` level, so no amount of process inspection can reveal it.
- Detection: `lsof -a -p <PID> -d cwd` resolved both `codex` processes to `/Users/liorparente/Projects/Yamit-Therapy` — an unrelated repo, and their prompt referenced an `Issue #154` that does not exist here. `ListAgents` then revealed the real peer, `auto-routing-e2`, invisible to `ps`. A third, independent signal had already fired without being recognized: `knowledge/institutional-memory.md` moved from unstaged to committed (`30548e3`) *between two of my own read-only calls* — state changing under a reader is direct proof of another writer, stronger than any inventory of processes.
- Resolution: treated the check as requiring three independent instruments, none sufficient alone — `cwd` resolution to discriminate among processes, `ListAgents` to find peer sessions, and observed state change across one's own calls as the ground-truth signal. Only after all three agreed did the merge proceed.
- Lesson: "is anyone else writing to this tree?" is a question about a working directory, not about a process table. Match the instrument to the question — and when a rule is documented as a principle, write down the procedure that verifies it, or each session will improvise a different and weaker one.

## 2026-08-13 — A Peer Agent's Silence Is Not Consent

- Mission: the same pre-merge coordination — having found the peer session `auto-routing-e2`, ask it directly whether it was still writing before taking the writer role.
- Issue: the coordination message was delivered successfully, and no reply ever came. The peer closed before its next tool round, so the message was never processed. A design that blocks on the reply would have waited indefinitely for an answer that could not arrive.
- Root Cause: `SendMessage` to a peer session is a request, not a handshake. Delivery is confirmed; processing is not, and the recipient may terminate between delivery and its next turn. Nothing in the mechanism distinguishes "still thinking" from "gone."
- Resolution: defined in advance what would constitute resolution in the absence of a reply, and used it — the peer's disappearance from `ListAgents`, plus an independent sweep showing a clean tree, no `index.lock`, no new commits since `17:07:00`, and no file modified in fifteen minutes. Approval to proceed rested on that evidence, never on inferred consent.
- Lesson: when coordinating with another agent, decide up front what decides the matter if no answer comes back. Treating silence as either consent or refusal is a guess; treating it as "gather independent evidence instead" is the only option that terminates. Related to the process-list entry above: the fallback was the same set of instruments used to detect the peer in the first place.

## 2026-08-13 — Unfamiliar Identifiers Read As "Another Project" Without a Single Grep

- Mission: survey open work in this repository; noticed `implementation_plan.md` in the repo root while enumerating candidate tasks.
- Claim I made, twice, and committed to this log in `b5c750a`: that the file's content "belongs to an entirely different project", because it discusses `AuditIssue`, `WARN-01`/`WARN-02` precedence, JSON/SARIF formatters, and a `discovery_ordinal` field, "none of which exist in this codebase."
- The claim was false. Every one of those identifiers except SARIF lives in this repository: `AuditIssue` and its `discovery_ordinal` field at `skills/worker-routing/routing_check.py:164-179`, and the `WARN-01`-over-`WARN-02` precedence rule the plan describes at `routing_check.py:940-948`, spelled out in the same words. The file is this repo's own plan from 2026-08-05/06 (`2fc92d5`, reverted in `27058f8`, scope-restored in `7ac1940`), describing work that was then implemented here.
- Root Cause: I inferred provenance from unfamiliarity. The identifiers were absent from the spec 0001-0004 line I had been reading all session, and I let "absent from the part of the codebase I have in context" stand in for "absent from the codebase" — without running the one grep that would have settled it. One corroborating detail (a link to `~/Documents/Projects/auto-routing`, a path that genuinely does not exist) was over-weighted into confirmation; it is a stale checkout path, not evidence of a different project.
- Compounding failure: the entry's own closing Lesson told the reader to grep the codebase for the file's *path* — advice I followed, which is how the load-bearing-path finding below is correct. It did not occur to me to apply the same instrument to the file's *contents*, which is the check that would have caught the error.
- What was correct, and survives: `skills/worker-routing/advisory_consultation.py` resolves `plan_path = root_dir / "implementation_plan.md"` and both writes and deletes exactly that path — via `_remove_stale_plan_artifact`, which exists to clear "a pre-existing `implementation_plan.md` under `root_dir` from an earlier run", and which caused the canary-deletion bug fixed in `aa118f1`. A stale file at that path is a live input to the CriticalDialogue machinery either way. The file was deleted on the user's instruction; it remains recoverable via `git show 7ac1940:implementation_plan.md`.
- Lesson: "I do not recognize this" is not evidence about a codebase, it is evidence about one's own context window. Before attributing a file to another project, grep for its distinctive identifiers — the check costs one command, and a wrong provenance claim propagates into permanent records, as this one did before being caught one step later.

## 2026-08-13 — A Single-Writer Check Expires the Moment It Is Made

- Mission: the same session — after merging spec/0004, distilling lessons, and deleting the stale plan artifact, commit and push the results.
- Issue: I verified single-writer at 17:20 — peer session gone from `ListAgents`, no process with this repo as its `cwd`, clean tree, no commits or file changes — and then treated that verification as covering the rest of the session. It did not. A new session, `auto-routing-bf`, opened at roughly 17:31 and committed `b266d88` at 17:51:57, sixty-nine seconds before my own `11353da` at 17:53:06. Its commit message even describes my worktree deletion, so it was reading the tree I was writing to.
- Second, worse issue: I staged with `git add -A`. In a shared working tree that command does not stage "my changes" — no such concept exists at the git level — it stages the entire working directory, including any in-flight edit another session has made but not yet committed, and attributes it to my commit message.
- Consequence: none. `11353da` contained exactly the three files I had touched. That was luck: the peer had already committed its work seconds earlier, so there was nothing uncommitted for `-A` to sweep. Had its timing differed by a minute in the other direction, I would have committed its half-finished work under my message, and it would have found its tree mysteriously clean.
- Detection: reading the `git log` output of my own push, which showed `b266d88..11353da` — a parent commit I had not made and had never seen. Note the direction: the push output is what surfaced this, not any check I ran deliberately.
- Resolution: two rules. First, re-verify (`git status`, `git log`, `ListAgents`) immediately before each write burst rather than once at session start — a verification is a snapshot, and its validity window closes the instant another session can open. Second, in any tree that might be shared, enumerate paths explicitly (`git add <path> <path>`) and never use `git add -A` or `git commit -a`. Also messaged the peer directly, declaring which two files I was about to write and stating what I would do if no reply arrived.
- Confirmed afterwards by the peer, which replied to the coordination message: it had committed `b266d88` with an explicit pathspec *precisely because* my staged deletion of `implementation_plan.md` was sitting in the index at that moment and it did not want to sweep it into its own commit. The discipline I failed to apply is the one that protected me.
- Aggravating factor discovered in the same exchange: `b266d88` rewrote the `.scratch` rule in `.gitignore` from `.scratch/` to `.scratch/**` plus re-includes, which promoted sixty ticket, map, and spec files from ignored to tracked (verified: `git ls-files .scratch` returns 60). A `git add -A` in this tree is therefore strictly more dangerous today than it was this morning — sixty files that used to be invisible to it are now sweepable.
- Lesson: the single-writer rule recorded on 2026-08-12 was stated as a property of a session. It is actually a property of an instant. Every gap between checking and acting is an unguarded window, so the check belongs immediately before the act — and the staging command should be narrow enough that being wrong about the window is survivable. Note also that a repo's blast radius for `-A` is not fixed: an ignore-rule change can enlarge it without any notice to sessions already running.

## 2026-08-13 — Deleting a Merged Branch Is Safe for Code and Quietly Wrong for Documents

- Mission: post-merge cleanup — remove the `.worktrees/spec-0004` worktree and delete the fully-merged `spec/0004-learning-loop` branch, locally and on `origin`.
- Verification performed, and sufficient for what it covered: the worktree's HEAD (`bc63316`) was confirmed an ancestor of `main`, present on `origin`, with a clean tree and only caches among ignored files; `git branch -d` was used deliberately over `-D` so the merge check would be re-enforced by the tool. No code or history was lost, and none could have been.
- What the verification did not cover: documents that *name* the branch. Ticket 12's status line in `.scratch/self-improving-orchestrator/` pointed at `spec/0004-learning-loop`, and the deletion turned a correct pointer into a stale one. A peer session retargeted it to `main` in `b266d88`.
- Second, unanticipated cost: the deletion was invisible from outside. The peer session had listed both the branch and the worktree minutes earlier; on its next git call both were simply gone, with no explanation obtainable from inside its own session. It verified the merge independently before reporting, so it told its user nothing was lost — but it correctly reported the disappearance as unexplained, which cost it real time.
- Resolution: two additions to the branch-deletion procedure. Before deleting, `grep -rn "<branch-name>"` across docs, specs, and tickets and retarget every hit. And if any peer session is active, announce the deletion — an unexplained disappearance in a shared tree is an incident from the other side, whatever it is from yours.
- Lesson: `git branch --merged` answers "is the code safe?", which is not the same question as "is anything still pointing at this?". A branch name is an identifier that documents copy; deleting the branch does not update the copies, and nothing warns you that copies exist.
