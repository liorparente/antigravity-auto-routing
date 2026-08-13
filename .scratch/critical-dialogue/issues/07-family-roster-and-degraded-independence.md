# 07 — Family-based roster resolution and degraded independence

**What to build:** Roster resolution treats *family* (provider lineage — Claude, Codex/GPT, Gemini,
each local model lineage) as the independence unit, not individual model name. Pairs must span two
families; panels must span three. Resolution follows `routing-config.json`'s role blocks and the
protocol's fallback chains. If a required family is unreachable, the system substitutes a family
from the fallback chain (local families qualify) before ever falling back to same-family. Only when
a single family remains does the dialogue run same-family — and then it carries an explicit
degraded-independence marker in both the `AdvisoryTelemetryRecord` and the rendered transcript.
Never silent.

**Blocked by:** 05.

**Status:** done

- [x] Given two+ reachable families, a pair/panel roster never repeats a family across roles.
- [x] Given one family in a role unreachable, resolution tries the next family in the configured
      fallback chain before considering same-family.
- [x] Only when the fallback chain is exhausted to a single remaining family does the dialogue run
      same-family, and doing so sets the degraded-independence marker.
- [x] The degraded-independence marker appears in both the telemetry record and the transcript text
      — a test asserting on transcript content should find it, not just the structured record.
- [x] A normal (non-degraded) run never carries the marker.

## Notes

Landed in commit `405ad60`. `classify_model_family` + `resolve_roster`, wired via an opt-in
`reachability_check` parameter (default `None` = unchanged behavior for every existing caller).
"Single family remains" read as roster-level exhaustion, not environment-wide — documented as the
more conservative reading. `/code-review` caught two real issues: an unanchored regex in the family
classifier risking two unrelated local models colliding into one family, and a fabricated citation
in a docstring attributing a design choice to ticket 06 that ticket 06 never said. Both fixed. 233
tests pass. Note: like every ticket in this series so far, nothing in production actually calls
`run_advisory_consultation_debate` yet, so this resolver is exercised by tests only — consistent
with the rest of spec 0003 being infrastructure-first.
