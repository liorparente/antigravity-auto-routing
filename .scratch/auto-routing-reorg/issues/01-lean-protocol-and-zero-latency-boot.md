# 01 — Lean Protocol & Non-Blocking Zero-Latency Boot Infrastructure

## What to build
Streamline the injected protocol sentinel from 22KB down to a compact ~4KB core block that strictly preserves the **Hard Gate**, mandatory `[ROUTING:]` first-line grammar, and **Worker Mode Override** token validation (`[WORKER-MODE: AGY-NESTED-EXEC]`). Move detailed worker CLI flags, sandbox edge cases, and procedural guides to `skills/worker-routing/REFERENCE.md`. 

Update `install.sh` and `uninstall.sh` to eliminate synchronous file-locking (`fcntl.flock`) and replace heavy startup checks with fast, lock-free hash verification across all harnesses (`~/.gemini/GEMINI.md`, `CLAUDE.md`, `AGENTS.md`). Harden external CLI worker subprocess spawning (e.g. `agy -p`) with explicit non-interactive stdin guards (`< /dev/null`, piped input) and mandatory `BypassSandbox: true` validation to prevent terminal and sandbox TTY freezes.

## Acceptance criteria
- [x] Injected protocol block in `AGENTS.md`, `CLAUDE.md`, and `~/.gemini/GEMINI.md` is under 5KB in size.
- [x] Hard Gate and `[ROUTING:]` first-line validation remain 100% active and unbypassable.
- [x] `install.sh` executes in <100ms with zero `fcntl.flock` deadlocks or file timeouts.
- [x] Subprocess invocations of `agy` and external CLI workers pass non-interactive EOF guards and never hang indefinitely.
- [x] Comprehensive CLI recipes, troubleshooting notes, and extended matrices are cleanly accessible in `REFERENCE.md`.

## Blocked by
None — can start immediately.
