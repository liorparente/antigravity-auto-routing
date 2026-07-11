# Codex Model Routing & Reasoning Effort Configurations

This document details the supported models, pricing tiers, reasoning effort levels, and CLI configuration flags for the OpenAI Codex CLI and API, referencing both local configurations and official OpenAI documentation.

## 1. Supported Model Tiers & Pricing
The GPT-5.6 model family (launched July 9, 2026) introduced a tiered system for routing tasks based on required reasoning depth, latency, and cost.

| Model Tier | Identifier | Target Complexity | Behaviors & Best Use Cases | Input Price (per 1M tokens) | Output Price (per 1M tokens) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Sol** (Flagship) | `gpt-5.6-sol` | Complex / Audit / Review | Optimized for multi-step agentic workflows, planning, codebase auditing, and peer review. Supports max reasoning/ultra mode. | $5.00 | $30.00 |
| **Terra** (Balanced) | `gpt-5.6-terra` | Simple / Scoped Code | Everyday workhorse. Balanced intelligence and speed. Best for scoped feature implementation. | $2.50 | $15.00 |
| **Luna** (Efficiency) | `gpt-5.6-luna` | Trivial / Boilerplate | High-speed, cost-effective. Optimized for simple refactors, formatting, quick Q&A, and basic tests. | $1.00 | $6.00 |

---

## 2. Configuration & Overrides in Codex CLI

### Default Global Settings
By default, the Codex CLI reads parameters from the global configuration file:
* **Path**: `~/.codex/config.toml`
* **Local Defaults**:
  ```toml
  model = "gpt-5.6-sol"
  model_reasoning_effort = "ultra"
  ```
> [!WARNING]
> If you do not specify the model or reasoning effort flags when executing Codex commands, the CLI will fall back to these global defaults. Running trivial tasks without overrides will route them to the most expensive tier (`gpt-5.6-sol` with `ultra` effort), causing significant token waste and high execution costs.

### Command-Line Flags
You can dynamically override global settings on a per-command basis:

#### A. Selecting the Model: `--model <identifier>`
Instructs the CLI to use a specific model tier (e.g., `gpt-5.6-luna`).
Example:
```bash
codex exec --model gpt-5.6-luna "Rename variable x to index"
```

#### B. Adjusting Reasoning Effort: `-c model_reasoning_effort="<level>"`
Controls the thinking depth (reasoning token budget) allocated to a task.
* **Valid Values**: `low`, `medium`, `high`, `ultra` (additionally supports `minimal` and `xhigh` for compatible API runtimes).
* **Impact**: Higher levels (e.g., `ultra`) increase reasoning accuracy and handle deep logic but suffer from higher latency, higher token usage, and higher cost. Lower levels (e.g., `low`) are optimized for fast response times and lower costs.

Example:
```bash
codex exec --model gpt-5.6-terra -c model_reasoning_effort="low" "Write input validation for helper.js"
```

---

## 3. Primary Sources & References
1. **Local Configurations**:
   - Global config: `/Users/liorparente/.codex/config.toml`
   - Project protocol: `skills/worker-routing/protocol.md`
   - Project skills definition: `skills/worker-routing/SKILL.md`
2. **External Sources**:
   - OpenAI GPT-5.6 Model Family Announcement (July 9, 2026).
   - Codex CLI Reference Manual (v0.125+).
