---
name: Council Review
description: "Orchestrates a panel of AI agents (Claude, Codex, and Gemini) to review implementation plans and propose improvements. Resolves disagreements via a local Adjudicator model."
---

# Council Review

The Council Review skill implements a formalized multi-agent peer review system. It orchestrates Claude, Codex, and Antigravity CLI models to scrutinize proposed changes along strict heuristics, finding design flaws, spec deviations, and standards violations before execution.

## Features
- **Parallel Evaluation**: Three distinct models review evidence concurrently.
- **Strict Heuristics**: Validates deep module design, hygiene, and logic bounds.
- **Material Adjudication**: Resolves deadlocks via an independent local model tie-breaker (LM Studio).
- **Verifiable Artifacts**: Generates structured, deterministic review reports mapped back to the origin branch.

## Architecture
- `scripts/council_review.py`: Main orchestration logic and file aggregation.
- `scripts/provider_adapters.py`: CLI adapters for Claude, Codex, and Antigravity.
- `references/council-policy.json`: The rules and timeouts the council must follow.
- `agents/openai.yaml`: Standard agent manifest format.

## Usage
Triggered dynamically during the `/plan` lifecycle, or invoked manually when the agent is asked to form an 'Agent Council' or 'Code Review Panel'.
