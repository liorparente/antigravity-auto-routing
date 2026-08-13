# 25 — Ground truth reaches the journal

**What to build:** Callers for the outcome family. Ticket 14 landed `learning_outcomes.py` — four
public entry points, validated, tested, content-free — and nothing in the system calls any of them.
`grep -c learning_outcomes` returns `0` in both `advisory_consultation.py` and `routing_check.py`;
every reference anywhere in the repository is a docstring or a test. The family that answers "were
we right" is a library with no producer, so the journal faithfully records every decision and not
one result.

This is not a defect in ticket 14. Its acceptance criteria asked for a public entry point and got a
good one — deliberately taking a plain `task_id` rather than reaching for the decision, so no caller
pays for a lookup it does not need. The wiring is simply work no ticket owned, and it stayed
invisible because the entry points are fully tested: green tests over a component nobody calls.

The four truths do not wire the same way, and the ticket is not done until each has an answer:

- **Test results and review verdicts** — produced by whatever actually runs them today. Name that
  producer rather than assuming one exists; if the run happens outside any process this repository
  controls, this truth's answer is a documented step, not a code path.
- **Plan acceptance or rejection** — the consultation already knows its own outcome, and
  `advisory_consultation.py` is the natural site.
- **The human's stalemate choice** — made *after* the consultation has halted and returned, by a
  person, outside the process. There may be no in-process caller at all, in which case the
  deliverable is a named step in `protocol.md`/`SKILL.md` that the orchestrator performs when it
  acts on a stalemate report. `_build_stalemate_report`'s three options already map one-to-one onto
  `OUTCOME_VERDICTS["stalemate_resolution"]` (`planner`, `critic`, `human`), so nothing needs
  inventing — only recording.

A truth that ends this ticket with neither a code caller nor a documented step is the same silence
this ticket exists to end; say so explicitly rather than leaving it unlisted.

Two constraints carry over from phase A and are not negotiable here. The `task_id` must be the id
the *decision* recorded — for a consultation, `_resolve_task_id(task_description, task_id,
"consensus")`, the same id `make_journaled_invoke_worker` already journals under — or the join that
justifies the entire family does not hold. And instrumentation never aborts what it observes:
`append_journal_record` returns its failure rather than raising, and every new caller keeps that
property, the way `advisory_consultation`'s journal-wiring `except` already does.

**Blocked by:** 14

**Status:** ready-for-agent

- [ ] Each of the four ground truths has a named producer: a code path that calls the entry point,
      or a documented orchestrator step where no code path can exist.
- [ ] A consultation that reaches a stalemate leaves a record of the option the human chose, once
      that choice is acted on.
- [ ] Every outcome record carries the TaskIdentity the decision recorded, so the decision and its
      result read together across the two streams.
- [ ] A journal-write failure degrades the instrumentation and never the thing being measured.
- [ ] A sensitivity-halted task produces no outcome record, consistent with ticket 12's rule.
- [ ] Tests assert the records appear from a whole run through the public entry points, not from
      calling `learning_outcomes` directly — the gap this ticket closes was invisible to exactly
      that kind of direct test.
