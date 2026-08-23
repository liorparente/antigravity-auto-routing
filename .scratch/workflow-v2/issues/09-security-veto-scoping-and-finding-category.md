# 09 — Security Veto Scoping & Finding Category Discrimination

**What to build:** Resolve the open design question raised in ticket 07 Pass 4 (finding N-P1) — whether `SecurityVetoHandler.check`'s non-`reviewer_security` `BLOCK`-vote veto path should be scoped strictly to security-relevant findings, or remain domain-agnostic across any perspective's Critical finding.

**Blocked by:** 07 — Fast-Path 1-Shot Council Review & Security Veto Engine

**Status:** open

## Background

ADR 0007 and ADR 0012 both define the unilateral security veto as a *security* signal: "any single model detecting a Critical or High severity threat **with verified locus**" (ADR 0007) triggers `SECURITY_HALT`, and ADR 0012 attributes the veto to "any single **perspective**" evaluating CWE vulnerabilities, input validation, auth boundaries, credential isolation, and sensitive data leakage — i.e. `reviewer_security`'s own domain.

`SecurityVetoHandler.check` (`debate_state_machine.py`) currently has two independent triggers:

1. A configured high-confidence, high-severity finding (`severity in veto_severities` and `confidence >= security_threshold`) attached to *any* vote, *any* provider/perspective, *any* verdict — this trigger is already domain-agnostic by design and is not in question here.
2. An explicit `"BLOCK"` verdict from a vote, gated two ways: `reviewer_security`'s own `BLOCK` always vetoes unilaterally (no finding required — the perspective's identity *is* the security signal); every other perspective's `BLOCK` only vetoes when it also carries a finding whose `severity` is in `veto_severities` *and* now (post N-S1) whose `confidence >= security_threshold`.

N-P1 (deferred from ticket 07's Pass 4 review) asks: should trigger 2's non-`reviewer_security` path go further and also require the attached finding's `category == "security"` (or an equivalent "verified locus" check), so a `reviewer_architecture` or `reviewer_maintainability` vote carrying a Critical-severity *non-security* finding (e.g. a maintainability blocker miscategorized at "critical" severity) cannot trigger a `SECURITY_HALT`?

## Options

1. **Scope strictly to `perspective == "reviewer_security"`.** Drop trigger 2's non-`reviewer_security` branch entirely; only `reviewer_security`'s own `BLOCK` (or trigger 1's confidence/severity check, which already applies to any perspective) can veto.
   - *Pros:* Matches ADR 0012's plain reading most literally — "security veto" is `reviewer_security`'s role. Simplest mental model: one perspective owns the security halt.
   - *Cons:* Trigger 1 already lets *any* perspective's sufficiently-severe, sufficiently-confident finding veto — so this option would leave an inconsistency where a `BLOCK` verdict is treated more narrowly than a bare finding from the same non-security perspective. Regresses ticket 07's original intent that a non-security reviewer's severe, confident `BLOCK` should still fail closed.

2. **Require `category == "security"` on the attached finding** (in addition to severity + confidence, already added by N-S1) before a non-`reviewer_security` `BLOCK` vote can veto.
   - *Pros:* Directly encodes "verified locus" from ADR 0007 — a finding must actually claim to be a security issue, not merely be labeled Critical, to trigger the security-specific halt path. Keeps trigger 2 symmetric with trigger 1 if the same `category` check is added there too.
   - *Cons:* Requires every perspective adapter's finding schema to reliably populate `category`, and requires deciding what happens to a `category`-less finding (fail open and let it veto, or fail closed and never let it veto) — a new source of silent behavior drift if adapters disagree on the field's presence or spelling.

3. **Remain domain-agnostic on all Critical findings** (status quo after N-S1): any perspective's `BLOCK` vote with a Critical/High-severity, high-confidence finding can veto, regardless of the finding's subject matter.
   - *Pros:* No new schema dependency; simplest to implement (already the current behavior); treats "Critical + confident" as itself a sufficient bar for a fail-closed halt, independent of which reviewer lens produced it.
   - *Cons:* Diverges from ADR 0007/0012's framing of the veto as a specifically *security* mechanism; a `reviewer_maintainability` Critical finding halting the pipeline the same way a `reviewer_security` one does may surprise an operator reading the ADRs, and blurs the distinction between "this needs a security halt" and "this needs a plain non-quorum stalemate".

## Notes

- Whichever option is chosen must not weaken trigger 1 (the confidence/severity check for *any* vote's attached finding), since that is settled behavior from ticket 07 and N-S1, not part of this question.
- If category discrimination is adopted (option 2), it should be applied to `_finding_field(f, "category", ...)` reads consistent with the existing `_finding_field`/`_is_finding` shape-dispatch already used for `severity`/`confidence`, so both dict/`MappingProxyType` and `StructuredFinding` findings are handled identically.
