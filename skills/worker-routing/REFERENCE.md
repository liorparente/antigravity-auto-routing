# CLI Command Reference for Worker Routing

This reference manual documents the exact command lines, REST API commands, and parameters used to run worker models within the Auto Routing Protocol.

---

## 💻 CLI Command Reference

### 1. Antigravity CLI (agy) - Gemini 3.5 Flash
*Always wrap with `script -q /dev/null` to allocate a PTY and prevent CLI hangs.*
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Codebase scanning & context distillation
IN_WORKER_ROUTING=true script -q /dev/null agy -p "Summarize how authentication is handled across the repository" --output-format markdown

# Large file parsing (e.g., PDFs, large logs)
IN_WORKER_ROUTING=true script -q /dev/null agy -p "Extract API schemas from this spec document" -i /path/to/spec.pdf
```

---

### 2. Claude Code CLI
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Complex implementation (Fable 5 / Sonnet 5)
IN_WORKER_ROUTING=true claude -p --dangerously-skip-permissions "Implement the user profile component following our design tokens"

# Opus-tier architectural research
IN_WORKER_ROUTING=true claude -p --model claude-opus-4-8 --dangerously-skip-permissions "Draft a migration plan for the database schema"
```

---

### 3. Codex CLI (v0.125+)
*Always specify both `--model <model>` (for `exec` commands) or `-c model="<model>"` (for `review` commands), and `-c model_reasoning_effort="low"|"medium"`, to prevent defaulting to the most expensive model/effort (e.g. Sol/Ultra) configured globally.*
*Always prefix with `IN_WORKER_ROUTING=true` so the worker's own tool calls aren't re-gated.*
```bash
# Plan critique (Consensus step)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-sol -c model_reasoning_effort="medium" "Analyze this implementation plan: $(cat .claude/plan_draft.md)"

# Code review (QA step)
IN_WORKER_ROUTING=true codex review --uncommitted -s workspace-write -c model="gpt-5.6-sol" -c model_reasoning_effort="medium"

# Trivial task (Luna - gpt-5.6-luna)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "Rename variables in file.js"

# Simple task (Terra - gpt-5.6-terra)
IN_WORKER_ROUTING=true codex exec --model gpt-5.6-terra -c model_reasoning_effort="low" -s workspace-write "Add input validation to helper.js"
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
    "messages": [{"role": "user", "content": "Write a TypeScript debounce function"}],
    "temperature": 0.2
  }' | jq -r '.choices[0].message.content'

# 4. Unload model (mandatory)
curl -s -X POST http://127.0.0.1:1234/api/v1/models/unload \
  -H "Content-Type: application/json" \
  -d '{"instance_id": "qwen/qwen3-coder-30b"}' > /dev/null
```
