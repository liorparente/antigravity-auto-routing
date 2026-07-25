# Worker Routing Fallbacks

## 2026-07-25 — `agy` deep-research worker unavailable

- Mission: ultra-high-effort review of the external `implementation_plan.md`.
- Failure: `agy` 1.1.7 could not create its logs or bind `127.0.0.1:0` in the managed sandbox (`operation not permitted`).
- Fallback: use the protocol-approved read-only Codex 5.6 Sol path for repository research, followed by a separate ultra-effort Codex 5.6 Sol critic pass.
- Scope impact: `agy` exited before reading the plan or repository; no research result was lost or partially trusted.

## 2026-07-25 — Codex CLI fallback unavailable

- Mission: read-only repository research for the same plan review.
- Failure: `codex-cli` 0.144.1 could not initialize its in-process app-server client in the managed sandbox (`operation not permitted`).
- Fallback: delegate independent read-only research and review passes to built-in worker agents, then synthesize only their evidence-backed findings.
- Scope impact: the CLI exited before producing a review; no partial output was trusted.

## 2026-07-25 — CLI research workers unavailable for calibration hardening

- Mission: deep research for HMAC calibration verification, unsafe-chain metrics suppression, and the multi-pass council debate.
- Failure: `agy` could neither create its runtime logs nor bind `127.0.0.1:0`; the protocol-approved Codex 5.6 Sol fallback could not initialize its in-process app-server client. Both failed with `operation not permitted`.
- Fallback: use a built-in read-only research worker, followed by delegated implementation and independent QA.
- Scope impact: both CLIs exited before reading or modifying repository files; no partial output was trusted.

## 2026-07-25 — Claude implementation worker unavailable

- Mission: implement the reviewed calibration HMAC, metrics suppression, council debate, and unit-test changes.
- Failure: Claude Sonnet 4.6 could not reach its API endpoint (`ENOTFOUND`) and exited without yielding implementation output.
- Fallback: use a built-in implementation worker with the same three-file scope, followed by an independent QA worker.
- Scope impact: the Claude CLI produced no file changes or partial result.

## 2026-07-25 — CLI research fallback unavailable for lint/type repair

- Mission: inspect the three worker-routing Python files before Ruff and mypy repair.
- Failure: `agy` failed before inspection due sandbox log/bind permissions; the Codex CLI fallback then failed before inspection due in-process app-server permissions.
- Fallback: use a delegated built-in research and execution worker.
