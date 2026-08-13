# 11 — Hygiene sweep

**What to build:** One mechanical pass over the inconsistencies that accumulated across the recent
changes, with no behaviour change at all. Individually none is a defect; together they make the module
read as though several authors worked on it without speaking.

Deliberately trivial, so it can be reviewed in a single pass.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] The function-local import moves to the module header, or a comment records why it must stay
      lazy.
- [x] Standard-library import ordering is restored.
- [x] Over-long lines are brought in line with the file's prevailing width.
- [x] The user-agent version is derived from the protocol version rather than restated, so it cannot
      drift again — it currently claims a version the protocol left behind.
- [x] Rationale comments dropped during the refactor are recovered from history and reattached to the
      code that now carries the constraint.
- [x] The suite passes with **zero** assertion changes. Needing one means the sweep changed behaviour
      and has exceeded its scope.

## Notes

**Status corrected 2026-08-11.** Delivered by commit `de12bda` (`agent_council.py`,
`routing_check.py`), which is on `main`; this file was simply never updated.

Two criteria were re-verified directly against current `HEAD` rather than taken on the commit
message's word:

- The lazy import at `routing_check.py:540` is still function-local, but now carries the comment
  that the criterion's second branch allows — "direct-path execution needs this sibling".
- `check_local_model_endpoint` sends `f"Antigravity/{PROTOCOL_VERSION}"`, so the user-agent version
  is derived and can no longer drift from the protocol.

The three formatting criteria (import ordering, line width, recovered rationale comments) are
attested by the commit plus a clean `ruff` gate, not by a line-by-line re-read. If that distinction
ever matters, re-read the `de12bda` diff — do not re-derive it from the current file.
