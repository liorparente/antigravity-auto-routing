# 02 — Active LM Studio Capability Probing & Universal Calibrated Provider Routing

## What to build
Implement an active, sub-millisecond capability probe against `http://127.0.0.1:1234/v1/models` in `production_invoker.py` that discovers loaded local models (e.g. Gemma 4, Qwen 2.5/3, DeepSeek-R1) and assesses their parameter classes. If LM Studio is unreachable or has no model loaded, the orchestrator triggers an explicit, interactive user decision prompt asking whether to launch LM Studio or proceed with Gemini Flash.

Update `routing-config.json` and `protocol.md` to establish a **Universal Calibrated Provider Matrix**:
- **Tier 0 (Local $0):** LM Studio for Trivial, Simple Boilerplate, and Sensitive/PII tasks.
- **Tier 1 (Cloud Fast/Cheap):** `agy` Gemini 3.6/3.7 Flash for Context, Search, and Single-File Execution.
- **Tier 2 (Cloud Heavy Doer / High-Velocity Coding):** Claude Sonnet 5 with fine-grained thinking/effort parameters (`--effort low | medium | high | ultra` / `ultracode`) & Codex 5.6 Terra for 3–4 files, intense refactoring, and feature builds.
- **Tier 3 (Cloud System 2 / Deep Planning):** Claude Opus 5 (Thinking) & Codex 5.6 Sol (Ultra) for 5+ files, DB/architecture migrations, initial planning (`/plan`), and stubborn bugs (2+ failures).

## Acceptance criteria
- [ ] Non-blocking HTTP probe checks `http://127.0.0.1:1234/v1/models` with a 200ms timeout and parses model capabilities.
- [ ] If LM Studio is offline, system surfaces an interactive prompt allowing user to start LM Studio or fallback to Gemini Flash.
- [ ] Provider routing correctly configures model and effort flags across Claude (`--effort`), Codex (`-c model_reasoning_effort`), `agy`, and LM Studio.
- [ ] Trivial/Simple tasks route strictly to Tier 0 / Tier 1 by default, preventing unnecessary cloud credit burn.
- [ ] System 2 escalation triggers (5+ files, DB/architecture, `/plan`, 2+ failures) reliably engage Tier 2 / Tier 3.

## Blocked by
- 01 — Lean Protocol & Non-Blocking Zero-Latency Boot Infrastructure
