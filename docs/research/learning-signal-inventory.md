# Learning-Signal Inventory — self-improving-orchestrator, ticket 01

* Date: 2026-08-11
* Sources: repo code/docs at HEAD (81f9dcf) plus user-level skills under `~/.claude/skills/` and sync targets under `~/.gemini/`.
* Question: what does this system already record per action that a continuous learning loop could feed on, and what is still missing?

## 1. Routing telemetry stream — `.ralph/routing_telemetry.jsonl`

One append-only JSONL file, shared by exactly two production writers (`skills/worker-routing/agent_council.py`, `skills/worker-routing/advisory_consultation.py`); path fixed at `agent_council.py:321` and `advisory_consultation.py:652`. Both append via an fcntl-locked, `sort_keys=True` writer (`agent_council.py:108-119`, mirrored byte-identically at `advisory_consultation.py:442-466`). The file does not exist on disk yet at the repo root — only `.ralph/cache/` and `.ralph/decisions/` do.

**Family A — council routing record** (`log_routing_telemetry`, `agent_council.py:130-136`), one per `route_task` call:
`timestamp` (UTC, `%Y-%m-%dT%H:%M:%SZ`), `task_id`, `complexity`, `chosen_worker`, `reason`. No `kind` field.
`chosen_worker` is actually the route action — `route_local` / `route_cloud` / `halt` (`agent_council.py:100-105`, passed at :345-346) — not a concrete model name. `reason` is one of four canned strings (`agent_council.py:336-344`), so no task text can leak through it.

**Family B — advisory consultation record** (`AdvisoryTelemetryRecord`, `advisory_consultation.py:493-499`), exactly one per consultation on every exit path (the `_result` choke point, :654-717):
`timestamp`, `task_id`, `rounds_run`, `outcome`, `planner_model`, `critic_model`, `kind="advisory_consultation"`.
`outcome` is one of `consensus | stalemate | unparseable_verdict | worker_error | sensitivity_halt` (:48-54).

**The `kind` asymmetry:** only advisory records carry `kind`; a council record deliberately has no `kind` at all, and "absence of `kind` = council record" is the documented join rule (`advisory_consultation.py:481-490`, `CONTEXT.md:35-41`). Normalising the asymmetry away would break the audit join.

**Redaction rules (both families):** the record never carries task text or a matched secret value on any outcome (`advisory_consultation.py:616-621`, `CONTEXT.md:41`). Default `task_id` is a truncated `sha256[:16]` of the task text (:310-321) — except on `sensitivity_halt`, where even a digest is treated as a confirmation oracle over guessable task text, so the default is a fresh random `secrets.token_hex(8)` unrelated to the task (:324-349, `CONTEXT.md:43-55`). A caller-supplied `task_id` always wins, on every outcome. Sensitivity is tripped pre-flight by marker list `SENSITIVITY_MARKERS` (:70-79, mirroring `agent_council.py:45-54`); only the marker constant is ever reported, never the surrounding text (:249-262).

## 2. ConsultationTranscript — `.scratch/planning_debate.md`

Written fresh (atomic replace, never appended — :141-154, :432-439) on **every** one of the five outcomes; path at `advisory_consultation.py:651`, mandated by `docs/specs/0001-advisory-consultation.md:134-136` and `skills/worker-routing/protocol.md:118` (Rule 6).

* Four outcomes (`_render_consultation_transcript`, :352-397): outcome, planner/critic model names, rounds-run count, **full task text**, and every round's full Planner proposal + Critic response.
* `sensitivity_halt` exception (`_render_sensitivity_halt_transcript`, :400-429): only the matched marker constant, the halt's `task_id` (for correlation with the telemetry record), and the statement that human approval is required — explicitly "No Planner or Critic was contacted. No task details are recorded here."
* On `consensus` only, the agreed plan is additionally written to `implementation_plan.md` (:650, :761-767). All four non-consensus exits delete any stale plan artifact (:265-287).
* Because the transcript is overwritten per run, it is a *latest-run* artifact, not a history.

## 3. Council decision manifests — `.ralph/decisions/{task_id}.json`

