# 09 — Triage the unreferenced helpers

**What to build:** Every helper is either reachable or gone. Roughly a dozen functions currently have
no caller and no test: the sensitivity, local-routing, telemetry and escalation helpers, plus several
in the routing checker. Some of them encode real policy — the proactive security gate, full audit
logging — which means the policy reads as enforced while nothing enforces it. That is worse than the
code being absent, because a reader stops looking.

One helper deserves specific attention before it ever gains a caller: the local-routing predicate
returns a bare `False` both for "not sensitive, cloud is fine" and for "sensitive, but the local model
is unavailable". Those are opposite instructions. A boolean cannot carry the difference, and the first
caller written against it will leak sensitive tasks to the cloud while looking correct.

**Blocked by:** None — can start immediately.

**Status:** closed

- [x] An inventory exists: each unreferenced function, the rule it claims to enforce, and a verdict of
      wire-up or delete.
- [x] Every keeper has a real caller, and a test driving that **caller** fails if the helper is
      removed. A direct unit test of an uncalled helper does not satisfy this.
- [x] Every non-keeper is deleted outright.
- [x] The local-routing predicate distinguishes route-local, route-cloud and halt before it acquires
      its first caller.
- [x] The sensitivity evaluator no longer returns the same value twice under two names.
- [x] The agent council module's docstring and its actual contents agree. It opens by promising "no
      model or network dependency", and a live HTTP probe of the local model endpoint sits a few
      screens below. Either the local-routing cluster leaves that module or the promise is reworded —
      carried over from ticket 01, which found this but correctly refused to widen its own scope.
