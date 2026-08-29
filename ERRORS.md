# Error Log & Strict Quality Gate

> [!IMPORTANT]
> **Strict Error Logging Gate:** Auto-log to `ERRORS.md` ONLY after non-trivial root cause diagnosis, verified TDD regression test (Red -> Green), and a clear actionable prevention rule. Capped at 20 entries (FIFO).

## Active Entries

### ERR-0001: LM Studio Reasoning-Delta Blindspot & Stdio Block-Buffering Stall
- **Date:** 2026-08-29
- **Root Cause:** Invoking local reasoning models (Qwen 27B / DeepSeek R1) in LM Studio via synchronous requests (`stream: false`) or naive streaming (`delta.content` only) resulted in 60-150s of pipe silence during `reasoning_content` generation. Additionally, Python's default stdio block-buffering on non-TTY pipes withheld output, triggering background task manager timeouts (`Last progress: never`).
- **Verified TDD Reproduction:** `scratch/repro_lmstudio_stall.py` empirically reproduced 2m17s pipe freeze on naive streaming vs 0.3s immediate TTFT on robust SSE with reasoning delta + explicit `sys.stdout.flush()`.
- **Actionable Prevention Rule (Golden Rule 35):** All local inference clients must enable SSE streaming (`stream: true`), process `delta.reasoning_content`, and explicitly invoke `sys.stdout.flush()` / execute with `python3 -u`. Complex code generation must be calibrated to Tier 2/3 workers rather than one-shot Tier 0.

