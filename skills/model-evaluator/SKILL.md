---
name: Model Evaluator & Dynamic Router
description: "Evaluates models against objective benchmarks (TTFT, TPS, Cost, Quality) using LiteLLM and LLM-as-a-Judge. Generates an active router config to dynamically optimize the Worker Routing Protocol's fallbacks."
---

# Model Evaluator & Dynamic Router

This skill is responsible for continuously benchmarking, scoring, and routing AI models based on cost, performance, and capability tiers. 

## When to Use
- When the user asks to "evaluate models", "benchmark performance", or "optimize routing costs".
- To generate a new `active_router_config.json` file for the orchestrator to ingest.

## Core Modules

1. **`storage.py`**: SQLite backend for telemetry (TTFT, TPS, Cost).
2. **`execution.py`**: Async benchmarking engine using `litellm`. It supports declarative YAML benchmarks and LLM-as-a-Judge scoring.
3. **`router_config_generator.py`**: Generates the final human-readable report and the JSON router config.

## Setup
Ensure `litellm` and its dependencies are installed in the Python environment:
```bash
pip install litellm aiosqlite pyyaml
```

## Usage
Run the execution suite:
```bash
python scripts/execution.py
```
Generate the router configuration:
```bash
python scripts/router_config_generator.py
```
