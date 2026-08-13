# 01 — Security Gate & Sensitivity Interceptor

**What to build:** Automatically intercept prompts and code containing security keys, credentials, or private user data flags, stopping execution and prompting the human user for explicit approval before dispatching data to any worker.

**Blocked by:** None — can start immediately

**Status:** completed

- [x] Detect presence of API keys, tokens, or credential patterns in prompt and code diffs.
- [x] Pause execution and present a clear security prompt to the human user when sensitive data is detected.
- [x] Proceed to routing only upon receiving explicit user approval.
