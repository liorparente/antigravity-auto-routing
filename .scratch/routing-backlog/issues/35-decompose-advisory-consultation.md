# Issue 35 — Decompose Advisory Consultation Monolith into Deep Modules (Spec 0006)

* Status: ready-for-agent
* Date: 2026-08-17
* Spec: [docs/specs/0006-advisory-consultation-decomposition.md](file:///Users/liorparente/Projects/auto-routing/docs/specs/0006-advisory-consultation-decomposition.md)
* GitHub Issues: [#1](https://github.com/liorparente/antigravity-auto-routing/issues/1), [#2](https://github.com/liorparente/antigravity-auto-routing/issues/2), [#3](https://github.com/liorparente/antigravity-auto-routing/issues/3), [#4](https://github.com/liorparente/antigravity-auto-routing/issues/4)

## Summary

Decompose the 4,000+ line monolithic `advisory_consultation.py` into three cohesive deep modules under a narrow orchestrator facade:
1. `dialogue_contracts.py` — Verdict contract, quote verification, and objection parsing.
2. `dialogue_degradation.py` — Pure budget degradation ladder and session cap math.
3. `dialogue_transcript.py` — Transcript rendering, telemetry records, and fail-closed sensitivity halt redactions.

## Slices & GitHub Issues

- [ ] [Slice 1 (#1)](https://github.com/liorparente/antigravity-auto-routing/issues/1): Extract `dialogue_contracts.py` with backward compatible re-exports.
- [ ] [Slice 2 (#2)](https://github.com/liorparente/antigravity-auto-routing/issues/2): Extract `dialogue_degradation.py` with backward compatible re-exports.
- [ ] [Slice 3 (#3)](https://github.com/liorparente/antigravity-auto-routing/issues/3): Extract `dialogue_transcript.py` with backward compatible re-exports.
- [ ] [Slice 4 (#4)](https://github.com/liorparente/antigravity-auto-routing/issues/4): Clean up `advisory_consultation.py` facade and run type-check / lint / install synchronization.
