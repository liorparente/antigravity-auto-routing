# 08 — Settle the ADR 0002 debt

**What to build:** Make the code match the architectural decision that claims to describe it. ADR 0002
records as accepted outcomes that the pass-through validator is eliminated and that root-directory
pollution is removed from the metrics entry point. Neither happened: the wrapper survives with a
docstring calling itself deprecated, the metrics entry point gained a parameter rather than losing
one, and two call sites still resolve a fresh security context per call — the very approach the ADR
considered and rejected. A reader trusting the ADR goes looking for code that does not exist.

This touches the most-travelled path in the routing checker. It ships on its own, with its own review.

**Blocked by:** None — can start immediately.

**Status:** closed

- [x] Characterisation tests pin the current observable behaviour of metrics computation and signature
      verification through their public entry points, and pass before anything is changed.
- [x] The pass-through validator is gone; its callers use the security context directly.
- [x] The root directory is no longer a parameter of the metrics entry point.
- [x] No call site constructs a security context per call; it is resolved once, as the ADR requires.
- [x] The glossary and the code agree on whether the security context is immutable — whichever way
      that is settled.
- [x] The characterisation tests still pass, unchanged.
