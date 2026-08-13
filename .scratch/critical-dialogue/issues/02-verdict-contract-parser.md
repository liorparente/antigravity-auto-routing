# 02 — The VerdictContract parser

**What to build:** Replace/extend `_parse_critic_verdict` and `CriticVerdict` so a Critic response
must contain, in order, rationale, then engagement units, then the verdict line. Engagement units
are (a) quotes from the reviewed artifact — verified mechanically by matching the quoted text
against the artifact's actual content, not merely present in the response — and (b) numbered atomic
objections. An approval with zero verified engagement units, a quote that fails verification, or an
unparseable response must all parse as "not approved" — this is the same rule spec 0001 already
applies to silence, extended to bare/fabricated approval.

**Blocked by:** 01 (needs the occasion-aware call sites this will run inside).

**Status:** done

- [x] A response with rationale → verified quotes → numbered objections → "APPROVE" parses as approved,
      and the parsed result carries the engagement-unit counts.
- [x] A response that is a bare "APPROVE" (zero engagement units) parses as not-approved.
- [x] A response quoting text that does not appear in the reviewed artifact parses as not-approved —
      that quote does not count toward engagement.
- [x] Zero objections is valid only when accompanied by at least one verified quote; zero of both is
      not-approved.
- [x] An unparseable response (missing verdict line, malformed structure) parses as not-approved,
      matching spec 0001's "absence of rejection is not agreement" rule.
- [x] No assertions on prompt wording beyond the two pinned exceptions (WorkerModeToken presence,
      VerdictContract parse behavior) per the spec's Testing Decisions.

## Notes

Landed in commit `ab6ad74`. The contract format: `QUOTE: "<verbatim text>"` lines (verified
byte-for-byte against the artifact), `N. <objection text>` lines, then `VERDICT: APPROVE`/`VERDICT:
REVISE` last. Approval requires `verified_quote_count >= 1` alone — objections are tallied but never
substitute for a quote. `/code-review` caught two real issues across two rounds: (1) the first pass
approved on `verified_quotes + objections >= 1`, letting fabricated objections substitute for a
missing quote — spec 0003's phrasing only licenses the reverse (zero objections is fine *with* a
quote); fixed to require a verified quote unconditionally. (2) a stale top-of-file comment kept
restating the old OR-rule after the code was fixed; corrected. 159 tests pass.
