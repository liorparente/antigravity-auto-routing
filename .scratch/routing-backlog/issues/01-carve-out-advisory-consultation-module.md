# 01 — Carve out the AdvisoryConsultation module

**What to build:** The advisory consultation code stops living inside the deterministic agent
council and moves into a module of its own. Nothing behaves differently — the only observable change
is where the symbols live. This exists so that the council's claim about itself, that it has no model
and no network dependency, stays true once the real deliberation loop arrives. That claim is not
decoration: the 24-hour planning cache and the signed decision manifest are only sound while the same
input produces the same output.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] The advisory consultation entry point, its result type, its trigger predicate and its round cap
      live in a module of their own.
- [~] The agent council module no longer defines or references them, and its stated "no model or
      network dependency" is accurate as written.
      **Partial.** The move is clean — no advisory symbol remains. But the claim is only true under the
      narrow reading "the council" = the `AgentCouncil` class. The module still holds a live HTTP probe
      of the local model endpoint, a few screens below that sentence. Pre-existing, not introduced
      here, and the cluster it belongs to is exactly what ticket 09 triages. Carried there rather than
      widening this ticket.
- [x] The existing test asserting that the unimplemented consultation raises still runs, from the new
      location.
- [x] The full suite passes with no change in test count — 89 before, 89 after.
