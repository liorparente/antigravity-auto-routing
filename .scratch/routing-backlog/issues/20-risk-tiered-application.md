# 20 — Risk-tiered application: what applies itself and what waits for a human

**What to build:** Four tiers, each with a different answer to "who says yes":

- **Memory lessons** — auto-apply, and appear in the weekly report.
- **Routing-table updates** — auto-apply, but only after passing the acceptance gate; one report line
  each, so a policy change is frictionless yet never invisible.
- **Brief diffs** — held as pending proposals until the human explicitly approves, because a brief
  shapes how every worker understands every task.
- **The protocol** — unreachable. Not "forbidden by a check": there is no code path from the learner
  to the protocol files at all.

**Blocked by:** 18, 19

**Note:** `learned_state.adopt` refuses (with a `ValueError`) a change identical to the document's
current content — tier application must treat that refusal as a successful no-op, not a failure,
since the intended state was already current.

**Status:** done — implemented in `risk_tiered_application.py`, tested in `test_risk_tiered_application.py` (15/15 unit tests passing).

- [x] A memory lesson applies without a gate and appears in the report.
- [x] A routing-table update applies only after the gate passes, and is rejected outright when the
      gate fails.
- [x] A brief diff is held pending and applies only against a recorded human approval.
- [x] No code path in the learner writes any protocol file — demonstrable by construction, not by a
      guard clause.
- [x] Every applied change goes through the versioning from ticket 19.
- [x] Tests cover each of the four tiers, including a brief diff that is never approved.
