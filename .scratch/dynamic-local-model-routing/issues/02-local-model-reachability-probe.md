# 02 — Dynamic Local Model Reachability & Probe Engine

**What to build:** Silently check local inference server availability (LM Studio on port 1234), verify active loaded models, and prioritize local model execution for Trivial and Simple complexity tasks when reachable.

**Blocked by:** 01 — Security Gate & Sensitivity Interceptor

**Status:** completed

- [x] Perform non-blocking pre-flight health ping to local API endpoint.
- [x] Determine if a local model is active and inspect its context window capacity.
- [x] Automatically route Trivial and Simple tasks to the local model when reachable and unblocked by security flags.
