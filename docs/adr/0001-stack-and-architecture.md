# ADR 0001: Initial Stack and Architecture

## Status
Accepted

## Context
Need a deterministic, zero-defect multi-agent routing and consensus engine operating with minimal dependencies, supporting local LLM evaluation, and enforcing strict static type safety.

## Decision
Adopt Python 3.10+ standard library primitives for core routing, Mypy for static typing, Ruff for linting, and Unittest/Pytest for testing across modular skill domains (worker-routing, council-review, learn-session).

## Consequences
- Fast, reproducible local execution with zero heavy runtime overhead.
- Strict type contracts across worker dispatch and debate loops.
- Multi-harness synchronization across Antigravity, Claude, and Codex via symlinks.
