# 03 — 20 Golden Rules Memory Compaction & Scoped Keyword Retrieval

## What to build
Distill the existing 103 items in `knowledge/institutional-memory.md` (~90KB) into 20 categorized, high-density **Golden Rules** spanning Architecture & Deep Modules, Testing & TDD Seams, Subprocess Safety, State Hygiene, and Multi-Harness Governance. Archive the complete raw historical records safely to `knowledge/archive/institutional-memory-legacy.md`.

Upgrade `prompt_assembler.py` and `learned_state.py` with `extract_scoped_memory`, a lightweight hybrid retrieval engine that scans task descriptions and target file tags/extensions to dynamically extract and inject strictly the top 3–5 most relevant rules per task context.

## Acceptance criteria
- [ ] `knowledge/institutional-memory.md` is condensed into 20 structured, actionable Golden Rules (<10KB).
- [ ] Full historical memory is preserved without data loss under `knowledge/archive/institutional-memory-legacy.md`.
- [ ] `extract_scoped_memory` dynamically returns exactly 3–5 high-signal rules matching the task context and file domain.
- [ ] Global task prompt size is reduced by >85% without sacrificing critical behavioral context.

## Blocked by
- 01 — Lean Protocol & Non-Blocking Zero-Latency Boot Infrastructure
