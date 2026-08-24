# Institutional Memory — 25 Golden Rules

## Metadata
- **Last updated:** 2026-08-24
- **Format:** distilled from 111 historical entries; full history in
  [`knowledge/archive/institutional-memory-legacy.md`](archive/institutional-memory-legacy.md).
- **Retrieval:** `skills/worker-routing/prompt_assembler.extract_scoped_memory`
  scores these rules against a task description and target files, and
  injects only the top 3–5 into a worker's mission brief.

Each rule is scored on keywords in the task description and file-pattern
matches against target files — see `GOLDEN_RULES` in `prompt_assembler.py`.

## Architecture & Deep Modules

1. **Decompose monoliths into deep, single-purpose layers behind a thin
   facade.** A module past a few thousand lines should split into leaf
   modules with isolated test seams, re-exported through a facade that keeps
   100% backward-compatible public names.
2. **Keep content-agnostic stores ignorant of what their content means.**
   A store like `learned_state.py` must not parse, interpret, or validate
   the meaning of what it persists — only its shape. Meaning belongs to the
   caller, not the store.
3. **A junction point only guarantees what actually reaches it.** Routing
   every exit through one `try/finally` or one return looks like a
   structural guarantee that every exit writes its result — it only covers
   code that reaches the junction. Map every I/O op that happens *before*
   it separately.
4. **Leaf modules never import siblings; use a hybrid import shim instead.**
   `if __package__: from . import x else: import x` (or a `_load_sibling`
   helper) keeps module identity stable in `sys.modules`, so
   `unittest.mock.patch.object` intercepts calls deterministically instead
   of silently missing them.
22. **Consolidate multi-model debate into a unified topology engine behind a thin facade.**
    Unify overlapping consultation scripts into a single state machine supporting `Dyad`
    (1-on-1 pairs for Medium tasks) and `CouncilPanel` (weighted quorum for Complex tasks),
    preserving legacy wrappers as sub-25-line delegators.

## Testing & TDD Seams

5. **An assertion that passes without exercising its named path proves
   nothing.** For every new or changed assertion, ask "what has to break
   for this to fail?" — this is the dominant defect class in this repo.
6. **A parameter no test ever gives a non-default value is untested.**
   Count parameters that stay at their default across the whole suite, and
   drive each one to a value that flips the outcome, not just one that
   avoids a crash.
7. **Isolate CI test files into separate processes.** Mocks and process
   handles leak across test suites sharing one Python interpreter; run each
   test file as CI does (a separate `python -m unittest` per file), not
   merged into one run.
8. **After fixing one write-site, grep for its twin in the same
   function.** A success-only test path leaves the matching error-path
   fields unasserted; the same construction is usually duplicated nearby
   and carries the same bug.
23. **Sequence architectural unifications as pure-reducer vertical slices before facade delegation.**
    Build and unit-test the pure deterministic state machine logic (transitions, voting weights,
    security halts) with zero subprocess mocking before touching process transport, HMAC crypto,
    or legacy facade wrappers.
24. **Render visual observability dashboards as pure, clock-free functions with atomic I/O.**
    Keep report rendering (`render_html_report`) strictly pure, deterministic, and clock-free by injecting an aware `now: datetime`, and delegate disk writes to an atomic tempfile-replace helper (`write_html_report`). All dynamic HTML values must pass through `html.escape`.
25. **Enforce injected-now CLI arguments to preserve clock-free AST test invariants.**
    When exposing CLI entry points on clock-free modules, require an ISO-8601 `--now` argument rather than reading live system clocks in `main()`, preserving reproducible historical replay and passing AST clock guards.


## Subprocess & CLI Process Safety

9. **Always `await proc.wait()` after `proc.kill()`.** A kill without a
   wait leaves a zombie process in the OS table under `asyncio.
   create_subprocess_exec`; pair every kill with a wait, in both the
   timeout and the unexpected-exception paths.
10. **Wrap CLI adapter calls in `except Exception` and degrade to
    abstain.** One reviewer's timeout or I/O error must never crash the
    rest of an `asyncio.gather` panel; catch broadly at the adapter
    boundary and return a safe abstain payload instead.
