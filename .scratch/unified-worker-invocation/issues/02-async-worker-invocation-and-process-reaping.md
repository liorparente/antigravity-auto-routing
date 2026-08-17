# 02 — Async Worker Invocation Engine & Timeout Process Reaping

**What to build:** Implement `invoke_worker_async` and `invoke_workers_parallel` in `production_invoker.py` supporting non-blocking asynchronous execution and parallel multi-model batches, with standardized non-interactive stdin (`DEVNULL`), environment variable forwarding (`IN_WORKER_ROUTING=1`), injectable async runner seams for deterministic testing, and guaranteed subprocess termination and reaping (`proc.kill()` + `await proc.wait()`) on timeout.

**Blocked by:** 01 — Worker Execution Result & Structured Output Extraction

**Status:** completed

- [x] Implement `invoke_worker_async` and `invoke_workers_parallel` using `asyncio.create_subprocess_exec` with non-interactive stdin.
- [x] Implement guaranteed child process termination and reaping on `asyncio.TimeoutError` to prevent zombie subprocess leaks.
- [x] Add unit tests with injectable runner seams verifying async execution, parallel batch execution, and timeout child reaping.
