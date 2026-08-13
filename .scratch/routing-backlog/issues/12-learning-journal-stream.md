# 12 — The LearningJournal stream

**What to build:** A dedicated, append-only JSONL stream that records what happened, separate from the
audited `.ralph/routing_telemetry.jsonl`. This ticket lands the module, the record contract for all
four signal families, and the structural guarantee that the journal can never become a place task
content leaks from.

The audited telemetry stream is not touched. Its record contract — including the deliberate
`kind`-asymmetry that tells advisory records from council records apart — stays frozen. The two
streams correlate through TaskIdentity.

All four families' schemas land here, including dialogue-quality, even though spec 0003 is what
fills that one (ticket 24). One module owns the record contract; its writers live elsewhere.

**Blocked by:** —

**Status:** done — commit `cf433ae`, on `main` since the phase A merge `70f653e`

The `spec/0004-learning-loop` branch this originally landed on was fully merged and has since been
deleted, along with its worktree; every phase A commit named in tickets 12–15 is reachable from
`main`. Look for them there, not on a branch.

Two independent Codex Sol reviews (standards and spec axes) ran against the first implementation and
found four real defects; all four were fixed before the commit. The two that mattered: every field
of every record was runtime-unvalidated behind erased annotations, so `kind` and `success` accepted
free text; and the marker scan was applied to the task id itself, which rejected ids
`agent_council` legitimately generates (`secret-rotation`) and would have silently broken the
cross-stream join for exactly the security-adjacent tasks. A fifth finding — snake_case `kind`
values vs the spec's hyphenated prose — was rejected: `AdvisoryTelemetryRecord`'s
`kind="advisory_consultation"` is the governing precedent.

- [x] A journal module writes append-only JSONL beneath the injected root directory, at a path
      distinct from the routing telemetry stream.
- [x] Four record families exist and are distinguishable by kind: worker-execution, outcome,
      dialogue-quality, compliance.
- [x] A write failure is reported to the caller and never raised, matching how the consultation's
      telemetry write already behaves.
- [x] Content-freedom is structural, not a convention: a record carrying task text, prompt text, or
      a matched secret value is rejected by construction rather than by reviewer discipline.
- [x] A normal task may carry a coarse task-type tag; a sensitivity-halted task carries no tag of any
      kind.
- [x] Records from both streams can be joined on TaskIdentity for the same task.
- [x] Tests cover each family's schema, the content-freedom rejection, the halted-task tag rule, and
      the cross-stream join.
