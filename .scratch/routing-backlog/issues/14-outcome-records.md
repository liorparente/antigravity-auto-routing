# 14 — Ground truth is joined to the decision that produced it

**What to build:** The system records decisions but never learns whether they were right. This ticket
adds the outcome family: each truth is recorded when it becomes known, carrying the TaskIdentity of
the decision it grades.

Four truths: whether tests passed, what a review verdict was, whether a plan was accepted or
rejected, and — when the consultation reaches a stalemate — which option the human chose. The last
one is the only signal the system has about how its owner settles disagreements, and today it is
discarded the moment it is acted on.

**Blocked by:** 12

**Status:** done

- [x] A public entry point records a test result, a review verdict, a plan acceptance or rejection,
      and a human stalemate choice.
- [x] Every outcome record carries the TaskIdentity of the decision it grades, so a decision and its
      result can be read together.
- [x] The stalemate resolution is recorded as the human's chosen option, not as free text.
- [x] Recording an outcome for an unknown task is handled explicitly rather than silently producing
      an orphan record.
- [x] Outcome records stay content-free under the same rule as every other family.
- [x] Tests cover each of the four truths and the decision-to-outcome join.

Delivered by commit `2336674`. `advisory_consultation.py` untouched — zero lines.
