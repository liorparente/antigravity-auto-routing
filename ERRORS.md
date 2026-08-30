# Error Log & Strict Quality Gate

> [!IMPORTANT]
> **Strict Error Logging Gate:** Auto-log to `ERRORS.md` ONLY after non-trivial root cause diagnosis, verified TDD regression test (Red -> Green), and a clear actionable prevention rule. Capped at 20 entries (FIFO).

## Active Entries

### ERR-0007: Atomic Copy Published Before Recording Rollback Identity
- **Date:** 2026-08-30
- **Root Cause:** `atomic_copy` appended the transaction-written file identity to its write ledger after `mv -f` published the inode. A failure or process interruption in that ordering left rollback unable to prove which inode the transaction had installed.
- **Verified TDD Reproduction:** `skills/worker-routing/test_routing.py::ManagedFileClosureTests::test_atomic_copy_records_write_identity_before_publication` failed structurally while the ledger append followed `mv -f "$temporary" "$target"`, then passed after the append moved ahead of publication.
- **Actionable Prevention Rule (Golden Rule 46):** Persist the transaction-written device/inode identity before atomic publication, then roll back only that exact inode with no-replace restoration and retained recovery bytes on failure. Persistent process-death recovery remains a separate open boundary under Golden Rule 47.

### ERR-0006: Transitional Runtime Proxying Obscuring Static Typecheck Seams
- **Date:** 2026-08-29
- **Root Cause:** When aliasing deprecated module facades via dynamic `sys.modules[__name__] = canonical_module`, test suites importing the legacy module name fail static analysis (`mypy`) with 96+ false `attr-defined` errors because typecheckers cannot follow runtime dynamic module replacement.
- **Verified Signal:** `mypy worker_routing` failed with 96 attribute errors on `test_debate_orchestrator.py` while runtime `python3 -m unittest` succeeded. Retargeting imports to `critical_dialogue` resolved all 96 errors cleanly.
- **Actionable Prevention Rule (Golden Rule 41):** Always update test suites and static type annotations directly to canonical deep modules rather than relying on runtime dynamic aliases during module migrations.

### ERR-0005: Dispatched Background Threads Bypassing Public Consultation Mock Seams
- **Date:** 2026-08-29
- **Root Cause:** In `critical_dialogue.py`, `_run_dispatched_post_mortem` directly invoked `run_critical_dialogue(...)` instead of the public alias `run_advisory_consultation_debate(...)`. Consequently, test harnesses patching `advisory_consultation.run_advisory_consultation_debate` with side effects were bypassed, preventing the background thread exception net from capturing and recording the simulated failure.
- **Verified TDD Reproduction:** `skills/worker-routing/test_routing.py::AdvisoryBlockingStanceTests::test_dispatch_post_mortem_consultation_records_unexpected_exceptions_instead_of_dropping_them` failed with `AssertionError: 'sentinel-oops' not found in transcript` when direct invocation was used, and passed cleanly once routed through `run_advisory_consultation_debate`.
- **Actionable Prevention Rule (Golden Rule 40):** Background dispatch wrappers must call the canonical public consultation alias to preserve mockable test seams across thread boundaries.

### ERR-0004: Event Loop Re-entrancy Failure in Synchronous Entry Points
- **Date:** 2026-08-29
- **Root Cause:** Invoking synchronous convenience wrappers (such as `request_council_review`) containing naked `asyncio.run(council.review(request))` from within an active asyncio event loop (e.g. asynchronous test runners, notebooks, or live async orchestrators) raised `RuntimeError: This event loop is already running`.
- **Verified TDD Reproduction:** `skills/worker-routing/test_critical_dialogue.py::test_request_council_review_completes_inside_a_running_event_loop` proved that calling `request_council_review` inside an `async def run_in_loop()` raised `RuntimeError` before fix and completed cleanly after thread-pool bridging.
- **Actionable Prevention Rule (Golden Rule 39):** Check `asyncio.get_running_loop()` before calling `asyncio.run()`, and dispatch through a dedicated `ThreadPoolExecutor` worker when an event loop is already active.

### ERR-0003: macOS Dotfile Permission Lock & TCC Extended Attributes on LM Studio
- **Date:** 2026-08-29
- **Root Cause:** LM Studio's macOS sandbox/TCC permissions and extended-attribute handling can deny writes to dotfile paths beneath its application-managed directory, even when ordinary filesystem permissions appear valid.
- **Verified Signal:** Writing `~/.lmstudio/test.txt` failed with `EPERM`, demonstrating that the denial occurs at the macOS privacy/extended-attribute layer rather than as an application-level model-server failure.
- **Actionable Prevention Rule:** Do not use `~/.lmstudio` as a general scratch or validation path. Use an approved workspace or temporary directory for test artifacts, and inspect TCC grants and extended attributes before treating an `EPERM` as a code defect.

### ERR-0002: Declarative Schema Key Desynchronization between STRUCTURAL_KEYS and NON_ROLE_CONFIG_KEYS
- **Date:** 2026-08-29
- **Root Cause:** In `routing_check.py:354`, structural keys are resolved dynamically via `routing_config.STRUCTURAL_KEYS if routing_config is not None else NON_ROLE_CONFIG_KEYS`. When `_active_profile` was added only to `NON_ROLE_CONFIG_KEYS` and `test_declarative_schema.EXPECTED_NON_ROLE_KEYS`, `routing_config.STRUCTURAL_KEYS` remained unupdated, silently bypassing the schema fix in runtime environments where `routing_config` is imported.
- **Verified TDD Reproduction:** `skills/worker-routing/test_declarative_schema.py` and `skills/worker-routing/test_routing.py` verified the drift between the two sets and validated the lockstep equality assertion.
- **Actionable Prevention Rule (Golden Rule 36):** Always update `routing_config.STRUCTURAL_KEYS` alongside `routing_check.NON_ROLE_CONFIG_KEYS` whenever introducing top-level non-role configuration keys, and verify parity via declarative schema tests.

### ERR-0001: LM Studio Reasoning-Delta Blindspot & Stdio Block-Buffering Stall
- **Date:** 2026-08-29
- **Root Cause:** Invoking local reasoning models (Qwen 27B / DeepSeek R1) in LM Studio via synchronous requests (`stream: false`) or naive streaming (`delta.content` only) resulted in 60-150s of pipe silence during `reasoning_content` generation. Additionally, Python's default stdio block-buffering on non-TTY pipes withheld output, triggering background task manager timeouts (`Last progress: never`).
- **Verified TDD Reproduction:** `scratch/repro_lmstudio_stall.py` empirically reproduced 2m17s pipe freeze on naive streaming vs 0.3s immediate TTFT on robust SSE with reasoning delta + explicit `sys.stdout.flush()`.
- **Actionable Prevention Rule (Golden Rule 35):** All local inference clients must enable SSE streaming (`stream: true`), process `delta.reasoning_content`, and explicitly invoke `sys.stdout.flush()` / execute with `python3 -u`. Complex code generation must be calibrated to Tier 2/3 workers rather than one-shot Tier 0.
