# Reference Manual for Worker Routing & Learning Protocols (v3.5)

This reference manual documents the complete CLI syntax, REST API commands, execution parameters, and learning-journal ground-truth recording mechanisms for the Auto Routing Protocol.

---

## 💻 CLI Command Reference

### 1. Antigravity CLI (`agy`) — Gemini 3.6 Flash / 3.5 Flash / 3.1 Pro
- Always append `< /dev/null` or pipe input to prevent non-interactive TTY hangs.
- Prefix with `IN_WORKER_ROUTING=true` and lead prompt with `[WORKER-MODE: AGY-NESTED-EXEC]`.
- Tool calls invoking external workers via `run_command` MUST specify `BypassSandbox: true`.

```bash
# Deep Research & codebase scanning (Flash/Pro)
IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: AGY-NESTED-EXEC] Perform deep research on authentication and token validation across the repository. List affected files, schemas, and dependencies." < /dev/null

# Large file / specification parsing
IN_WORKER_ROUTING=true agy -p "[WORKER-MODE: AGY-NESTED-EXEC] Extract API schemas and data contracts from this spec document" -i /path/to/spec.pdf < /dev/null
```

---

### 2. Claude Code CLI
- Prefix with `IN_WORKER_ROUTING=true` and lead prompt with `[WORKER-MODE: AGY-NESTED-EXEC]`.
- Always append `< /dev/null`.
- Tool calls invoking external workers via `run_command` MUST specify `BypassSandbox: true`.

```bash
# Complex implementation (Sonnet 5 / Fable 5)
IN_WORKER_ROUTING=true claude -p --no-session-persistence --model claude-sonnet-5 --effort high --allow-dangerously-skip-permissions --permission-mode bypassPermissions "[WORKER-MODE: AGY-NESTED-EXEC] Implement the user profile component with full type safety and test coverage" < /dev/null

# High-precision architectural planning (Opus 5 / Fable 5)
IN_WORKER_ROUTING=true claude -p --no-session-persistence --model claude-opus-5 --effort high --allow-dangerously-skip-permissions --permission-mode bypassPermissions "[WORKER-MODE: AGY-NESTED-EXEC] Draft a comprehensive migration plan for the database schema with zero downtime" < /dev/null
```

---

### 3. Codex CLI (v0.125+)
- Always specify both `--model <model>` (for `exec`) or `-c model="<model>"` (for `review`), and `-c model_reasoning_effort="low"|"medium"|"high"|"ultra"`.
- `codex exec` selects sandbox mode via `-s`/`--sandbox`; `codex review` uses `-c sandbox_mode="<mode>"`.
- Prefix with `IN_WORKER_ROUTING=true` and lead prompt with `[WORKER-MODE: AGY-NESTED-EXEC]`.
- Always append `< /dev/null`.
- Tool calls invoking external workers via `run_command` MUST specify `BypassSandbox: true`.

```bash
# Plan critique (Consensus step — High Effort for Deep Thinking)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] Perform deep reasoning review on this implementation plan: $(cat .claude/plan_draft.md)" < /dev/null

# Code review (QA step — High Effort for Zero-Defect Audit)
IN_WORKER_ROUTING=true codex review --uncommitted -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null

# Trivial task (Luna - gpt-5.6-luna)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] Rename variables in file.js" < /dev/null

# Simple task (Terra - gpt-5.6-terra)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra -c model_reasoning_effort="medium" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] Add input validation and error handling to helper.js" < /dev/null
```

---

### 4. Local Models (LM Studio API)
- Verify and query local models via `http://127.0.0.1:1234/v1`.

```bash
# 1. Verify if a model is currently loaded
curl -s http://127.0.0.1:1234/v1/models | jq '.data[0].id'

# 2. Load model if not already active
curl -s -X POST http://127.0.0.1:1234/api/v1/models/load \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen3.8-27b-mlx"}' > /dev/null

# 3. Run inference
curl -s http://127.0.0.1:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.8-27b-mlx",
    "messages": [{"role": "user", "content": "Write a TypeScript debounce function with full type signatures"}],
    "temperature": 0.1
  }' | jq -r '.choices[0].message.content'

# 4. Unload model
curl -s -X POST http://127.0.0.1:1234/api/v1/models/unload \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "qwen3.8-27b-mlx"}' > /dev/null
```

---

## 📓 Learning-Journal Ground-Truth Recording (Spec 0004 & Spec 0011)

`learning_outcomes.py` grades four ground truths after the fact: `tests`, `review`, `plan`, and `stalemate_resolution`.

### Core Identity Rule
`task_id` must match the exact ID passed into the consultation or task runner. Never omit `task_id`.

### 1. Test Results
Producers: Doer local unit/integration tests (Phase 3) or CI runner.
Call: `learning_outcomes.record_test_result(task_id, passed=<bool>, root_dir=<repo root>)`.
*Note: `passed` must be a Python boolean (`True`/`False`), never the integer exit code (0 vs 1).*

### 2. Plan Rejection
Recorded when a human or orchestrator declines an agreed plan or abandons it unimplemented:
Call: `learning_outcomes.record_plan_outcome(task_id, accepted=False, root_dir=<repo root>)`.

### 3. Review Verdicts
Recorded when a reviewer (human, `/code-review`, PR) reviews and issues a verdict on the code:
Call: `learning_outcomes.record_review_verdict(task_id, approved=<bool>, root_dir=<repo root>)`.

### 4. Stalemate Resolution
Recorded when an advisory consultation debate reaches a stalemate and a human selects an option:
Call: `learning_outcomes.record_stalemate_resolution(task_id, report, chosen, root_dir=<repo root>)` where `chosen` is the actual `AdvisoryResolutionOption` from `report.options`.

### Positional Reduction Convention
Outcomes are grouped by `(task_id, ground_truth)`. Within a group, the last record wins. The stream is append-only.
