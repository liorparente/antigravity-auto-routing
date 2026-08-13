# 04 — Blocking stance per occasion

**What to build:** Plan-review and code-review dialogues gate mission progress — the caller does
not proceed until the dialogue resolves. Post-mortem dialogues run without blocking the mission
path; their occurrence and outcome are still recorded via telemetry/transcript regardless of when
they finish relative to the mission.

**Blocked by:** 01, 03.

**Status:** done

- [x] A plan-review or code-review dialogue in progress prevents the caller from observing mission
      completion until the dialogue result is available.
- [x] A post-mortem dialogue is dispatched without the caller waiting on it — the mission path
      returns/proceeds independent of the post-mortem's completion.
- [x] A post-mortem's eventual record (transcript + telemetry) is still written and discoverable
      after the fact, exactly as if it had blocked.
- [x] Test doubles can assert "blocked" vs "did not block" as an observable behavior (e.g. call
      ordering or a fake that records whether the mission path returned before the dialogue settled).

## Notes

Landed in commit `a13db3a`. New `dispatch_post_mortem_consultation` spawns
`run_advisory_consultation_debate` on a non-daemon background thread. `/code-review` caught a real
gap: the first pass used `daemon=True`, which would let the interpreter kill the thread without
cleanup on process exit — breaking the "written exactly as if it had blocked" guarantee. Fixed to
`daemon=False`, plus a last-resort exception wrapper so an unexpected bug inside the debate loop
can't vanish silently. 185 tests pass.
