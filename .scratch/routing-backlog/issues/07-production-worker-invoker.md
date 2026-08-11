# 07 — The production worker invoker

**What to build:** The implementation that makes the consultation real — the default callable that
actually launches Planner and Critic workers via the protocol's own command templates. Until this
lands, the loop is exercisable only against fakes. It depends only on the callable's contract from 02,
so it can proceed alongside the outcome tickets.

**Blocked by:** 02

**Status:** done

- [x] A default implementation of the injected callable launches the real CLI workers using the
      command shapes the protocol documents.
- [x] Every prompt it sends carries the nested-worker token, so the worker executes its mission
      instead of self-blocking on the routing gate.
- [x] Every invocation is non-interactive and cannot block waiting on a terminal.
- [x] Each round carries a time limit; exceeding it fails the consultation closed rather than hanging
      the mission.
- [x] The Planner and Critic models and their reasoning efforts are parameters with tier-appropriate
      defaults, not literals inside the loop.
- [x] Verified once end to end against real workers, with that run's outcome recorded.