`AgentCouncil.run` materialises one signed manifest per task, including on cache hits (`agent_council.py:770-772`); 24h planning cache at `.ralph/cache/planning_cache.json` (:29, :320, :766-769). Fields (`_tier3_debate_manifest`, :655-668):
`task_id`, `task` (**full task text — no redaction here**), `complexity`, `effort`, `decision` (`APPROVED`/`REJECTED_LEXICAL`), `signature` (HMAC-SHA256 over `CALIBRATION_FIELDS`, :43, :436-444), `nonce` (:242-255), `debate_rounds` (2-3 items of `{round, focus: safety|constraints|adjudication, assessment, vote: APPROVE|REJECT}`, :513-556), `consensus_status`, `consensus_round`.
Verified against the two real artifacts in `.ralph/decisions/council-review-pass-{1,2}.json` (mode 600). Local-model failures append a **markdown** entry (Task ID / Primary Worker / Error / Fallback Action) to `.ralph/errors.log` (:147-176, path at :353); sensitive tasks additionally fail closed via `SensitiveTaskFallbackBlocked` (:179-183). Repo-root `ERRORS.md` is a second, hand-written prose fallback log (protocol Rule 3.5).

## 4. Post-session audit — `skills/worker-routing/routing-audit.sh` → `routing_check.py`

The local script is the real thing (the `~/.gemini/config/skills/worker-routing/` copy is an install-synced duplicate, same size). It locates `~/.gemini/antigravity/brain/<conversation-id>/.system_generated/logs/{overview.txt|transcript.jsonl}` (`routing-audit.sh:22, 45-53`) and delegates everything to `routing_check.py`, relaying exit codes 0/1/2 (fail-closed on unreadable/unparseable logs, `routing_check.py:61-69`, :1234-1240).

Detected per step (`_analyze_step` :743-807, `StepAnalysis.has_violations` :617-622):
* **Violation — unrouted code edit:** step writes a source-code file (extension list from `routing-config.json:43`) with zero worker CLI calls in that same step, regardless of its `[ROUTING:]` label (:55-59, report :1254-1268).
* **Violation — unrouted mutation:** a command that is neither a worker invocation nor a `safe_commands` match, or carries unsafe shell syntax (`$(`, backtick, redirect…) (:44-49, :758-773).
* **Issue codes:** DEC-01 declaration worker/model drift (:458), DEC-02 effort drift (:460), DEC-03 invalid routing declaration (:1042), DEC-04 missing `--model`/effort flags (:412), DEC-05 HMAC calibration signature mismatch (:218), LOG-01 unknown write tool (:788).
* **Warnings:** more code edits than worker calls; writes with zero `[ROUTING:]` declarations (:1271-1284; `--strict` promotes warnings to failure).
* Metrics computed: `total_writes`, `code_writes`, `routing_declarations`, `worker_calls`, `code_write_files`, `violations`, `declaration_drift`, `violation_details`, `calibration_markers` (:850-860).

**The entire report goes to stdout/stderr only.** Nothing is persisted, timestamped, or joined to the telemetry stream.

## 5. learn-session skill — `~/.claude/skills/learn-session/SKILL.md`

Manual trigger (`/learn-session`). Scans **conversation history** (not telemetry/audit artifacts), extracts ≤7 insights, each classified by scope (`[global]` → `~/.gemini/antigravity/knowledge/global-memory.md`; `[local]` → workspace), category (`architecture|gotcha|domain|workflow|rule|pattern|preference`) and importance 1-5 (SKILL.md:14-34). A **mandatory human approval gate** precedes any write (:38-42). Write matrix (:46-65): `knowledge/institutional-memory.md` / global-memory (prepend + strikethrough decay), `CONTEXT.md` (domain terms), `ERRORS.md` (gotchas: attempted/failed/signal/what-worked), `AGENTS.md`/`PROJECT_RULES.md` (durable rules), optional ADR. Then multi-harness sync via `./install.sh` (:69-76): sentinel-wrapped protocol block copied atomically to `AGENTS.md`, `CLAUDE.md`, `~/.gemini/GEMINI.md` and `skills/worker-routing/` mirrored to `~/.gemini/config/skills/`, `~/.codex/skills/`, `.agents/.agent/.codex` project dirs (`install.sh:18-26, 149`), with preflight, one-time backups and rollback. Step 5 re-syncs modified memory files to NotebookLM (:80-87). Current `knowledge/institutional-memory.md` holds 16 dated, tagged insights.

