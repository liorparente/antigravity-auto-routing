# 06 — A transcript for every outcome, plus telemetry

**What to build:** The developer can always read what the two models actually said — not only when
they agreed. The round-by-round transcript is written on every exit path: consensus, stalemate, worker
failure, and the sensitivity halt. Alongside it, every consultation leaves a structured telemetry
record, so an auditor can later tell which decisions were genuinely deliberated.

This waits on 04 and 05 because only then do all four outcomes exist to be covered.

**Blocked by:** 04, 05

**Status:** done

- [x] The transcript artifact is written for all four outcomes: consensus, stalemate, worker failure,
      sensitivity halt.
- [x] The transcript presents each round's Planner proposal and Critic response in order, readable by
      a human without tooling.
- [x] Every consultation emits one telemetry record carrying the task identity, rounds run, outcome,
      and which models played Planner and Critic.
- [x] All artifacts are written beneath the injected root directory.
- [x] A test covers each of the four outcomes writing its transcript.
