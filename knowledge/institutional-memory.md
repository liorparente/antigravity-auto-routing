# Institutional Memory — 25 Golden Rules

## Metadata
- **Last updated:** 2026-08-26
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
28. **Prefer CSS-only interactivity (checked-radio + sibling selectors) over
    inline `<script>` in static HTML reports.** A tab bar or segmented
    toggle can switch visible sections with radio inputs and `~`/`+`
    sibling CSS rules, keeping a "ships zero script tags" invariant intact
    (`learning_report_html.py`'s two-tab dashboard, ticket 47) until a
    ticket's scope genuinely requires real client-side state (model/effort
    binding, undo, floating action bars) to justify breaking it.

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
26. **`pipx run mypy` needs a symlink workaround for `skills/worker-routing/`.**
    A hyphenated directory name is not a valid Python package name, so mypy
    refuses outright (`... is not a valid Python package name`) rather than
    type-checking anything — CI dodges this by `sed`-rewriting the module
    list to `worker_routing/` first; reproduce locally with
    `ln -sfn worker-routing skills/worker_routing` before invoking
    `pipx run mypy --config-file pyproject.toml skills/worker_routing/<file>.py`.
30. **This repo's HTML-report tests assert substrings, never full-document
    equality.** `test_learning_report_html.py`'s convention is
    `assertIn`/`assertNotIn` against fragments — verify this holds before
    extending a rendered template, since it means new sections/tabs can be
    added without rewriting dozens of pre-existing tests, as long as their
    asserted substrings still appear somewhere in the larger document.
31. **A first-pass zero-findings `/code-review` is a signal the change was
    well-scoped, not that review was skipped.** Ticket 47 (backward-compatible
    optional params, additive markup, substring-safe tests) converged with
    0 Standards/Spec findings on iteration 1, unlike ticket 46's 8 rounds /
    25 findings — treat a fast convergence as confirmation the smaller,
    additive-only shape of a change is worth repeating, not as grounds to
    skip the review step itself.
32. **Fixing one false factual claim in a comment can produce a new false
    claim, not convergence.** Ticket 47's `_ROLE_ACCENT_COLORS` comment
    took four `/iterative-fix-review` rounds: a nonexistent
    `sensitivity_gate` config key → a wrong "unique to this role" claim
    about `local_only` (another role shared it) → an overclaimed "from the
    spec's palette" attribution (the spec only names one of seven hex
    values) → finally clean. Each rewrite was independently plausible and
    independently wrong.
33. **When a reviewer flags one wrong claim inside a multi-sentence
    comment, re-derive the *whole* block from primary sources in one pass
    — not just the flagged clause.** All three wrong claims in rule 32's
    incident lived in the same ~15-line comment; each incremental patch
    left an adjacent, equally-unverified sentence standing, which is
    exactly what the next review round caught.
34. **Brief a re-verification agent with the specific history of prior
    wrong fixes, and instruct it to re-derive every claim from primary
    sources.** A verifier told only "check this fix" tends to confirm it
    looks plausible; a verifier told "the last two fixes for this exact
    spot were both wrong, re-derive independently" is what actually caught
    rounds 2 and 3 of rule 32's incident instead of rubber-stamping them.
35. **Fix a review-flagged "cosmetic"/non-functional inconsistency in the
    same pass, even though it changes no behavior.** Two dict literals
    left listing roles in a stale order after a third was reordered were
    flagged as harmless but got re-flagged in the next review round anyway
    — closing it immediately costs less than the extra review cycle it
    otherwise causes.


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
27. **Re-read the ticket's own backlog file before trusting a memory
    summary of it.** Cross-session project memory can carry a stale or
    outright wrong ticket description (e.g. describing ticket 47 as a
    `GET /api/model-capabilities` endpoint when
    `.scratch/routing-backlog/issues/47-*.md` actually specified two-tab
    navigation and a Bento Grid) — always open the actual `issues/<N>-*.md`
    file at the start of a ticket, even when a memory note seems to already
    know what it says.
29. **`install.sh`'s mirrored copies (`.agents/`, `.codex/`, `.agent/`) are
    gitignored — edit only the canonical `skills/worker-routing/` file, then
    run `./install.sh` to sync.** `git status`/commits never need the
    mirrors touched directly, but other harnesses (Codex, Gemini) only see
    a change after `install.sh` re-copies it; verify with
    `git check-ignore -v` before assuming a mirrored path needs staging.