11. **External CLI workers need explicit stdin and sandbox bypass.**
    `codex`, `claude -p`, and `agy -p` hang on a missing TTY without
    `< /dev/null` (or piped input), and fail `bind: 127.0.0.1:0` under
    macOS IDE sandboxing without `BypassSandbox: true`.
12. **Sanitize externally supplied run IDs before using them in a path.**
    `re.sub(r'[^a-zA-Z0-9_-]', '_', run_id)` before writing any manifest or
    log path derived from a caller-supplied identifier — prevents path
    traversal and directory escapes.

## State & File Locking Hygiene

13. **Never re-open and re-lock a file your own call stack already
    holds locked.** A helper that opens a fresh file descriptor on a path
    the caller already holds an exclusive `fcntl.flock` on deadlocks
    against itself; split into locked entry points and unlocked internal
    helpers.
14. **Distinguish "absent" from "damaged" at every filesystem read.**
    `Path.is_dir()` / `.exists()` swallow `PermissionError` and return
    `False` — using them collapses a real permission fault into "nothing
    here." Use `os.stat` and handle `OSError` explicitly wherever a missing
    path and a damaged path must be told apart.
15. **Re-read state inside the lock before honoring a compare-and-swap
    rejection.** An optimistic CAS that decides "rejected" from a read
    taken before acquiring the lock can reject on stale state; re-check the
    live value inside the critical section before finalizing a rejection.
16. **Bound accumulation stores with a fixed capacity and FIFO
    eviction, and dedupe exact matches before appending.** Journals and
    lesson logs that grow forever eventually blow context budgets or disk;
    cap them (e.g. `DEFAULT_MAX_MEMORY_LESSONS`) and drop case-sensitive
    exact duplicates before writing.

## Multi-Harness Sync & Governance

17. **Adding a Python module means updating four lists in lockstep.**
    `install.sh`'s `MANAGED_FILES`, `uninstall.sh`'s `INSTALLED_FILES`, and
    CI's `PYTHON_MODULES` and `PYTHON_TESTS` in `.github/workflows/test.yml`
    all need the new file, or it silently skips lint, type-check, or
    execution.
18. **Never `git add -A` / `git commit -a` on a shared working tree.**
    Another session's in-flight, unstaged work sits in the same tree;
    enumerate exact paths so an unrelated writer's changes are never swept
    into your commit.
19. **`git push` publishes the whole ancestor chain, not just your own
    commits.** Run `git log origin/main..main` and confirm authorship of
    every commit about to be pushed before pushing on a tree another
    session might also be writing to.
20. **A "done" ticket's acceptance criteria can all pass while nothing
    calls the component it built.** A component can pass every quality
    gate — tests, review, install manifests — and still have zero callers.
    One acceptance criterion must name the actual caller, and one test must
    reach the new code through that caller's path, not the entry point
    directly.
21. **Centralize loose JSON into strongly-typed immutable schemas with per-key fallbacks.**
    When parsing shared configuration files across multiple consumers, replace ad hoc
    dict reads with a single `@dataclass(frozen=True)` module (`routing_config.py`).
    Downstream consumers should read typed models and public structural keys
    (`STRUCTURAL_KEYS`), and section parsers must support per-key fallbacks for partial
    configurations to prevent false-positive validation crashes during progressive migrations.
23. **Bind heterogeneous model reasoning effort dynamically with auto-snap to valid defaults.**
    Models have disparate reasoning effort spaces (e.g. Luna supports only `low`, Sol/Opus support `high`/`ultra`).
    The configuration layer must filter effort options reactively by model and auto-snap invalid selections to
    the model's `default_effort` to eliminate runtime CLI worker invocation failures.
24. **Decompose complex features into single-file vertical slices for local Tier-0 offloading.**
    Partitioning work into atomic, single-file tickets (Tickets 45-53) allows 80%+ of implementation to run
    cost-free on local models (LM Studio / Tier 0), reserving Tier-3 models strictly for council review and quality gates.
25. **Audit live CLI provider model IDs and decouple them from human-readable display labels.**
    Never assume static config names match callable CLI flags; probe runtime providers dynamically (`probe_models.py`
    and LM Studio probe) and map display labels to verified wire CLI identifiers.

