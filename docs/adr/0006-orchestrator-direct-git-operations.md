# 0006. Orchestrator Direct Git Operations

* Status: accepted
* Date: 2026-08-10

## Context and Problem Statement

The Worker Routing Protocol gates the Orchestrator from any state-modifying action: every write or command must route to a worker CLI (`codex exec`, `claude -p`, `agy -p`). Worker CLIs run inside a sandbox that locks the `.git/` directory (`Operation not permitted`), so any mission that needs version control — creating a branch, committing staged work, reverting a bad edit — cannot be delegated. The protocol's own gate then forbids the Orchestrator from running it directly. The result is a deadlock: no path exists to perform version control at all.

This was first identified on 2026-08-06. That entry's logged "Resolution" claimed `protocol.md` had been updated to add version control to the Allowed Direct Actions list — but the edit was never actually made. The Allowed Direct Actions list continued to permit only read-only diagnostics (`git status`, `git log`, `curl` health checks), so the deadlock persisted for over a month before being caught here.

## Decision Drivers

* **Break the deadlock:** worker sandboxes cannot touch `.git/`, so *something* must be authorized to run version control directly, or the deadlock is permanent by construction.
* **Minimize blast radius:** the Orchestrator has no worker-routing gate protecting the actions it takes directly. Whatever is allowed direct must be safe to run without a second opinion.
* **Preserve the gate's purpose:** the gate exists to keep the Orchestrator from self-executing *code* changes (ADR 0005, Pillar 1). Version control is not code authorship — it moves or records changes that were already made, ideally by a routed worker.

## Considered Options

1. **Keep the gate closed; require manual user execution of every git command.** The Orchestrator would report "worker sandbox cannot touch `.git/`, please run `git commit ...` yourself" for every version-control step of every mission.
2. **Allow the Orchestrator to run any git command directly**, trusting the existing user-confirmation flow for destructive actions to catch mistakes.
3. **Allow a narrow, explicit set of non-destructive git commands directly; keep destructive/irreversible commands behind explicit user approval** (Selected).

Option 1 was rejected: it does not resolve the deadlock, it just relocates it to the user, and it would inject a manual step into every mission that touches version control — defeating the purpose of an autonomous routing protocol for the one category of operation (git) that is guaranteed to recur on nearly every mission.

Option 2 was rejected: the Orchestrator's own hard gate exists precisely because unreviewed direct execution is the failure mode this protocol was built to prevent (ADR 0005, Pillar 1). Extending that same unreviewed-execution trust to `git push --force`, `git reset --hard`, or `git clean -fd` — commands that can discard or overwrite work with no local recovery path — reintroduces the exact risk the gate was designed against, for no corresponding benefit (none of these commands are needed to break the `.git/` sandbox deadlock).

## Decision Outcome

Chosen option: **Option 3 — narrow, explicit allow/deny list.**

Added to `## ✅ Allowed Direct Actions` in `skills/worker-routing/protocol.md`:

* **Allowed direct (no worker, no gate):** `git add`, `git commit`, `git branch`, `git checkout`, `git revert`, `git stash`, `git tag`. These are non-destructive or trivially reversible — they add, record, or move pointers without discarding history irrecoverably.
* **Forbidden without explicit user approval:** `git push`, `git reset --hard`, `git clean -fd`, and any `--force` variant. These can lose local work, overwrite shared/remote state, or delete untracked files with no undo.

This allowance covers version control only. It does not extend the Orchestrator's direct-execution rights to source-code edits, which must still route to a worker under the existing gate.

### Positive Consequences

* The `.git/`-sandbox deadlock is resolved without weakening the gate around code authorship.
* The allow-list matches exactly the operations version-control workflows actually need mid-mission (stage, commit, branch, switch, revert, stash, tag); nothing destructive is silently permitted.
* `ERRORS.md`'s 2026-08-06 entry and `protocol.md` no longer contradict each other.

### Negative Consequences

* The Orchestrator can now commit or create branches without a worker-routing declaration for that specific action, slightly narrowing the routing audit trail (`routing-audit.sh`) for version-control steps specifically. This is judged acceptable because these actions are non-destructive and fully visible in `git log`/`git reflog` regardless of how they were invoked.
* The allow/deny boundary must be kept in sync by hand if git grows new destructive subcommands or flags; it is not derived from a general "is this reversible" classifier.
