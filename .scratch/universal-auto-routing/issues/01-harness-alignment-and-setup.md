# 01 — Cross-Harness Permission Alignment & Unified Setup (`install.sh`)

**What to build:** End-to-end setup and synchronization script (`install.sh`) that atomically installs and aligns sentinel-wrapped routing rules and sandbox permissions across `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`, with automatic backup creation, preflight integrity verification, and instant rollback on failure. Includes a lightweight startup check for CLI flags.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] `install.sh` reads and validates sentinel markers across `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md`
- [ ] Atomic synchronization writes configuration to all three manifests with mode 600 key file validation
- [ ] Automatic rollback is triggered if any file write or validation step fails during installation
- [ ] Startup sanity check verifies environment variables and CLI execution flags at session start
- [ ] Full unit test coverage in `test_routing.py` verifying setup synchronization and rollback behavior
