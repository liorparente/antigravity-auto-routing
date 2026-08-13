# 10 — Telemetry extensions

**What to build:** Extend `AdvisoryTelemetryRecord` / `_build_telemetry_record` with the fields the
four occasions and the panel/canary/budget work above now produce: occasion, topology, per-round
verdict sequence, engagement-unit counts per round, canary flag and result, degradation flags, and
the degraded-independence marker. All existing redaction invariants hold unchanged — no task text
and nothing derived from it leaves the record, per the glossary's TaskIdentity rules. These fields
exist to be consumed by spec 0004's LearningJournal and scoreboard, so field names/shapes should be
stable, not provisional.

**Blocked by:** 05, 07, 08, 09 (collects fields those tickets produce).

**Status:** done

- [x] `AdvisoryTelemetryRecord` carries occasion, topology, per-round verdict sequence, and
      per-round engagement-unit counts for every dialogue, pair or panel.
- [x] Canary flag and result (miss/catch) appear on canary-run records and are absent/false on real
      mission records.
- [x] Degradation flags (which rung, if any) and the degraded-independence marker appear when their
      respective conditions from tickets 07/09 fire.
- [x] A redaction test proves no task text or derivative of it (per the existing hashing gotcha in
      institutional memory — a truncated hash of guessable text is not "non-revealing") appears
      anywhere in the record.
- [x] Existing spec-0001 telemetry fields and their tests are unchanged.

## Notes

Landed in commit `a8af830`. This completes spec 0003's full telemetry contract — every field the
spec's Telemetry paragraph lists now exists on `AdvisoryTelemetryRecord`. `AdvisoryRoundVerdict`
wraps ticket 02's `VerdictContractResult` per round, kept parallel with the existing round sequence.
`/code-review` found no hard violations or confirmed bugs — cleanest large ticket in the series. One
proactive addition on request: an explicit docstring warning that a canary's `round_verdicts` entry
is structurally indistinguishable from a real dialogue's, so spec 0004's LearningJournal must filter
`outcome != "canary"` before aggregating. 288 tests pass.

**Spec 0003 is now feature-complete: tickets 01-10 all done.** Only ticket 11 (sensitive-task path)
remains — see its own file for status.
