# 34 — Reclaim project-local learned state on uninstall

**What to build:** `uninstall.sh` removes installed skill and learned-state files from `$TARGET_PROJECT_DIR/.agents/skills/worker-routing` and `$TARGET_PROJECT_DIR/.agent/skills/worker-routing` without deleting parent convention directories if they contain other tools' content.

Today, `uninstall.sh` intentionally excludes `$TARGET_PROJECT_DIR/.agents/` and `$TARGET_PROJECT_DIR/.agent/` from `TARGET_DIRS` because they are shared convention directories. Now that `install.sh` propagates learned state (`memory`, `briefs`, `routing_table`, `history.jsonl`), uninstallation leaves learned state files inside those two project-local subdirectories.

**Status:** ready-for-agent

- [ ] `uninstall.sh` removes `skills/worker-routing/` within `.agents` and `.agent` when present.
- [ ] Preserves non-worker-routing content in `.agents/` and `.agent/`.
- [ ] Tests verify clean teardown across all 5 installation targets.
