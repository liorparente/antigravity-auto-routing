# CLI Command Reference for Worker Routing (v3.4 Quality-First)

This reference manual documents the exact command lines, REST API commands, and parameters used to run worker models within the Auto Routing Protocol.

---

## 💻 CLI Command Reference

### 1. Antigravity CLI (agy) - Gemini 3.5 Flash / Pro
*Always wrap with `script -q /dev/null` to allocate a PTY and prevent CLI hangs.*
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Deep Research & codebase scanning (Flash/Pro)
IN_WORKER_ROUTING=true script -q /dev/null agy -p "Perform deep research on how authentication and token validation are handled across the repository. List all affected files, schemas, and dependencies." --output-format markdown

# Large file / specification parsing
IN_WORKER_ROUTING=true script -q /dev/null agy -p "Extract API schemas and data contracts from this spec document" -i /path/to/spec.pdf
```

---

### 2. Claude Code CLI
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Complex implementation (Fable 5 / Sonnet 5)
IN_WORKER_ROUTING=true claude -p --allow-dangerously-skip-permissions "Implement the user profile component with full type safety and test coverage"

# High-precision architectural research & planning (Opus 4.8 / Fable 5)
IN_WORKER_ROUTING=true claude -p --model claude-opus-4-8 --allow-dangerously-skip-permissions "Draft a comprehensive migration plan for the database schema with zero downtime"
```

---

### 3. Codex CLI (v0.125+)
*Always specify both `--model <model>` (for `exec` commands) or `-c model="<model>"` (for `review` commands), and `-c model_reasoning_effort="low"|"medium"|"high"|"ultra"`, to match the task's complexity and guarantee a perfect score.*
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Plan critique (Consensus step — High Effort for Deep Thinking)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="high" "Perform deep reasoning review on this implementation plan: $(cat .claude/plan_draft.md)"

# Code review (QA step — High Effort for Zero-Defect Audit)
IN_WORKER_ROUTING=true codex review --uncommitted -s workspace-write -c model="gpt-5.6-sol" -c model_reasoning_effort="high"

# Trivial task (Luna - gpt-5.6-luna)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "Rename variables in file.js"

# Simple task (Terra - gpt-5.6-terra)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra -c model_reasoning_effort="medium" -s workspace-write "Add input validation and error handling to helper.js"
```

---

### 4. Local Models (LM Studio API)
*Use the REST API to load, verify, and unload models to preserve system RAM.*

```bash
# 1. Verify if a model is currently loaded
curl -s http://127.0.0.1:1234/v1/models | jq '.data[0].id'

# 2. Load model (qwen/qwen3-coder-30b or gemma-4-e4b-it-mlx) if not already active
curl -s -X POST http://127.0.0.1:1234/api/v1/models/load \
  -H "Content-Type: application/json" \
  -d '{"model": "qwen/qwen3-coder-30b"}' > /dev/null

# 3. Run inference
curl -s http://127.0.0.1:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen/qwen3-coder-30b",
    "messages": [{"role": "user", "content": "Write a TypeScript debounce function with full type signatures"}],
    "temperature": 0.1
  }' | jq -r '.choices[0].message.content'

# 4. Unload model (mandatory)
curl -s -X POST http://127.0.0.1:1234/api/v1/models/unload \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "qwen/qwen3-coder-30b"}' > /dev/null
```
