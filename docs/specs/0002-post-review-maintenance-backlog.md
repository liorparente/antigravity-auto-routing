# Spec 0002 — Post-review maintenance backlog

* Status: Implemented
* Date: 2026-08-10
* Source: two-axis code review (Standards + Spec) of the uncommitted work on 2026-08-10

Four independent items. Each is separately shippable; none blocks another. They are grouped here
because they share an origin, not because they should land as one change.

---

## Item 1 — Settle the ADR 0002 debt

### Problem Statement

ADR 0002 lists as accepted outcomes that the pass-through `HMACValidator` class is *eliminated* and
that `root_dir` parameter pollution is *removed* from `compute_metrics`. Neither happened.
`HMACValidator` survives, relabelled as a deprecated compatibility wrapper; `compute_metrics` still
takes `root_dir` **and** gained a security-context parameter, a net increase. A reader who trusts the
ADR will look for code that does not exist, and the "on-demand resolution" approach the ADR
explicitly rejected is still in effect at two call sites, which construct a fresh security context
per call.

### Solution

Either finish the refactor the ADR describes, or amend the ADR to record what was actually decided.
The two must agree. Finishing it is preferred: the ADR's reasoning still holds, and the wrapper's
own docstring already calls itself deprecated.

### Implementation Decisions

- Remove the pass-through validator and route its callers to the security context directly.
- Remove `root_dir` from the metrics entry point; the resolved context already carries it.
- Eliminate per-call context construction at the signature-issue and validation call sites — resolve
  once at startup, as the ADR's chosen option requires.
- This touches the most-travelled path in the routing checker. It ships as its own change with its
  own review, never bundled with unrelated fixes.
- The `SecurityContext` is documented in `CONTEXT.md` as *immutable*; the class is currently plain and
  mutable. Either make it so or correct the glossary in the same change.

### Testing Decisions

Characterisation tests come first: pin the current observable behaviour of metrics computation and
signature verification through the public entry points, confirm they pass, then refactor and confirm
they still pass. Prior art: `Phase1CharacterizationTests` and `CalibrationSignatureTests`. No new test
seam is needed — the public functions are already the seam.

### Out of Scope

Any change to the HMAC scheme, the secret-resolution rules, or the manifest format.

---

## Item 2 — Triage the unreferenced helpers

### Problem Statement

Roughly twelve functions have zero call sites and zero tests: the sensitivity, local-routing,
telemetry and escalation helpers added to the agent council, plus several helpers in the routing
checker. Some encode real policy from ADR 0004 (the proactive security gate, full audit logging) that
nothing currently enforces; others may be genuinely obsolete. Unreferenced code that *looks* like
enforcement is worse than absent code, because a reader assumes the policy is live.

### Solution

Decide per function: wire it into the path that should call it, or delete it. Nothing stays in the
"written but unreachable" state.

### Implementation Decisions

- Produce the inventory first: every function, its intended ADR rule, and whether a caller should
  exist.
- For each keeper, identify the call site that should invoke it and wire it there with a test that
  exercises the policy end to end — not a test that calls the helper directly, which would prove
  nothing about enforcement.
- For each obsolete one, delete it. Deletion is recorded in the change description, not in a comment.
- `evaluate_sensitivity` returns the same value twice under two names; collapse it or give the second
  value a real meaning.
- `should_route_to_local_model` returns a bare `False` both for "not sensitive, cloud is fine" and for
  "sensitive, but the local model is unavailable". The second case must fail closed, and a boolean
  cannot express that. Before this function acquires its first caller, give it a return value that
  distinguishes *route local*, *route cloud*, and *halt* — otherwise the first caller written against
  it will leak sensitive tasks to the cloud while looking correct.

### Testing Decisions

A helper is only "wired" if a test that drives the *caller* fails when the helper is removed. That is
the acceptance criterion. Directly unit-testing a helper that nothing calls does not count.

### Out of Scope

The AdvisoryConsultation stub, which spec 0001 covers.

---

## Item 3 — Resolve the contradictory sandbox guidance

### Problem Statement

`knowledge/institutional-memory.md` asserts that setting `TMPDIR=/tmp` together with
`GIT_OPTIONAL_LOCKS=0` fully resolves the worker socket-initialisation error. `protocol.md` Rule 4.7
states the fix is to bypass the IDE sandbox on the tool call, and explicitly notes that filesystem
permission changes do not address socket isolation. An agent reading the knowledge file will apply a
remedy the protocol says does not work.

### Solution

Determine empirically which is true, then make one of the two documents defer to the other. A short
reproduction — attempt a worker invocation under the sandbox with the environment variables set —
settles it.

### Implementation Decisions

- The protocol is the contract; the knowledge file is a note. If they conflict after verification,
  the knowledge file changes.
- If the environment variables turn out to help *partially*, say exactly what they fix and what they
  do not. A partial remedy documented as a complete one is the failure being corrected here.
- Record the outcome in `ERRORS.md` — this is the third instance this month of a documented
  resolution that did not match reality.

### Testing Decisions

Not unit-testable; verification is the reproduction itself. Record the commands run and their output
in the `ERRORS.md` entry so the next reader does not have to re-derive it.

### Out of Scope

Any change to Rule 4.7 itself unless the reproduction proves it wrong.

---

## Item 4 — Hygiene sweep

### Problem Statement

Small inconsistencies accumulated across the recent changes: an `fcntl` import buried inside a
function body, a dataclass import breaking the alphabetised standard-library block, several lines
running roughly forty characters longer than their surroundings, a hardcoded `Antigravity/3.4`
user-agent while the protocol is at v3.5, and rationale comments deleted during a refactor without
being relocated. None is a defect; together they make the module read as though it had several
authors who never spoke.

### Solution

One mechanical sweep, no behaviour change.

### Implementation Decisions

- Move the function-local import to the module header unless there is a documented reason for lazy
  import; if there is one, write it as a comment.
- Restore the standard-library import ordering.
- Bring long lines into line with the file's prevailing width.
- Derive the user-agent version from the protocol version rather than restating it, so it cannot drift
  again.
- The refactor dropped explanatory comments that recorded *why* certain constraints exist. Recover
  them from git history and reattach them to the code that now carries the constraint.

### Testing Decisions

The existing suite is the whole test: it must pass before and after with no assertion changes. Any
required assertion change means the sweep changed behaviour and has exceeded its scope.

### Out of Scope

Any renaming, signature change, or restructuring. This item is deliberately trivial so it can be
reviewed in one pass.
