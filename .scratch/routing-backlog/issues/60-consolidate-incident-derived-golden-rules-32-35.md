# 60 — Consolidate Incident-Derived Golden Rules 32–35

**Spec:** 0014 — Institutional Memory Catalog

**Status:** done — consolidated into Golden Rule 32 (Full consolidation policy chosen to optimize injection-slot budget)

## Proposal

Decide whether incident-derived Golden Rules 32–35 should remain four separate catalog entries or be consolidated into fewer rules that express the shared review-convergence lesson.

Rules 32–35 cover closely related facets: a corrected factual claim needs fresh proof, an entire comment block should be re-derived when one claim is wrong, a verifier should receive the history of prior failed fixes, and cosmetic or non-functional drift introduced by a fix should be settled in the same pass. Their overlap is real, but Spec 0014 does not decide the desired consolidation boundary.

## Trade-offs

- **Keep four granular rules:** preserves facet-specific keywords, file patterns, and retrieval. A task can surface the narrow lesson most relevant to its failure mode. The cost is injection-slot competition: related rules can occupy several of the bounded scoped-memory slots and crowd out unrelated guidance.
- **Consolidate the rules:** reduces duplication and preserves more scoped-memory slots for other categories. The cost is coarser retrieval: one broader rule may score less precisely, and a worker may miss a specific operational facet hidden inside a long combined directive.
- **Partial consolidation:** can combine the factual-claim rules (32–34) while leaving the same-pass cosmetic-drift rule (35) separate. This balances slot use against retrieval specificity, but requires defining and defending the grouping boundary.

## Decision Needed

Choose the catalog policy for balancing granular facet retrieval against bounded injection-slot competition, then update the catalog, generated institutional memory, and retrieval tests together. Until that policy is decided, retain rules 32–35 as separate entries.
