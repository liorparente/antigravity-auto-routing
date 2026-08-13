# 02 — A single-round consultation that reaches consensus

**What to build:** A caller can run an AdvisoryConsultation on a task and get back a real result. The
Planner is asked for a plan, the Critic is asked to judge it, an explicit approval is recognised, and
the agreed plan is written out for the developer to approve. Every model call goes through one
injected worker-invocation callable, so the whole path is exercisable offline.

This is the narrowest complete path through the feature. It is demoable on its own: hand it a fake
that approves, and a real plan file appears.

**Blocked by:** 01

**Status:** done

- [x] The entry point accepts a callable shaped `(model, effort, prompt) -> text` and reaches a worker
      no other way. `invoke_worker` is a required parameter; the module imports no `subprocess`,
      `socket`, or HTTP client.
- [x] Given a fake that approves on the first response, the result reports one round and consensus.
- [x] The agreed plan is written to the plan artifact, under a caller-supplied root directory, so a
      test never writes into the real repository. `root_dir` is required — there is no default root.
- [x] Consensus comes only from an explicit approval verdict in the Critic's response; the loop reads
      the verdict line, not the surrounding prose. Empty, prose-only, and near-miss responses are all
      covered by test.
- [x] The result is neither stored in the planning cache nor given a calibration signature — both
      mechanisms promise a reproducibility this feature cannot offer.
- [x] The suite passes and no test contacts a network or spawns a process. 89 before, 94 after
      (6 added, 1 obsolete `NotImplementedError` test removed).

**Carried out of this ticket.** The Codex Sol review raised one defect that no ticket currently owns:
when a consultation fails to reach consensus and `root_dir` already holds an `implementation_plan.md`
from an earlier run, the stale plan is left in place. The returned result is honest, but the artifact
on disk is not. Belongs to 04, which owns the non-consensus exit path. The review's other findings
were all deferred-ticket scope — revision rounds (03), stalemate report (04), sensitivity gate (05),
transcript and telemetry (06), production caller and per-round timeout (07).
