# 13 — Every worker invocation leaves a measurement

**What to build:** Today's biggest blind spot: `production_invoker.invoke_worker` launches a worker,
returns its stdout, and remembers nothing. After this ticket every invocation — success or failure —
leaves a worker-execution record carrying duration, cost estimate, outcome, retry count, effort,
model and model family.

The injected-callable seam is the constraint. The consultation takes a `(model, effort, prompt) -> str`
callable and must keep taking exactly that; instrumentation may not leak into the signature the
consultation depends on, and a test that injects its own fake must stay unaffected.

**Blocked by:** 12

**Status:** done

- [x] A successful worker invocation appends one worker-execution record.
- [x] A failed invocation — non-zero exit and timeout alike — appends one too, marked failed.
- [x] The record carries duration, cost estimate, success flag, retry count, effort, model, and model
      family.
- [x] The consultation's injected worker callable keeps its existing signature; a test injecting a
      fake produces no journal writes it did not ask for.
- [x] The record correlates to the invocation's task by TaskIdentity.
- [x] Tests cover success, non-zero exit, and timeout, asserting on the records written.

Delivered by commit `f9763ab`.
