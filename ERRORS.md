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





