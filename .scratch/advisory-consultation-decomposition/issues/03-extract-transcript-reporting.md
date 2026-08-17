# 03 — Extract Transcript Formatting & Telemetry Reporting (Slice 3)

**What to build:** Extract consultation transcript formatting, telemetry record creation (`AdvisoryTelemetryRecord`), and fail-closed task redaction on sensitivity halts into a dedicated reporting module (`dialogue_transcript.py`). Re-export from `advisory_consultation.py`. Add isolated tests verifying transcript serialization and sensitivity marker preservation.

**Blocked by:** 02 — Extract Degradation Policy & Budget Ladder (Slice 2).

**Status:** completed

- [x] Extract `ConsultationTranscript`, `AdvisoryTelemetryRecord`, and formatting helpers into `dialogue_transcript.py`.
- [x] Centralize fail-closed prompt redaction on `sensitivity_halt` ensuring zero task prompt leakage.
- [x] Re-export all transcript and telemetry symbols in `advisory_consultation.py`.
- [x] Add direct unit tests for transcript rendering, sensitivity redaction boundaries, and telemetry stream serialization.
- [x] Verify all existing tests in `test_routing.py` pass without regression.
