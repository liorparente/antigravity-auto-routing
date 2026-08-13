# 01 — Cleanup Legacy Model Identifiers in Routing Config

**What to build:** Remove legacy transition model patterns (`Opus 4.8`, `claude-3-7-sonnet`) from `skills/worker-routing/routing-config.json` so that the routing specification is strictly standardized on active v5 models (`claude-sonnet-5` and `claude-opus-5`).

**Blocked by:** None — can start immediately.

**Status:** completed

- [x] Remove `Opus 4.8` and `claude-3-7-sonnet` pattern entries from `skills/worker-routing/routing-config.json`.
- [x] Ensure `planner`, `heavy_doer`, and `qa_auditor` roles rely cleanly on `claude-opus-5` and `claude-sonnet-5`.
- [x] Run `./install.sh /Users/liorparente/Documents/Projects/auto-routing` to synchronize updated configuration.