## 6. model-evaluator skill — `~/.claude/skills/model-evaluator/SKILL.md`

Benchmarks models on **TTFT, TPS, Cost, Quality** via LiteLLM with LLM-as-a-Judge scoring (SKILL.md:3, :14-18). Persistence: SQLite `benchmark_runs` (`scripts/storage.py:25-38`): `id`, `timestamp`, `model`, `tier`, `task_id`, `ttft_ms`, `tps`, `cost_usd`, `success`, `score`, `error_msg` — in a cwd-relative `benchmark_history.db` (:7), a task-id space disjoint from repo telemetry. `router_config_generator.py:37-45` emits `active_router_config.json` — per-tier fallback chains ordered by score then cost. **Nothing in the repo references `active_router_config.json`** (verified by grep); the orchestrator's live config is the static `skills/worker-routing/routing-config.json` — seven role blocks (`context_specialist`, `planner`, `critic`, `heavy_doer`, `light_doer`, `sensitive_doer`, `qa_auditor`) each `{name, patterns}` (:2-29), `supported_models` (:30-42), `code_extensions` (:43), `safe_commands` regexes (:44-58) — which is audit-matching data, not a learned policy.

## Gap table — what a continuous learning loop still needs

| Learning signal | Exists today? | Where | Redaction constraint |
|---|---|---|---|
| Routing decision (complexity, route, canned reason) | Yes | council record, `.ralph/routing_telemetry.jsonl` (`agent_council.py:130-136`) | no task text; `reason` must stay canned |
| Deliberation outcome (rounds, outcome, model pair) | Yes | advisory record (`advisory_consultation.py:493-499`) | no task text/secret; halt id must stay non-derived |
| Deliberation content (proposals, critiques) | Latest run only | `.scratch/planning_debate.md`, overwritten per run | halt transcript = marker + id only |
| Signed decision + full task text | Yes | `.ralph/decisions/*.json` (`agent_council.py:772`) | carries raw task text — a learner reading it inherits that exposure |
| Concrete worker model actually chosen per route | No | `chosen_worker` holds route action, not model (`agent_council.py:100-105`) | — |
| Per-invocation worker result (latency, tokens, cost, success, retries) | No | `production_invoker.py` records nothing (verified) | must not log prompts on sensitive paths |
| Effort levels used (planner/critic effort) | No | accepted as params (`advisory_consultation.py:571-572`), never recorded | — |
| Per-round verdict sequence (approve/revise/unparseable per round) | No | only terminal `outcome` + `rounds_run` in telemetry | — |
| Post-session compliance verdict (violations, DEC-*/LOG-* codes) | Computed, not persisted | `routing_check.py` stdout/stderr only (:1244-1298) | log excerpts may contain task text |
| Fallback events | Partial, unstructured | prose in `ERRORS.md` + `.ralph/errors.log` (`agent_council.py:170-176`) — two disjoint surfaces | error text may embed sensitive task detail |
| Ground-truth task outcome (tests passed, review verdict, plan accepted) | No | nowhere; telemetry never joined to results | — |
| Human stalemate resolution (which of the 3 options was picked) | No | options built (`advisory_consultation.py:545-560`), choice never recorded | — |
| Sensitivity-halt frequency/correlation | Countable only | random-id telemetry + redacted transcript | may never become content-bearing; digest ban is structural |
| Model benchmark scores (TTFT/TPS/cost/quality) | Yes, separate world | `benchmark_history.db` (user skill dir) | — |
| Learned routing policy fed back to the router | No | `active_router_config.json` generated but ingested by nothing; live config static | — |
| Session insights → durable memory | Yes, manual + approval-gated | `/learn-session` → memory/CONTEXT/ERRORS/AGENTS + `install.sh` fan-out | human gate is the filter; no automated telemetry mining |

**Reading of the table:** the system already records *decisions* (what was routed where, and whether deliberation agreed) under strict redaction, but records almost nothing about *execution* (what the worker did, how well, at what cost) and nothing about *outcomes* (whether the decision proved right). The two learning mechanisms that exist — learn-session and model-evaluator — are both manual, and neither closes the loop: one mines chat instead of telemetry, the other produces a config nothing consumes.
