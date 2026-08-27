# Worker Routing Fallbacks

## 2026-08-27 — Simplifying a Function's Body Left Two Stale Comments Elsewhere Describing the Old Behavior

- Mission: `/iterative-fix-review` on ticket 50's live JSON drawer (`learning_report_html.py` /
  `test_learning_report_html.py`), across three independent review rounds.
- Issue: a first draft's `toggleConfigDrawer()` read `classList.contains(...)` and branched between
  `add`/`remove`; a review round simplified it to a one-line `classList.toggle("is-open")`. The
  simplification landed correctly in the function itself, but two *other* comments elsewhere in the
  same diff kept describing the old mechanism: a test-file comment above `CONFIG_DRAWER`'s
  declaration explicitly narrating the removed `classList.contains` branch, and (a separate, unrelated
  drift of the same shape) a CSS comment claiming a JSON key's trailing colon renders in the ambient
  ink color, when the regex that decided that boundary actually includes the colon in the key's own
  colored span.
- Detection: two separate fresh review passes — each told to verify claims against the actual code
  rather than trust a prior description, run cold on the committed diff rather than continuing the
  same review thread — each caught one of the two stale comments. Neither would have been caught by
  a round re-reading its own prior conclusions, since neither comment was itself the thing that round
  had just changed.
- Resolution: both comments rewritten to describe what the code does now, each re-verified directly
  against the live function/regex (not just against the reviewer's report) before being marked fixed.
- Lesson: after simplifying or refactoring a function's internal logic, grep the surrounding file(s)
  for comments that reference that function's *old* mechanism by name (a branch it no longer has, a
  boundary a changed regex no longer draws where a comment says it does) — a comment several lines or
  files away from the changed code is exactly the kind of drift a diff review of the changed lines
  alone will not surface, and a review pass scoped to "re-verify what was already flagged" will not
  catch a stale comment nobody flagged yet either. See `AGENTS.md`'s "Convergence-loop comment-drift
  discipline" for the durable rule this became.

## 2026-08-27 — A JS Regex Embedded in a Non-Raw Python String Silently Corrupts `\b`

- Mission: add a hand-rolled JSON syntax highlighter to `learning_report_html.py`'s embedded
  dashboard JavaScript (ticket 50), including a token regex using `\b`/`\d`/`\s` word-boundary and
  character-class escapes.
- Issue: `_SCRIPT` (the Python string constant carrying the dashboard's client-side JS) was a plain
  `"""..."""` literal, not raw. Python's own string parser treats `\b` as a *valid* escape sequence —
  an actual backspace byte (0x08) — not the two characters backslash-then-b a JS regex needs. `\s`
  and `\d` are merely *invalid* escapes (Python keeps them as literal backslash+letter today, only
  emitting a `SyntaxWarning`), but `\b` silently substitutes a control byte with no error and no
  warning at all.
- Detection: neither `ast.parse` nor `python3 -m py_compile` flag this — both succeed on a Python
  string that secretly contains a stray backspace byte in place of an intended `\b`. It only surfaced
  because this file's own test convention runs the embedded JavaScript under `node` against a DOM
  stub rather than ever asserting on its source text (`test_learning_report_html.py`'s
  `_run_embedded_script`) — a corrupted regex source there is a genuine JS `SyntaxError`, not a
  false-green Python test.
- Resolution: converted `_SCRIPT`'s declaration to `_SCRIPT = r"""..."""`. Checked safety first:
  `re.finditer(r'\\.', script)` over the ~13,000-character string confirmed nothing else in it relied
  on Python's own escape processing (no `\n`, `\t`, `\"` elsewhere) before converting — a string with
  other genuine backslash-escape content would need every one of those re-examined too, since raw
  mode stops interpreting *all* backslashes in the string, not just the newly-added ones.
- Lesson: before writing a regex-like literal (`\b`, `\s`, `\d`, `\w`, etc.) into a non-raw Python
  string, either declare that string raw or double every backslash by hand — then actually execute
  the embedded code (not just parse the Python file) to confirm the regex still behaves as intended.
  `ast.parse` succeeding is not evidence that a regex embedded inside a string literal survived
  intact.

## 2026-08-27 — Third Recurrence of Institutional Memory / Golden Rules Drift, and Why the Second Fix Didn't Hold

- Mission: Diagnose why `test_institutional_memory_matches_golden_rules` was red at HEAD of
  `spec-0013-role-and-model-matrix-dashboard` (`12054a0`), independent of the ticket 48 work in
  progress on the same branch.
- Failure: `AssertionError: 35 != 25` — `knowledge/institutional-memory.md` parses to 35 numbered
  rules (31 committed at `12054a0`, 4 more added by a concurrent session) against `GOLDEN_RULES`'s 25.
  This is the *third* time this exact assertion has failed this way: `72f5abf` (20→23), `3815eea`
  (23→25 in the doc only, fixed by `b03d510`), `d4934ac` (25→28 in the doc only, fixed by `932e748`).
  The 2026-08-24 entry below recorded the second occurrence and stated the lesson in the imperative
  ("must never append freeform text ... without code-level catalog updates") — the third occurrence
  happened anyway, one session later.
- Root Cause: the lesson was recorded as a rule for a human/agent to remember, not as something the
  writing process is structurally incapable of violating. `/learn-session` (a skill living outside
  this repository, at `~/.claude/skills/learn-session/SKILL.md`, and not distributed by `install.sh`)
  instructs its own step 3 to "prepend to `institutional-memory.md`" and contains zero mentions of
  `GOLDEN_RULES` or the sync contract. Every prior fix patched the *symptom* (re-added the missing
  catalog entries, or deleted the extra doc entries) without touching the *process* that keeps
  re-creating the symptom. A recorded lesson that lives beside the code but not inside the tool that
  does the writing is not a control — it is a note a future run has no way to consult.
- Resolution (this session): did not re-patch the symptom a third time. Instead: (1) traced full
  history to confirm the pattern (`git log -p` + per-commit rule/id diffing across d1ff4af, 72f5abf,
  3815eea, d4934ac, 932e748, 12054a0); (2) confirmed via grep that no production code path reads
  `institutional-memory.md` — the sync test is its only reader — and that `learned-state/` has never
  existed in this repo, so `get_scoped_memory` always falls back to the in-code catalog; (3) filed
  spec 0014 (`docs/specs/0014-generated-institutional-memory-and-single-source-rule-catalog.md`,
  tickets 54–59 = GitHub issues #30–#35) making the doc a generated build output of the catalog, so
  the drift becomes structurally unexpressible rather than merely re-detectable; (4) added an explicit
  moratorium guard to `/learn-session`'s own Step 3 (the tool that keeps causing this) pointing at
  spec 0014, so this session's fix does not evaporate the moment someone runs `/learn-session` again
  before ticket 59 lands. The test itself is still red as of this entry — ticket 54 (#30) narrows it,
  and it has not yet been implemented.
- Lesson: when the same invariant violation recurs after being fixed twice, stop fixing the data and
  fix the process that produces it. A lesson recorded only as prose next to the broken invariant (an
  `ERRORS.md` entry, a code comment, a memory note) is not a guard — it has no way to intercept the
  next write. The actual fix is either (a) removing the second hand-maintained copy entirely (this
  case: generate the doc from the catalog), or (b) teaching the specific tool that writes the second
  copy about the contract it is breaking (this case: the `/learn-session` moratorium guard), ideally
  both. See spec 0014 and the [[green-assertion-unexercised-path]] memory note for the related pattern
  of a passing assertion that never exercised the path it names.

## 2026-08-26 — Fixing a False Comment Produced Two More False Comments in a Row

- Mission: re-run `/iterative-fix-review` on ticket 47's committed diff
  (`learning_report_html.py`'s `_ROLE_ACCENT_COLORS`/`_ROLE_DISPLAY_ORDER`
  region) to adversarially re-verify the two findings a prior review round
  had already flagged and fixed.
- Issue: it took four rounds, not one, because each fix introduced a *new*
  unverified factual claim instead of converging:
  1. **Round 1 (baseline):** found a comment claiming `routing-config.json`
     has a `sensitivity_gate` key justifying why `reviewer_security`/
     `sensitive_executor` render red. No such key exists.
  2. **Round 2:** the rewrite instead claimed `sensitive_executor`'s
     `capability_requirements.local_only=True` was unique to it "besides
     `sensitive_doer`'s provider entry." Both halves were wrong:
     `sensitive_doer` is an unrelated legacy top-level key with no
     `capability_requirements` field at all, and the role that actually
     shares `local_only=True` with `sensitive_executor` is `adjudicator`
     (rendered amber, not red) — never mentioned.
  3. **Round 3:** the rewrite correctly named `adjudicator` and dropped the
     `sensitive_doer` reference, but a broader adjacent claim in the same
     comment block — "one hex per role, from the spec's Ethos Analytics
     palette (Implementation Decisions §2)" — turned out itself
     unverified: the spec's §2 only ever names one color, `#2563eb`; the
     other six hex values in `_ROLE_ACCENT_COLORS` never appear in it.
  4. **Round 4:** rewrote the full comment block from the primary sources
     (`routing-config.json`, the spec file) in one pass instead of patching
     the newly flagged clause alone. An independent adversarial
     verification agent, briefed on the exact history above and told to
     re-derive every claim rather than trust the fix, found nothing wrong.
- Detection: each round used a fresh sub-agent given the finding from the
  previous round *and* instructed to re-derive every checkable claim from
  `routing-config.json`/the spec directly, rather than a sub-agent that
  simply confirmed the stated fix looked plausible — that is what caught
  rounds 2 and 3 instead of rubber-stamping them.
- Resolution: the final comment (`learning_report_html.py` lines ~689-716)
  states plainly which single value is spec-cited and which are this
  module's own extension, states the real shared `local_only` fact
  (`adjudicator` too) and explicitly concludes `local_only` cannot be the
  actual coloring rule, and no longer references any nonexistent config
  key. Also reordered `_ROLE_DISPLAY_NAMES`/`_ROLE_ACCENT_COLORS`'s dict
  literals to match `_ROLE_DISPLAY_ORDER`'s corrected order — a stale,
  non-functional inconsistency a prior round had flagged but the fix
  hadn't addressed, left for round 4 to also close in the same pass.
- Lesson: a plausible-sounding rewrite of a wrong comment is not
  self-evidently correct just because it fixes the specific clause a
  reviewer named — the surrounding sentences in the same paragraph share
  the same unverified-provenance risk and are just as likely to be wrong.
  Re-derive the *whole* claim-bearing block from primary sources in one
  pass, and brief the verifying agent with the specific history of prior
  wrong fixes so it treats the new version with the same skepticism as the
  old one, not less.

## 2026-08-26 — `pipx run mypy` Refuses to Run on `skills/worker-routing/` Directly

- Mission: type-check `learning_report_html.py` and its test file after
  adding ticket 47's role-matrix rendering, per this repo's own memory note
  to always re-run `ruff`/`mypy` via `pipx run` rather than trust a worker's
  claimed-green report.
- Issue: `pipx run mypy learning_report_html.py` (run from inside
  `skills/worker-routing/`) and `pipx run mypy skills/worker-routing/learning_report_html.py`
  (run from the repo root) both fail immediately with `worker-routing
  contains __init__.py but is not a valid Python package name` — mypy
  refuses to treat a hyphenated directory as an importable package, so it
  never even reaches type-checking.
- Detection: the error is not a type error at all, just an import-name
  rejection; naively reading it as "0 files checked, nothing wrong" would
  have produced a false-clean gate for the entire module.
- Resolution: `.github/workflows/test.yml` already works around this by
  `sed`-renaming every path in its module list from `skills/worker-routing/`
  to `worker_routing/` before invoking mypy (`TARGETS=$(echo "$PYTHON_MODULES"
  | sed 's|skills/worker-routing/|worker_routing/|g')`). Reproduced locally
  with a symlink instead: `cd skills && ln -sfn worker-routing worker_routing`,
  then `pipx run mypy --config-file pyproject.toml skills/worker_routing/<file>.py`
  from the repo root, then `rm skills/worker_routing` afterward — the
  symlink is gitignored-equivalent scratch, never a tracked path.
- Lesson: a bare `pipx run mypy <file>` on this repo is not a reliable
  gate by itself when the file lives under `skills/worker-routing/` — it
  either needs the symlink workaround or must go through CI's own
  `sed`-rewritten invocation to actually type-check anything.

## 2026-08-26 — A `<script` Substring Inside a CSS Comment Still Trips the "No Script Tag" Invariant Test

- Mission: add a CSS-only (radio-input + sibling-selector) tab bar to
  `learning_report_html.py` for ticket 47, explicitly to avoid tripping the
  existing `test_rendered_report_never_leaks_an_unescaped_script_tag` test,
  which asserts `assertNotIn("<script", report)` against the full rendered
  document.
- Issue: a new CSS comment inside the embedded `<style>` block explained the
  design as "CSS-only... No `<script>` anywhere in this document" — and
  that comment's own literal text contains the substring `<script`, so the
  full-document `assertNotIn` check failed even though no real `<script>`
  tag was ever emitted.
- Detection: `python3 -m unittest test_learning_report_html` immediately
  failed on that one pre-existing test with the offending substring visible
  in the assertion's printed document dump — not a new test written for
  this change, an old one catching an unrelated new hunk.
- Resolution: reworded the comment to "This document ships no script tags
  at all" — same meaning, no literal `<` immediately followed by `script`.
- Lesson: `assertNotIn("<script", ...)`-style invariant tests do a raw
  substring search with no HTML-context awareness; prose describing
  generated HTML (docstrings, code comments compiled into the output, even
  comments meant only for a human reading the CSS) must avoid the literal
  trigger substring, not just avoid emitting real markup.

## 2026-08-26 — `claude --effort ultra` Silently Downgrades Instead of Erroring

- Mission: audit CLI provider effort-flag error handling for `docs/research/live-model-catalog-audit.md` (Ticket 45, finding F1).
- Issue: the audit doc and `probe_models.py` claimed an unsupported `--effort` value on the `claude` CLI path is "a CLI error at dispatch time, not a slower run" — mirroring the genuine behavior of `agy`, which does hard-error (`invalid --effort %q`).
- Root Cause: the claim was written without extracting the actual `claude` binary's argument parser. The installed binary's `--effort` `argParser` (`wsi()`) writes a warning to stderr and returns `undefined` on an unrecognized value such as `ultra` — no throw, no `process.exit`. The session then runs once at the model's `default_effort`.
- Detection: caught in round 4 of Ticket 45's fix-review loop by an agent instructed to re-derive the claim from the installed binary rather than trust the existing text; proven by extracting the `argParser`/`wsi`/`vop`/`Top` functions via `strings -a` on the binary.
- Resolution: corrected all three sites (`live-model-catalog-audit.md`, `probe_models.py:11`, `probe_models.py:206`) to state the real mechanism: warn-and-run-at-default, not error.
- Lesson: a role configured at `ultra` (which `CLAUDE.md` invites and `learning_journal.VALID_EFFORTS` blesses) does not fail loudly on the `claude` path — it silently dispatches at a different effort than configured. Any claim about a CLI's argument-validation behavior must be checked against that binary's own parser, never assumed from a sibling provider's behavior. See [[SilentEffortDowngrade]] in `CONTEXT.md`.

## 2026-08-26 — Written Baseline Claimed Green While `mypy` Was Red for 9 Hours

- Mission: resume Ticket 45's iterative fix-review loop from a handoff document specifying a verification baseline (ruff clean, mypy clean, 1531 tests).
- Issue: the actual HEAD (`03884a9`) had advanced two commits past the handoff's stated commit. The newest commit's provider-ownership work passed a bare `str` (`provider.adapter`) into a `dict[ProviderId, ...]` lookup, leaving `mypy` red with 2 errors for 9 hours before anyone re-ran the gate.
- Root Cause: the handoff's verification *commands* were correct, but its stated *expected results* were a point-in-time snapshot invalidated by a later commit. The session's own first `git log`/`git status` calls also read a stale directory view before its working directory had switched to the target repo, compounding the risk.
- Detection: re-running the exact verification commands from the handoff before touching any code, rather than trusting its stated baseline.
- Resolution: fixed the type by narrowing the free-form adapter string with a real `in PROVIDER_IDS` membership check — no `# type: ignore`, no unverified `cast`.
- Lesson: a written baseline is a claim about the state at the time it was written, not a fact about now. Always re-run the verification gate yourself before accepting any handoff's "current state is green" — including handoffs and institutional memory written earlier in the same project.

## 2026-08-26 — Correcting a False Factual Claim Introduced a Second False Claim

- Mission: fix two Spec-axis findings in Ticket 45's audit doc — both factual claims about installed CLI behavior that live tooling contradicted.
- Issue: happened twice in one session. (1) Rewriting `claude-3-7-sonnet`'s `evidence` string to admit its context window was inferred, not read, left the document's own opening claim ("everything below was read off the installed toolchain") standing uncontradicted, and therefore self-contradictory. (2) Rewriting the `--effort` failure-mode text introduced "reruns at the model's default effort" — but nothing reruns; the value is discarded during argv parsing, before any turn starts, and the session simply runs once.
- Root Cause: verification checked that the old error was gone (the cheap half) but not that the replacement text was itself accurate (the expensive half, requiring re-deriving from ground truth).
- Detection: the next review round was instructed explicitly to treat each rewritten sentence as a brand-new claim requiring its own proof, not to "confirm the fix" — this surfaced both defects immediately.
- Resolution: qualified the document's blanket provenance claim with the one known exception; changed "reruns" to "runs" at both code sites.
- Lesson: when a review finding is "this statement is false" and a fix rewrites the statement, review the *rewrite* as a new claim on its own terms. "The old error is gone" is never sufficient proof that the new text is correct.

## 2026-08-26 — Test Fixture Named the Discrimination It Was Meant to Prove, But Didn't Perform It

- Mission: verify a Spec-axis finding that `main()` actually passes a live provider snapshot into `audit_config_drift` (Ticket 45, round 4).
- Issue: the existing fixture `_clean_config`'s in-code comment explicitly named the discrimination it needed to make ("this fixture must use its published model rather than the stale audited Gemini 3.1 Pro entry"), then picked "Gemini 3.7 Flash (Low)" — a model published by *both* the live listing and the audited catalog. Dropping `snapshot=snapshot` at the call site left every `--audit` test, and the full 1551-test suite, green.
- Root Cause: a comment asserting intent was trusted as evidence the fixture achieved that intent; nobody checked whether the chosen fixture value was actually present in both branches of the condition it claimed to distinguish.
- Detection: mutation testing — dropping the disputed argument and re-running the named tests, per the loop's mandatory-proof rule.
- Resolution: added a fixture naming a model that is audited but *not* live (`gemini-3.1-pro-high`), which genuinely disappears from the active catalog only when the snapshot wires correctly, plus a dedicated test asserting that.
- Lesson: a comment describing what a fixture is *for* is not evidence it does that. When a fixture must discriminate between two code paths, verify its value satisfies only one of them — do not take the author's stated intent, even your own, at face value. Same shape as the repo's "green assertion over an unexercised path" defect class, one level up (in the fixture, not the assertion).

## 2026-08-26 — Closed-Looking Local Ticket Had Zero Commits on the Remote the Tracked Issue Points To

- Mission: close GitHub issue #21 (Ticket 45) after its local backlog file was marked `Status: done` and all fixes were committed locally.
- Issue: the local backlog file said `done` and all five commits existed on disk, but `git log origin/main..HEAD` showed all five were local-only — the `ticket-45-hardening` branch had never been pushed, and `origin/main` was still at the pre-feature commit. Closing the GitHub issue at that point would have marked the public tracker complete with zero corresponding code on the server.
- Root Cause: local "done" status (backlog markdown, commit history) was treated as sufficient to close a tracked issue, without checking whether the branch existed on the remote the issue lives in.
- Detection: `git fetch` + `git log origin/main..HEAD` + `git ls-remote --heads origin <branch>` checked before running `gh issue close`.
- Resolution: pushed the branch, confirmed remote HEAD matched local HEAD, then closed the issue with a comment naming the branch explicitly as not yet merged to `main`.
- Lesson: before closing any tracked issue, verify the code is not just committed but present on the remote the tracker points to, and state in the closing comment whether it has been merged to the default branch or only pushed to a feature branch.

## 2026-08-25 — TLS Certificate Verification & Network Failure for `gh` CLI in Standard Sandbox

- Mission: Synchronize local backlog tickets and query GitHub issues via `gh issue list`.
- Failure: Command failed with `Post "https://api.github.com/graphql": tls: failed to verify certificate: x509: “api.github.com” certificate is not trusted` and sandbox socket isolation.
- Root Cause: Standard sandbox mode (`BypassSandbox: false`) enforces strict local network isolation and blocks TLS certificate validation to external APIs.
- Resolution: Executed `gh` CLI commands (`gh issue list`, `gh issue close`, `gh issue create`) with `BypassSandbox: true`.
- Lesson: Remote CLI tools (`gh`, `git push`, remote registry fetches) require network access and must be executed with sandbox bypass enabled, while purely local file inspection and build tools remain sandboxed.


## 2026-08-24 — Dual-Store Invariant Violation by Appending Freeform Session Learnings to `institutional-memory.md`

- Mission: Run CI checks after documentation sync.
- Failure: `test_learned_state.py` failed with `AssertionError: 28 != 25` in `test_institutional_memory_matches_golden_rules`.
- Root Cause: In commit `d4934ac`, three session takeaways were appended to `knowledge/institutional-memory.md` with duplicate IDs (`23`, `24`, `25`), breaking the strict 25-rule 1:1 sync invariant with `prompt_assembler.GOLDEN_RULES`.
- Resolution: Removed the duplicate trailing entries from `knowledge/institutional-memory.md` to restore exact 25-rule parity with `prompt_assembler.GOLDEN_RULES`.
- Lesson: `knowledge/institutional-memory.md` and `prompt_assembler.GOLDEN_RULES` form a strictly synchronized dual-store; session learnings must route to `CONTEXT.md` (domain glossary) or `ERRORS.md` (incident learnings), and must never append freeform text to the fixed Golden Rules catalog without code-level catalog updates.


## 2026-08-24 — Speculative Model Names Drift in Static Configuration vs Live CLI Identifiers

- Mission: Inspect model roster for Role Matrix configuration dashboard (Ticket 45 / PRD 0013).
- Failure: Static `routing-config.json` contained future/speculative model names (`Claude Sonnet 5`, `Codex 5.6 Sol`, `Claude Fable 5`, `Gemini 3.6 Flash`) which do not match wire model flags accepted by live CLI providers (`claude -p`, `codex exec`, `agy -p`).
- Root Cause: Model roster was authored as an idealized specification without dynamic reachability/parameter validation against installed CLI binaries.
- Resolution: Created Ticket 53 as a prerequisite audit to decouple display labels from wire CLI identifiers, map exact reasoning effort parameters per provider, and build `probe_models.py` / LM Studio runtime probing.
- Lesson: Configuration schemas must never conflate user-facing display names with wire CLI model IDs; always gate model rosters with runtime capability probes and provider-specific parameter schemas.


## 2026-08-24 — `protocol.md` Size Budget Exceeded by Link Documentation Updates

- Mission: Update repository documentation, SKILL.md, and protocol.md to v3.6.0 Quality-First Standard.
- Failure: `test_routing.py` failed with `AssertionError: 5121 not less than 5120 : protocol.md is 5121 bytes, exceeding the 5KB budget` in `test_protocol_md_size_is_under_5kb`.
- Root Cause: Adding verbose markdown links to line 58 of `protocol.md` pushed total file size to 5,121 bytes (1 byte over the 5,120 byte hard limit enforced by the test).
- Resolution: Shortened link and reference text in line 58 of `skills/worker-routing/protocol.md` while keeping permanent targets (`docs/specs/0003-critical-dialogue.md` and `REFERENCE.md`), bringing file size safely under 5KB.
- Lesson: `skills/worker-routing/protocol.md` is strictly size-budgeted to prevent token bloat across agent rule files; always verify file byte size (`len(bytes) < 5120`) when editing protocol text.

## 2026-08-24 — Test Spy Argument Type Incompatibility in Mypy Strict Mode

- Mission: Run CI checks on PR #91.
- Failure: `worker_routing/test_debate_orchestrator.py:512: error: Argument 1 has incompatible type "str"; expected "Literal['ambiguity', 'plan-review', 'code-review', 'post-mortem']" [arg-type]`.
- Root Cause: Mock `spy` parameter was typed with primitive `occasion: str` instead of the domain's literal type alias `dialogue_contracts.Occasion` when delegating to `_resolve_topology(occasion: Occasion, ...)`.
- Resolution: Updated `spy` signature in `test_debate_orchestrator.py` to `def spy(occasion: dialogue_contracts.Occasion, complexity: str) -> debate_orchestrator.ConsultationTopology:`.
- Lesson: When mocking or spying on typed domain reducers, annotate mock parameters with the domain's explicit literal union types rather than primitive `str` to satisfy static type checkers.

## 2026-08-24 — Dual-Store Catalog Count Drift between Institutional Memory and Prompt Assembler

- Mission: Run full test suite validation after Ticket 44 documentation sync.
- Failure: `test_learned_state.py` failed with `AssertionError: 25 != 23` in `test_institutional_memory_matches_golden_rules`.
- Root Cause: Rules 24 & 25 were added to `knowledge/institutional-memory.md` without updating `GOLDEN_RULES` in `prompt_assembler.py` and rule-count assertions in `test_prompt_assembler.py` / `test_learned_state.py`.
- Resolution: Added `GoldenRule` entries 24 and 25 to `prompt_assembler.py` and updated catalog length assertions from 23 to 25.
- Lesson: `knowledge/institutional-memory.md` and `prompt_assembler.GOLDEN_RULES` form a strictly synchronized dual-store; updating human-facing Markdown rules requires updating `GOLDEN_RULES` tuples and test invariants in lockstep.

## 2026-08-23 — CLI Worker Piped-Stdin Deadlock and Swift Fallback to Codex Exec

- Mission: Dispatch Ticket 44 implementation task to `claude -p` worker via background process runner.
- Failure: Task `task-93` hung with 0 bytes of log output for 20+ minutes, blocking pipeline progress.
- Root Cause: On macOS in certain background subshell contexts, `claude -p --no-session-persistence` can deadlock waiting on tty/stdin if permissions or input streaming pipes block silently.
- Resolution: Killed the stuck background task immediately and routed the mission brief to `codex exec --model gpt-5.6-terra -s workspace-write "< /dev/null"`, which completed in 77 seconds with 100% test pass.
- Lesson: When a background CLI worker process emits 0 log bytes past a short grace period (e.g. 60 seconds), do not wait blindly; cancel immediately and activate the alternative worker CLI in the fallback matrix (`codex exec`).

## 2026-08-23 — Multi-Harness `install.sh` and Git Operations Blocked by macOS Sandbox (`Operation not permitted`)

- Mission: Synchronize multi-harness skills via `install.sh` and commit ticket 43 completion to git.
- Failure: `./install.sh .` failed with `cp: /Users/liorparente/.gemini/config/skills/worker-routing/SKILL.md...: Operation not permitted`, and `git commit` failed with `fatal: Unable to create '.git/index.lock': Operation not permitted`.
- Root Cause: Standard sandbox isolation (`BypassSandbox: false`) prevents writes outside the workspace root (e.g. `~/.gemini/config/skills/`) and restricts locking `.git/index.lock` in the macOS sandbox environment.
- Resolution: Re-ran `./install.sh .` and `git commit` with `BypassSandbox: true`.
- Lesson: All multi-harness synchronization tools writing to user dotfiles and repository git write operations must be executed with sandbox bypass enabled.

## 2026-08-23 — Mypy Incompatible Tuple Unions on Dataclass State Fields

- Mission: Implement Ticket 43.1 pure state machine and quorum reducer transitions.
- Failure: CI type checking failed with `Argument 3 to "RoundTurnResult" has incompatible type "tuple[PerspectiveReviewResult, PerspectiveReviewResult]"; expected "tuple[CriticResponse, ...]"` in `test_debate_state_machine.py`.
- Root Cause: Dataclasses `RoundTurnResult` and `DebateState` narrowly typed `critic_responses` as `tuple[CriticResponse, ...]` rather than using the polymorphic union alias `tuple[VoteInput, ...]`. In Mypy, tuple types are invariant; a tuple of a concrete type cannot be passed where a different member of a union is expected.
- Resolution: Widened dataclass sequence annotations (`RoundTurnResult.critic_responses` and `DebateState.critic_responses`) and helper signatures (`evaluate_quorum`) to accept `tuple[VoteInput, ...]`, using polymorphic `_field()` access.
- Lesson: When state containers and reducers are designed to support multiple input shapes via a union type (`VoteInput`), dataclass fields and helper arguments must type sequence containers directly with the polymorphic union rather than a concrete subclass.

## 2026-08-23 — Ruff SIM117 Linter Error on Sequential Test Context Managers

- Mission: Validate default routing configuration failure paths in `test_routing_config.py`.
- Failure: GitHub Actions CI failed during `ruff check` on `SIM117 Use a single 'with' statement with multiple contexts instead of nested 'with' statements` at lines 398 and 405.
- Root Cause: Test methods nested `with mock.patch.object(...):` directly enclosing `with self.assertRaises(...):` without intermediate statements.
- Resolution: Combined the nested contexts into single parenthesized multi-context statements: `with (mock.patch.object(...), self.assertRaises(...)):`.
- Lesson: In Python 3.9+, always use parenthesized multi-context managers when wrapping test assertions with both mock patches and exception checks to prevent SIM117 linter violations.

## 2026-08-23 — Strict Whole-File Validation Crashing Partial Legacy Config Readers

- Mission: Unify disparate JSON config readers into centralized `routing_config.py` (Ticket 42).
- Failure: Refactoring section-specific loaders (`_load_dialogue_budget_config`, `_load_degraded_roster_model`, `_load_acceptance_gate_config`) to call `load_routing_config()` caused valid partial configs (e.g. `{"dialogue_budget": {}}` or `{"light_doer": {"patterns": [...]}}`) to raise fatal `ConfigValidationError` exceptions due to missing subkeys, breaking existing fallback contracts.
- Root Cause: Top-level section parsers treated presence of a section dictionary as an all-or-nothing requirement for all keys, rather than falling back per-key/per-field to immutable default constants.
- Resolution: Updated `_parse_dialogue_budget`, `_parse_acceptance_gate`, and `_parse_legacy_role_config` to check key presence individually and substitute defaults for omitted fields (`DEFAULT_ROUTING_CONFIG`).
- Lesson: When replacing loose dictionary lookups with typed schema validators, always support granular per-key fallback defaults for optional and partial dictionaries to ensure zero breaking changes during progressive migrations.

## 2026-08-23 — Zsh Markdown Backtick Substitution in Double-Quoted Worker Prompts

- Mission: Dispatch worker task via `claude -p` with mission brief passed as an inline shell string.
- Failure: Inline string contained markdown backticks and parentheses inside double quotes (`"[WORKER-MODE: NESTED-EXEC] ... \`test_production_invoker.py\` ..."`), causing zsh to interpret backticks as command substitutions and parentheses as subshell syntax, resulting in `zsh: command not found: test_every_declared_role_builds_a_valid_command` and exit code 1.
- Root Cause: In zsh, double quotes allow parameter expansion, command substitution (`\`` and `$()`), and arithmetic expansion. Unescaped backticks in markdown prompt strings are evaluated as shell commands before the string reaches the CLI binary.
- Resolution: Saved the mission prompt to a dedicated scratch file and dispatched the worker by redirecting stdin (`< prompt.txt < /dev/null`).
- Lesson: Never pass multi-line prompts containing markdown backticks or code identifiers directly as double-quoted inline arguments in zsh; always use file/stdin redirection or single-quoted strings.

## 2026-08-22 — macOS Quarantine & File Permissions Block LM Studio Startup (`EPERM test.txt`)

- Mission: Launch LM Studio and start local OpenAI-compatible inference server.
- Failure: LM Studio GUI failed to start, popping modal `Failed to Start LM Studio. It appears that LM Studio does not have sufficient permissions to run. (Tried to write to /Users/liorparente/.lmstudio/test.txt). Raw Error: EPERM: operation not permitted, open '/Users/liorparente/.lmstudio/test.txt'`.
- Root Cause: On macOS (Sonoma/Sequoia), downloading/updating LM Studio via Chrome marks `/Applications/LM Studio.app` with `com.apple.quarantine`. Gatekeeper isolation and translocation restrict the app's write access to user dotfiles (`~/.lmstudio`), causing EPERM on startup test file write.
- Resolution: Ran `chmod -R 755 ~/.lmstudio` and `xattr -cr ~/.lmstudio` (and `xattr -cr "/Applications/LM Studio.app"`). Verified `~/.lmstudio/test.txt` write test succeeded and reopened LM Studio. The local server started cleanly on `http://127.0.0.1:1234` with model `qwen3.8-27b-mlx` loaded in `READY` status.
- Lesson: When Electron/native macOS apps fail with EPERM on startup file-write checks in home directories, inspect extended attributes (`com.apple.quarantine`, `com.apple.provenance`) and reset directory write permissions.

## 2026-08-21 — Non-Interactive `agy` Background Task TTY / IPC Socket Lock

- Mission: Dispatch worker task via `agy -p` (Antigravity CLI) as a background task from within the IDE.
- Failure: Executing `agy` as a non-interactive background subagent / subprocess without a connected interactive TTY caused the process to hang indefinitely waiting for terminal IO / IPC socket attachment.
- Resolution: Killed the stuck background task, avoided nested `agy` invocations in non-interactive subshells, and routed execution directly via specific worker CLIs (`codex exec`, `claude -p`) or local REST endpoints (`LM Studio`).
- Lesson: In automated orchestration environments lacking an interactive TTY, never invoke tools expecting terminal attachment; use decoupled non-interactive headless CLI tools or HTTP APIs.

## 2026-08-20 — Word-Boundary False Positives in Prose Security Indicator Scanner

- Mission: Implement prose security veto for unstructured critic responses in `production_invoker.py` (Spec 0009).
- Failure: Simple substring matching (`"rce" in text.lower()`, `"cwe" in text.lower()`) caused false-positive security halts on benign technical prose containing words like `"source"`, `"resource"`, or `"authentic"`.
- Resolution: Replaced plain substring matching with compiled regexes using word boundaries (`re.compile(r"\b(rce|remote\s+code\s+execution)\b", re.IGNORECASE)`), while explicitly isolating unnegated findings from negated reassurance phrases.
- Lesson: Any automated security veto scanning unstructured LLM review text must enforce regex word boundaries (`\b`) on short indicators to prevent catastrophic false-positive halts on ordinary English words.

## 2026-08-20 — Cascading Dictionary Sanitization KeyError on Non-Idempotent `.pop()`

- Mission: Validate and normalize mutually dependent configuration keys (`min_weight`, `max_weight`) in `consultation_policy.py`.
- Failure: When both `min_weight` and `max_weight` were invalid or out of range, the validator popped `max_weight` in the first check and then attempted `config.pop("max_weight")` in a subsequent fallback branch, raising an unhandled `KeyError`.
- Resolution: Standardized on safe dictionary popping (`config.pop("max_weight", None)`) across all policy sanitizers.
- Lesson: In validation and sanitization pipelines that mutate dictionary copies, never call `.pop(key)` unconditionally; always use `.pop(key, None)` to ensure idempotent cleanup.

## 2026-08-20 — Path Traversal Vulnerability in Generated Manifest Filenames

- Mission: Implement signed manifest persistence in `debate_orchestrator.py` (`write_council_manifest`).
- Failure: `write_council_manifest` interpolated user-supplied `run_id` directly into the manifest path (`root_dir / ".ralph" / f"council_manifest_{run_id}.json"`), allowing malicious or malformed `run_id` strings containing `../` to write manifests outside `.ralph/`.
- Resolution: Sanitized `run_id` with `re.sub(r'[^a-zA-Z0-9_-]', '_', run_id)` before constructing the target path.
- Lesson: All file persistence helpers accepting external identifiers must sanitize path components against directory traversal before joining with directory paths.

## 2026-08-19 — Module-Identity Split with Monkeypatching in Path-Loaded Sibling Modules

- Mission: Verify and test isolated worker transport and recurring failure notifier in `debate_transport.py` and `debate_orchestrator.py` (Spec 0008).
- Failure: `DebateTransport` cached `_production_invoker = _load_sibling("production_invoker")` at module import time. Tests attempting to mock `production_invoker.invoke_worker` by patching `sys.modules["production_invoker"]` failed to intercept calls made by `DebateTransport`, because the sibling loader held a separate module instance. The test bypassed the mock and executed real subprocess worker commands, causing the test runner to hang.
- Resolution: Updated `DebateTransport` to resolve `production_invoker` dynamically from `sys.modules` on every invocation (`sys.modules.get("production_invoker", _production_invoker)`), ensuring test monkeypatching is respected across all path-loaded harnesses.
- Lesson: In repos using path-based sibling loaders (`importlib.util.spec_from_file_location`), never assume module identity is identical across distinct file loads. Resolve mutable runtime dependencies dynamically from `sys.modules` or inject them explicitly via constructors.

## 2026-08-19 — Target-Directory Globbing in Uninstaller Scripts Risks Deleting Unrelated Files

- Mission: Universal module discovery and clean uninstallation in `install.sh` and `uninstall.sh` (Ticket 40).
- Failure: `uninstall.sh` dynamically globbed `*.py` files directly inside `target_dir` to remove installed modules. Because target convention directories like `.agents/skills` or `.codex/skills` are shared across multiple tools, globbing target directories indiscriminately deleted user files and third-party skills.
- Resolution: Refactored `uninstall.sh` to dynamically discover production modules from the *source* repository directory (`$SRC_DIR`), appending them to `INSTALLED_FILES` and surgically deleting only known repository files.
- Lesson: Installers and uninstallers targeting shared multi-tool convention directories must always discover managed files from source directories, never by wildcard globbing the target installation folder.

## 2026-08-18 — Static Analysis (Ruff AST & Mypy) Failures on Dynamic Facade Modules in CI

- Mission: Resolve GitHub Actions CI failure on commit `4b6a850` (`Test / unit-tests`).
- Failure: CI pipeline running `ruff check $PYTHON_MODULES` and `mypy $PYTHON_MODULES` failed with multiple static analysis errors:
  1. `ruff F822` / `RUF022`: Exported symbols in `__all__` resolved dynamically via `__getattr__` were flagged as undefined by Ruff's static AST analyzer, and `__all__` was not sorted alphabetically.
  2. `mypy` type errors: Reassigning dynamically loaded module attributes to uppercase variables (e.g. `Occasion = _dialogue_contracts.Occasion`) was treated by mypy as invalid runtime variable assignments rather than valid type aliases when used in annotations like `occasion: Occasion`.
  3. `debate_orchestrator.py` duplicate definition of `_detect_sensitivity_marker` (line 67 and line 1179).
  4. Leaf module unsorted imports (`I001`) and blind exception in manual smoke test (`BLE001`).
- Resolution:
  1. Added `# noqa: F822` to `__all__ = (` in `advisory_consultation.py` and sorted all exported symbol names alphabetically (`RUF022`).
  2. Wrapped dynamic module type aliases in `if not TYPE_CHECKING:` in `debate_orchestrator.py`, while importing exact types inside `if TYPE_CHECKING:` from sibling leaves.
  3. Removed duplicate `_detect_sensitivity_marker` definition in `debate_orchestrator.py`.
  4. Added `# noqa: BLE001` to `test_lmstudio.py` and sorted leaf imports with `ruff check --fix`.
- Lesson: Dynamic facades delegating via `__getattr__` require explicit static typing annotations under `if TYPE_CHECKING:` and `# noqa: F822` on `__all__` to satisfy static linters (Ruff/Mypy) without sacrificing dynamic backwards-compatibility.

## 2026-08-17 — Subprocess Mock State Leak Across In-Process Unittest Invocations

- Mission: Run full regression and unit test suite across decomposed advisory consultation and production invoker.
- Failure: Running `python3 -m unittest skills/worker-routing/test_routing.py skills/worker-routing/test_production_invoker.py` in a single Python invocation caused module-level mock process handlers registered in `test_production_invoker.py` to contaminate unmocked subprocess tests in `test_routing.py`, generating false positive test failures.
- Resolution: Executed each test file in its own isolated Python interpreter invocation (matching CI's `for test_file in $PYTHON_TESTS; do python3 "$test_file"; done` loop).
- Lesson: Never run disparate test suites sharing process-level patches or global mock dispatchers in the same in-memory Python unittest runner invocation; run them as isolated child processes.

## 2026-08-17 — Council Review Provider Adapter Gather Crash on Narrow Exception Catch

- Mission: Resolve ruff BLE001 blind exception lint error in `provider_adapters.py`.
- Failure: Replacing `except Exception:` with `except ValueError:` in `CLIReviewerAdapter.review()` caused non-ValueError exceptions (such as `TimeoutError`, `OSError`, or subprocess communications errors) to escape unhandled, crashing entire `asyncio.gather` parallel review panels instead of returning the safe `{"vote": "abstain"}` fallback.
- Resolution: Restored catching all runtime exceptions with `except Exception as error: # noqa: BLE001`.
- Lesson: For resilience in parallel async worker panels, adapter review calls must safely catch all exceptions at the boundary and translate them to structured fallback results (`abstain`).

## 2026-08-17 — CI Ruff isort (`I001`) Test Import Formatting Breakdown

- Mission: Add unit test suite `test_dialogue_transcript.py` for Spec 0006 decomposition.
- Failure: GitHub Actions CI runner installing the latest Ruff release failed on `ruff check $PYTHON_MODULES` with `I001 [*] Import block is un-sorted or un-formatted` due to an extra newline separating `import advisory_consultation` from sibling module imports.
- Resolution: Organized imports into contiguous alphabetical blocks and verified locally using `ruff check --select I $PYTHON_MODULES`.
- Lesson: Whenever introducing new test files, verify import sorting with `ruff check --select I` to prevent CI runner version mismatches from failing automated builds.

## 2026-08-17 — Stale Candidate Rejection in Anti-Flapping Optimistic CAS Retry Loop

- Mission: Implement atomic, bounded memory lesson accumulation with anti-flapping (`reject_if_candidate_digest`) in `risk_tiered_application.py` (Ticket 33 / ADR 0010).
- Failure: In an optimistic Compare-And-Swap loop, comparing the candidate digest against `reject_if_candidate_digest` and immediately returning `status="rejected"` caused a stale rejection bug: if a concurrent writer updated the `memory` document after this iteration's read, the rejection evaluated against obsolete state rather than retrying against the fresh state.
- Resolution: Added a CAS linearization point recheck before returning rejection: `learned_state.read_current(root_dir).get("memory") == existing`. If the state mutated, the loop branches to `continue` (retry) rather than returning stale rejection.
- Lesson: In optimistic retry loops with semantic rejection rules, always verify current state equality (linearization point) under or immediately prior to returning rejection to prevent stale rejections in concurrent write environments.

## 2026-08-15 — POSIX Advisory Lock Reentrancy Deadlock in File Operations

- Mission: Implement pending proposal store for Tier 3 worker briefs under `.ralph/pending_proposals.jsonl`.
- Failure: A public function (`approve_pending_proposal` / `submit_brief_proposal`) acquired an exclusive file lock (`fcntl.flock(stream.fileno(), fcntl.LOCK_EX)`) via a context manager and then called `read_pending_proposals`, which attempted to open the same lock file and acquire another `LOCK_EX` on a new file descriptor, resulting in a self-deadlock that hung the test runner indefinitely.
- Resolution: Split the logic into an internal unlocked helper (`_read_pending_proposals_unlocked`) called when the lock is already held, and a public locked function (`read_pending_proposals`).
- Lesson: POSIX `fcntl.flock` is not reentrant across separate file descriptors in the same process. Always separate unlocked internal helpers from public locked entry points.

## 2026-08-15 — Triple Manifest Closure Invariant for Skill Python Modules

- Mission: Add new production module `risk_tiered_application.py` and test suite `test_risk_tiered_application.py`.
- Failure: Regression tests in `test_routing.py` (`ManagedFileClosureTests`, `LearningJournalTests`) failed because new skill modules must be declared in three separate manifests: `.github/workflows/test.yml` (`PYTHON_MODULES`/`PYTHON_TESTS`), `install.sh` (`MANAGED_FILES`), and `uninstall.sh` (`INSTALLED_FILES`).
- Resolution: Updated all three files in tandem when adding the new modules.
- Lesson: Skill Python files have a triple-manifest invariant; always synchronize `test.yml`, `install.sh`, and `uninstall.sh` whenever introducing a new `.py` file to `skills/worker-routing/`.

## 2026-08-15 — Voting Quota Paradox (Banzhaf Power Collapse in Multi-Agent Ensembles)

- Mission: Design weighted multi-agent jury system for Council Review with Claude Opus (45%), Codex Sol (45%), and Gemini Pro (10%).
- Failure: Game-theoretic mathematical analysis proved that under simple binary majority ($q=0.50$), $(0.45, 0.45, 0.10)$ yields a Banzhaf Power Index of $(1/3, 1/3, 1/3)$, giving Gemini (10%) equal voting power to Claude and Codex. Under supermajority ($q \ge 0.60$), Gemini becomes a 0% power Dummy Player.
- Resolution: Adopted baseline weights of $(0.40, 0.40, 0.20)$ combined with continuous Soft-Confidence Scoring ($s_i \in [-1.0, +1.0]$) and asymmetric loss multiplier ($1.5\times$ on negative scores).
- Lesson: Never use naive binary majority thresholds for asymmetric model weightings. Always verify Banzhaf power distributions or use continuous soft-confidence aggregation.

## 2026-08-15 — Subprocess Zombie Process Leak in Asyncio Process Calls

- Mission: Implement live CLI adapters for Council Review providers with timeout deadlines.
- Failure: In `provider_adapters.py`, calling `proc.kill()` upon `asyncio.TimeoutError` failed to reap child process exit status, leaving zombie processes in macOS process table.
- Resolution: Appended mandatory `await proc.wait()` immediately following `proc.kill()`.
- Lesson: In Python `asyncio.subprocess`, `proc.kill()` sends `SIGKILL` but does not reap the process table entry; always pair `proc.kill()` with `await proc.wait()`.

## 2026-08-15 — LM Studio MLX Thinking Mode Token Budget Starvation

- Mission: Generate full-page landing page code locally with `lmstudio-community/Qwen3.8-27B-MLX-6bit`.
- Failure: Model reasoning chains in thinking mode consumed thousands of tokens, exhausting small `max_tokens` limits (3,000–6,000) before emitting `<!DOCTYPE html>`.
- Resolution: Required `max_tokens >= 16384` for code generation with thinking enabled, or setting `reasoning_effort="low"` / instructing direct code output.
- Lesson: For reasoning-enabled local models in agent pipelines, allocate ample token budget for internal chain-of-thought overhead.

## 2026-07-25 — `agy` deep-research worker unavailable

- Mission: ultra-high-effort review of the external `implementation_plan.md`.
- Failure: `agy` 1.1.7 could not create its logs or bind `127.0.0.1:0` in the managed sandbox (`operation not permitted`).
- Fallback: use the protocol-approved read-only Codex 5.6 Sol path for repository research, followed by a separate ultra-effort Codex 5.6 Sol critic pass.
- Scope impact: `agy` exited before reading the plan or repository; no research result was lost or partially trusted.

## 2026-07-25 — Codex CLI fallback unavailable

- Mission: read-only repository research for the same plan review.
- Failure: `codex-cli` 0.144.1 could not initialize its in-process app-server client in the managed sandbox (`operation not permitted`).
- Fallback: delegate independent read-only research and review passes to built-in worker agents, then synthesize only their evidence-backed findings.
- Scope impact: the CLI exited before producing a review; no partial output was trusted.

## 2026-07-25 — CLI research workers unavailable for calibration hardening

- Mission: deep research for HMAC calibration verification, unsafe-chain metrics suppression, and the multi-pass council debate.
- Failure: `agy` could neither create its runtime logs nor bind `127.0.0.1:0`; the protocol-approved Codex 5.6 Sol fallback could not initialize its in-process app-server client. Both failed with `operation not permitted`.
- Fallback: use a built-in read-only research worker, followed by delegated implementation and independent QA.
- Scope impact: both CLIs exited before reading or modifying repository files; no partial output was trusted.

## 2026-07-25 — Claude implementation worker unavailable

- Mission: implement the reviewed calibration HMAC, metrics suppression, council debate, and unit-test changes.
- Failure: Claude Sonnet 4.6 could not reach its API endpoint (`ENOTFOUND`) and exited without yielding implementation output.
- Fallback: use a built-in implementation worker with the same three-file scope, followed by an independent QA worker.
- Scope impact: the Claude CLI produced no file changes or partial result.

## 2026-07-25 — CLI research fallback unavailable for lint/type repair

- Mission: inspect the three worker-routing Python files before Ruff and mypy repair.
- Failure: `agy` failed before inspection due sandbox log/bind permissions; the Codex CLI fallback then failed before inspection due in-process app-server permissions.
- Fallback: use a delegated built-in research and execution worker.

## 2026-07-25 — Definitive Resolution for CLI Worker Socket Permission Errors

- Issue: CLI workers (`codex exec`, `claude -p`, `agy -p`) failed with `Operation not permitted (os error 1) - failed to initialize in-process app-server client` when invoked inside `run_command` in standard IDE sandbox mode (`BypassSandbox: false`).
- Root Cause: IDE sandbox process isolation blocks local loopback socket binding (`127.0.0.1:0`) and IPC pipes required by in-process app-servers. macOS "Full Disk Access" (TCC) settings have no effect on IDE subprocess sandbox rules.
- Permanent Resolution: Mandated Rule 4.7 in `protocol.md` requiring `BypassSandbox: true` on `run_command` for all external CLI worker invocations. Synchronized across `AGENTS.md`, `CLAUDE.md`, `~/.gemini/GEMINI.md`, and project skill rules via `./install.sh`.

## 2026-07-27 — CLI research fallback for BypassSandbox cleanup

- Mission: research the code-review loose ends in the BypassSandbox documentation and synchronization tests.
- Failure: `agy` could not write its runtime logs or bind `127.0.0.1:0` in the managed sandbox.
- Fallback: used independent built-in read-only workers to inspect the canonical sources and tests.
- Scope impact: `agy` exited before repository analysis; no partial output was trusted.

## 2026-07-27 — CLI fallbacks for RoutingAuditEngine plan critique

- Mission: deep research and final Critic review of `.scratch/plan_draft.md`.
- Failures: `agy` 1.1.7 failed before repository access because the managed sandbox denied `~/.gemini` log/crash writes and localhost `127.0.0.1:0` binding; the Codex 5.6 Sol CLI then failed before repository access because its in-process app-server could not initialize (`Operation not permitted`).
- Fallback: used three built-in read-only research workers; after two bounded documentation workers stalled without writing, materialized the Markdown artifacts under the protocol's documentation-only exception and sent them to a returning worker for independent read-only QA.
## 2026-07-27 — Deprecated Claude 4.6 Model Retirements & V5 Standardization

- Mission: Update retired Claude 4.6 model identifiers (`claude-sonnet-4.6`, `claude-opus-4.6`) to active models (`claude-sonnet-5`, `claude-opus-5`).
- Issue: Anthropic retired `claude-sonnet-4.6` and `claude-opus-4.6` CLI endpoints on June 15, 2026, causing external CLI calls using those parameters to fail.
- Resolution: Standardized `protocol.md`, `routing-config.json`, `SKILL.md`, and `test_routing.py` on active v5 models (`claude-sonnet-5`, `claude-opus-5`). Enhanced `routing_check.py` with numeric version matching (`re.search(r'\b\d+(?:\.\d+)?\b', declared_worker)`) to strictly detect version drift against declared routing headers.
- Verification: Ran `./install.sh` to update system-wide targets and verified all 76 unit tests pass (`OK`).

## 2026-08-04 — Claude CLI Positional Argument & Context Leak

- Issue: The Claude CLI (`claude -p -c ... "Prompt" < /dev/null`) ignored the positional prompt argument due to the flag chain and `/dev/null` redirection.
- Consequence: Treating the prompt as empty, the CLI defaulted to loading its stateful project history (e.g., from a `.claude` directory) and answered based on an old conversation context (e.g., Phase 3 Auth) instead of the intended prompt.
- Resolution: Pipe the prompt strictly through `stdin` using `echo "..." | claude -p -`. This forces the CLI to read the exact input and prevents it from falling back to cached stateful history.
- Correction (2026-08-10): the diagnosis above was wrong, and its prescribed remedy was never applied to any command template. `< /dev/null` was not the cause — `-c` was. In the Claude CLI, `-c` is `--continue`, a boolean flag that resumes the most recent conversation in the current directory; it is not a config-override flag as it is in `codex`. The "flag chain" therefore did not swallow the prompt, it explicitly asked the CLI to reload prior conversation state, which is exactly the reported symptom. Root cause fixed on 2026-08-10 by replacing `-c model_reasoning_effort="high"` with `--effort high` across all templates. The stdin-pipe remedy is superseded: the project standard remains the `< /dev/null` guard of Rule 4.6, and `skills/worker-routing/REFERENCE.md` was brought into compliance with it on the same date (six examples were missing the guard entirely).

## 2026-08-06 — Worker Sandbox Blocks Git Operations

- Issue: Delegating basic version control operations (`git branch`, `git checkout`) to CLI workers (`codex exec`, `claude -p`) failed because the worker sandbox locks the `.git/` directory (`Operation not permitted`).
- Consequence: Orchestrator workflows that require branching or reverting were blocked because the routing protocol strictly forbade the Orchestrator from running these commands directly.
- Resolution: This entry originally claimed `skills/worker-routing/protocol.md` had been updated to add version control to the "Allowed Direct Actions" list. **That update was never actually applied** — `protocol.md` continued to permit only read-only diagnostics (`git status`, `git log`, `curl` health checks), so the deadlock this entry describes persisted for over a month. The fix was actually applied on 2026-08-10, with a narrower command list than originally claimed: `git add`, `git commit`, `git branch`, `git checkout`, `git revert`, `git stash`, `git tag` are direct-allowed; `git push`, `git reset --hard`, `git clean -fd`, and any `--force` variant remain explicitly forbidden without user approval (see ADR 0006). Lesson: a Resolution line in this file is a claim, not a guarantee — verify the target file actually changed before trusting an entry's account of its own fix.

## 2026-08-06 — Background Worker Collision & Assumed Codebase State

- Issue: The orchestrator launched a nested worker (`codex exec`) in the background to implement Phase 2, but incorrectly assumed the worker failed due to a misread log tail, and simultaneously misjudged the codebase state by assuming a "Phase 0 Restoration" commit had reverted the source files when it had only reverted documentation.
- Consequence: The orchestrator almost duplicated the worker's effort, experiencing cognitive dissonance when viewing files that were being actively mutated by the background worker, and when discovering Phase 3-5 implementations that were never actually reverted.
- Resolution: Always use the `manage_task` tool with `status` to ensure background workers have fully terminated before inspecting their output. Additionally, never assume a codebase was cleanly reverted based on a commit message alone without verifying `git diff` or `git log --stat` on the source files.

## 2026-08-07 — Claude Code Session State Leak in Background Workers

- Issue: The CLI worker (`claude -p`) was continuing conversations from the last active session in the workspace instead of starting a fresh, isolated context for each new task. This resulted in workers acting on unrelated context (e.g., from old tasks) and creating cognitive dissonance.
- Consequence: Worker tasks in new sessions incorrectly referenced plans or context from prior, completed tasks in the same project directory, causing hallucinations and incorrect implementations.
- Resolution: Added the `--no-session-persistence` flag to all `claude -p` invocations in the worker routing protocol (`protocol.md`, `SKILL.md`, `REFERENCE.md`). This flag prevents Claude Code from saving or resuming disk-based session history, guaranteeing a stateless, clean slate for every worker invocation.

## 2026-08-07 — Brittle Test Assertions on Protocol Commands

- Issue: After updating `protocol.md` to include `--no-session-persistence`, the CI tests failed because `test_routing.py` contained hardcoded strings expecting the exact previous command structure.
- Consequence: The `unit-tests` GitHub Action failed, blocking the pipeline despite the logic being correct.
- Resolution: Updated all hardcoded `claude -p` assertion strings in `test_routing.py` to match the new protocol command exactly. Moving forward, any change to CLI command shapes in the documentation or protocol must be accompanied by synchronous updates to the test suite's expected string assertions.

## 2026-08-10 — Protocol Self-Reference: Workers Self-Blocking on AGENTS.md

- Issue: `codex exec` workers routed inside this repo returned `[ROUTING: BLOCKED]` without touching code, three consecutive times. Codex CLI auto-loads the repo-root `AGENTS.md` as project instructions, and `install.sh` injects the full orchestrator protocol there. The worker therefore received "you are a pure orchestrator, self-execution is a protocol violation" as its highest-priority instruction and correctly obeyed it.
- Root Cause: The only exemption was `IN_WORKER_ROUTING=true`, an environment variable. A model cannot read environment variables — they are not in its context — and verifying one requires running a shell command, which is the very action behind the gate. Circular. Independently, Codex strips non-core variables from the sandboxed shell under its default `shell_environment_policy.inherit = "core"`, so the variable would have read empty even if checked. An unobservable exemption always resolves to "not exempt".
- Consequence: Every worker routed to Codex in an installed repo halted. The documented fallback chain masked the defect as a per-vendor outage (logged against Codex Terra) rather than a protocol design fault, which is why it recurred instead of being diagnosed. Not a vendor failure: nothing was unreachable, and the worker did exactly what the document told it to do.
- Resolution: Protocol v3.5 adds a `## 🚦 READ THIS FIRST — Worker Mode Override` section at the very top of `protocol.md`, ahead of the orchestrator identity statement. The exemption now keys on the token `[WORKER-MODE: AGY-NESTED-EXEC]` carried *inside the worker's prompt* — an observable value — instead of an invisible environment variable. All worker command templates in `protocol.md`, `SKILL.md`, and `REFERENCE.md` now embed the token in their prompt argument (`codex review` is flagless, so it takes the token as its positional `[PROMPT]`). The token is emitted only by the orchestrator's own command templates and must never be self-issued, which preserves the gate for a primary-session agent (ADR 0005, Pillar 1) while exempting genuine nested workers.
- Rejected Alternative: Stripping the protocol block from `AGENTS.md` would have fixed the symptom without touching Antigravity (its gate lives in `~/.gemini/GEMINI.md`), but would have silently removed gating for Codex CLI and Claude Code as primary session agents — a regression against ADR 0005, Pillar 1.
- Verification: `expected_commands` in `test_routing.py` synchronized in the same change (per the 2026-08-07 brittle-assertion lesson); 82 tests pass. End-to-end: a Terra worker carrying the token executed its mission instead of blocking, and a worker without the token still blocks.

## 2026-08-10 — Invalid CLI Flags Copied Across Worker Templates

- Issue: Two invalid-flag defects had shipped into every documented `codex review` and `claude -p` command template. (1) `codex review --uncommitted -s workspace-write ...` — `codex review` (unlike `codex exec`) has no `-s`/`--sandbox` flag at all (confirmed against `codex review --help`, codex-cli 0.144.1); the flag was silently ignored or rejected depending on parser strictness. (2) `claude -p ... -c model_reasoning_effort="high" ...` — `claude`'s `-c` is short for `--continue` (resume the most recent conversation), not a config override; the real reasoning-effort flag is `--effort <low|medium|high|xhigh|max>` (confirmed against `claude --help`).
- Consequence: The `codex review` QA command carried a dead flag with no effect on sandboxing. The `claude -p` command was worse: passing `-c` silently set `--continue` on every worker invocation, re-introducing the exact stateful-session-leak risk that `--no-session-persistence` (2026-08-07 entry above) was added to eliminate — a worker could resume a prior conversation's context via `-c` even with `--no-session-persistence` present, because `-c` and `--no-session-persistence` are independent flags with no interaction check. `--allow-dangerously-skip-permissions` alone was also insufficient to actually bypass permissions: per `claude --help`, it only *enables* the bypass option without activating it — the activating flag is `--permission-mode bypassPermissions`.
- Root Cause: Codex CLI's `-c key=value` config-override syntax was copied onto the `claude` and `codex review` command templates without checking each subcommand's own `--help` output. `-c` happens to be a valid short flag on both CLIs but means something completely different on each — a coincidental syntax collision that went unnoticed because both commands appeared to run.
- Resolution: Replaced `-s workspace-write` on `codex review` with `-c sandbox_mode="workspace-write"` (empirically verified honored: the resolved sandbox mode reflected the override, and an invalid value was rejected by the config-value enum). Replaced `-c model_reasoning_effort="high"` with `--effort high` on all `claude -p` commands, and added `--permission-mode bypassPermissions` alongside `--allow-dangerously-skip-permissions`. Updated all four documented locations (`protocol.md`, `SKILL.md`, `REFERENCE.md`, `test_routing.py` `expected_commands`) plus the two additional `claude -p` examples in `REFERENCE.md` that shared the permission-mode defect. Rule 4 in `protocol.md` now states explicitly that `codex exec` selects sandbox mode via `-s`/`--sandbox` while `codex review` has no such flag and uses `-c sandbox_mode=` instead, to prevent this from reading as a blanket "-s" rule again.
- Verification: `./install.sh` re-rendered `AGENTS.md`, `CLAUDE.md`, and `.claude/rules/worker-routing.md` from the corrected `protocol.md`; `.venv/bin/python skills/worker-routing/test_routing.py` reports OK. Lesson: a flag's presence in one CLI's `--help` output is not evidence it exists on a *different* CLI's subcommand — verify per-subcommand, not per-vendor.

## 2026-08-10 — Contradictory Sandbox Guidance: `TMPDIR`/`GIT_OPTIONAL_LOCKS` vs Protocol Rule 4.7

- Issue: `knowledge/institutional-memory.md` claimed setting `TMPDIR=/tmp` and `GIT_OPTIONAL_LOCKS=0` fully resolved worker socket initialization errors (`Operation not permitted (os error 1)`), contradicting `protocol.md` Rule 4.7 which states `BypassSandbox: true` is strictly required on `run_command`.
- Empirical Test Command: `TMPDIR=/tmp GIT_OPTIONAL_LOCKS=0 IN_WORKER_ROUTING=true codex exec --model gpt-5.6-luna -c model_reasoning_effort="low" -s workspace-write "[WORKER-MODE: AGY-NESTED-EXEC] echo test" < /dev/null`
- Empirical Test Result: Failed immediately with `Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)`. Re-running the exact same invocation with `BypassSandbox: true` succeeded (`exit code 0`). Environment variables do not resolve macOS IDE process socket isolation.
- Resolution: Updated `knowledge/institutional-memory.md` to remove the false claim and explicitly defer to `protocol.md` Rule 4.7 (`BypassSandbox: true`).
- Systemic Pattern: Third instance of documented resolutions found not matching empirical reality. Always require empirical test evidence before writing workarounds into institutional memory.

## 2026-08-10 — `codex review` Positional Prompt Argument Syntax

- Issue: Invocations of `codex review --uncommitted "Prompt"` failed with `error: the argument '--uncommitted' cannot be used with '[PROMPT]'`.
- Cause: Codex CLI positional `[PROMPT]` argument is mutually exclusive with `--uncommitted` flag in `codex review`.
- Resolution: Omit `--uncommitted` when providing a explicit positional prompt string (e.g. `codex review -c sandbox_mode="workspace-write" -c model="gpt-5.6-sol" -c model_reasoning_effort="high" "[WORKER-MODE: AGY-NESTED-EXEC] ..." < /dev/null`).

## 2026-08-10 — Protocol `install.sh` Requires `BypassSandbox: true` for Home Directory Writes

- Issue: Running `install.sh` inside standard IDE sandbox (`BypassSandbox: false`) failed with `cp: ~/.gemini/config/... Operation not permitted`.
- Cause: `install.sh` synchronizes protocol files to global home directory targets (`~/.gemini/config/skills/worker-routing/`, `~/.codex/skills/worker-routing/`, `~/.gemini/GEMINI.md`) outside the workspace.
- Resolution: Always invoke `install.sh` via `run_command` with `BypassSandbox: true` to permit global home directory target synchronization.

## 2026-08-10 — Post-Review Maintenance Backlog Execution (Tickets 08 & 09)

- Mission: Settle ADR 0002 debt (Ticket 08) and triage unreferenced helpers (Ticket 09) via worker CLI routing.
- Key Learnings:
  1. Standard Sandbox Worktrees: `git worktree add` to directories outside the workspace (`../auto-routing-backlog`) is blocked by IDE sandbox boundaries; creating worktrees inside workspace subdirectories (`.worktrees/backlog`) works cleanly inside standard sandbox mode.
  2. Pure Frozen Dataclass Pattern: `SecurityContext` in `@dataclass(frozen=True)` avoids `__post_init__` `object.__setattr__` mutation by resolving secrets in factory methods (`SecurityContext.create()`), ensuring pure immutability and simple assignment error testing.
  3. Telemetry and Fail-Closed Routing: `AgentCouncil.route_task` combines sensitivity classification (`evaluate_sensitivity`), endpoint probing (`check_local_model_endpoint`), and telemetry logging (`log_routing_telemetry`), failing closed via `record_local_model_failure`.
- Verification: 100 tests pass in `skills/worker-routing/test_routing.py` (`OK`).

## 2026-08-10 — CI Mypy Type Checking Resolution on Dynamic JSON Dictionary Lookups

- Mission: Resolve GitHub Actions CI workflow failure on commit `61d41c1`.
- Root Cause: `_valid_debate_rounds` parameter annotations were typed as `list[dict[str, Any]]` and `int`. Callers passing `manifest.get("debate_rounds")` and `manifest.get("consensus_round")` (which return `Any | None`) triggered `mypy` error `Argument 1 to "_valid_debate_rounds" has incompatible type "Any | None"; expected "list[dict[str, Any]]"`.
- Resolution: Typing dynamic dictionary validation helpers to accept `Any` parameter types allows `manifest.get(...)` calls to pass without `mypy` type friction while internal `isinstance()` runtime guards ensure strict validation.



## 2026-08-11 — Ticket 07 Production Worker Invoker, Code Review & Deployment

- Mission: Implement Ticket 07 (`Production Worker Invoker`), execute Two-Axis Code Review (Standards + Spec) via Codex 5.6 Sol, fix code review findings, and deploy/synchronize via `install.sh`.
- Key Learnings & Failure Patterns:
  1. **`uninstall.sh` File List Synchronization**: Adding new managed files (`advisory_consultation.py`, `production_invoker.py`) to `install.sh`'s `MANAGED_FILES` array without updating `uninstall.sh`'s `rm -f` file list breaks uninstallation tests (`test_uninstall_sh_removes_generated_docs`). `rmdir` fails to delete target directories because non-deleted files remain. *Rule: Always update `uninstall.sh` file cleanup list whenever modifying `install.sh` `MANAGED_FILES`.*
  2. **Strict Worker Token Prefix Guard (`startswith`)**: Using substring match (`WORKER_MODE_TOKEN in prompt`) allows prompts discussing the token mid-prompt to bypass prepending, causing workers to self-block at the routing gate. *Rule: Always check `prompt.startswith(WORKER_MODE_TOKEN)`.*
  3. **Display Model Name Normalization (`MODEL_ALIASES`)**: High-level orchestrators use display labels (`"Claude Opus 5 (Thinking)"`, `"Codex 5.6 Sol"`, `"Gemini 3.6 Flash (High)"`), whereas CLI workers require strict model identifiers (`claude-opus-5`, `gpt-5.6-sol`, `gemini-3.6-flash`). *Rule: Use explicit `MODEL_ALIASES` normalization dictionary that fails closed (`ValueError`) on unmapped names.*
- Verification: 10/10 invoker tests OK, 127/127 routing tests OK (`test_routing.py`), `ruff` and `mypy` 0 errors. Committed `ae76189`.



## 2026-08-11 — Spec Status vs Git Commit History Drift

- Mission: Verify open tasks and spec status against repository state.
- Root Cause: `docs/specs/0001-advisory-consultation.md` and `docs/specs/0002-post-review-maintenance-backlog.md` retained `Status: Ready for agent` header after their underlying tickets had been implemented and committed to Git. This caused false-positive reports of open tasks.
- Resolution: When implementing specs, update the status header in `docs/specs/` to `Status: Implemented` upon completion.
- Correction (same day): the status flip on spec 0001 was premature when made, and its original Verification line was wrong. It claimed commits `dc91a72` through `ae76189` "implemented all tickets" while ticket 06 (transcript and telemetry) was still being written in a parallel session; ticket 06 landed afterwards in `816b3c8`. The claimed count of 137 passing tests was also stale — it was the pre-ticket-06 figure (127 in `test_routing.py` plus 10 in `test_production_invoker.py`). Spec 0001 became genuinely complete only once `816b3c8` landed, at which point `test_routing.py` reports 144.
- Lesson: verifying a spec's status against `git log` alone is not sufficient when another session holds uncommitted work — the working tree of every active session is part of the repository state. Cross-check `git status` and in-flight tickets before declaring a spec implemented. This is a second instance of the pattern already recorded on 2026-08-06: a Verification line in this file is a claim, not a guarantee.

## 2026-08-11 — A Truncated Digest Is Not Redaction (Ticket 06)

- Mission: give every AdvisoryConsultation a telemetry record carrying a task identity, without breaching the module's documented redaction boundary ("nothing derived from `task_description` reaches a reason beyond the matched marker constant").
- Issue: the orchestrator's mission brief specified a truncated SHA-256 digest of the task description as a "stable, non-revealing" default identity, and the implementing worker's docstring then asserted it "must carry no recoverable information". Both were wrong. On the `sensitivity_halt` path the task text is known to contain a credential, and a 64-bit digest over guessable text is a confirmation oracle: anyone who guesses the task can verify the guess against the logged identity.
- Why it survived implementation: the guarding test asserted only that the secret *substring* was absent from the artifacts. A derived value is structurally invisible to a substring assertion, so the test could never have failed on this.
- Detection: the Standards axis of the two-axis review. The Spec axis reviewed the same code in parallel and reported the boundary intact — accurately, because it asked whether the secret *appears*, while Standards asked whether anything *derived* escapes. The disagreement between the axes was the finding.
- Resolution: `_resolve_task_id` now keys on outcome. A caller-supplied `task_id` wins on every path and is the production route; non-halt outcomes keep the digest; `sensitivity_halt` uses `secrets.token_hex(8)`, unrelated to the task text. The transcript and the telemetry record for the same halt carry the same random id, so correlation survives. The guarding test now also asserts the emitted identity is not equal to the digest of the task text.
- Lesson: when a documented rule says "nothing *derived* from X", it means derived — hashing is a transformation, not a redaction. Assert the property, not the absence of one literal string.

## 2026-08-11 — A Worker's Report About Files It Does Not Own Is a Guess

- Issue: a CLI worker resolving code-review findings reported that four documentation files were "untouched by me — their diffs are unchanged from before this session, confirmed by inspecting `git diff --stat`". `knowledge/institutional-memory.md` had in fact grown from 7 to 12 changed lines during that same window, written by a different Claude Code session working on the repository concurrently.
- Consequence: none this time, because the orchestrator diffed the file independently. Taken at face value it would have hidden the existence of the parallel session, which was material to the commit decision.
- Root Cause: the worker was asked to report `git status` and did so honestly for the files it changed, then extended the same confidence to files it never opened. A statement about a file the worker did not write is an inference from a stale snapshot, not an observation.
- Resolution: treat a worker's file-state claims as covering only the files it edited. Verify everything else with `git status` / `git diff` in the orchestrator session, on the same footing as re-running the test and lint gates rather than trusting the reported output. Related: the 2026-08-06 entry above, where assuming a clean revert from a commit message alone nearly caused duplicated work.


## 2026-08-11 — LM Studio Resource Safety Guardrail Failure on Large Default Context Lengths

- Issue: Attempting to on-demand load MLX models in LM Studio (such as `qwen3-coder-next-mlx`) via OpenAI-compatible API (`/v1/chat/completions`) failed with HTTP 400 (`Failed to load model... Error: Model loading was stopped due to insufficient system resources.`).
- Root Cause: By default, `qwen3-coder-next-mlx` declares a `max_context_length` of 262,144 tokens (256K). On JIT auto-load, LM Studio attempts to allocate KV cache memory for the full default context size, triggering LM Studio's pre-load resource guardrails even when sufficient RAM exists for smaller context windows.
- Resolution: Restricted `Context Length` in LM Studio's load parameters/presets to `8,192` or `16,384` tokens (via GUI `+ Load Model` or `Model Defaults`), reducing RAM allocation requirements by >70% and allowing the model to load and serve API requests cleanly.

## 2026-08-12 — Two Writers, One Tree

- Mission: land spec 0003's remaining tickets and reconcile with spec 0004.
- Issue: two separate Claude Code sessions wrote to `advisory_consultation.py`/`test_routing.py` on the same `main` working tree at the same time, on 2026-08-12. Neither session was aware of the other until the resulting confusion forced a coordination handoff.
- Consequence: a session had to spend real effort re-establishing ground truth (whose edits were whose, what was actually committed vs. still in flight) before any further work could safely proceed. A stale worktree (`review-snapshot`, detached at `23a138c`) was left behind from that period and was only cleaned up on 2026-08-13, after confirming its one commit was already reachable from the real branch history and nothing would be lost.
- Resolution: single-writer, sequential discipline for the rest of the session — one working tree per active session, never two sessions editing the same branch's tree concurrently. Before deleting an orphaned worktree, always confirm its HEAD commit is already an ancestor of a real branch (`git branch --contains <sha>`) rather than assuming it is safe to discard.
- Lesson: a shared working tree is shared mutable state. Two agents (or two sessions of the same agent) writing to it without coordination is the same class of bug as two threads writing to memory without a lock — the fix is the same too: one writer at a time, enforced structurally, not by convention alone.

## 2026-08-13 — Checked Is Not Run

- Mission: reconcile main into spec/0004-learning-loop; verify the merged CI configuration.
- Issue: `.github/workflows/test.yml` can list a `test_*.py` file in its ruff/mypy module list without that file ever appearing in the step that actually executes tests. This already happened once, silently, to `test_production_invoker.py` (sixteen tests never run despite CI staying green) — spec 0004 later added `test_ci_runs_every_test_file_it_checks` specifically to catch a repeat.
- Root Cause: linting and executing are two independent CI steps reading two independently-maintained lists; nothing forces them to agree except discipline, until a test asserts it.
- Detection: the new guard test itself failed immediately after this merge, because `test_lmstudio.py` (a live-LM-Studio-server smoke suite, checked but deliberately never executed — its own module docstring says so) was newly added to the checked list without being added to the executed list.
- Resolution: narrowed the guard test's assertion with one named, commented exception (`_CHECKED_BUT_NOT_EXECUTED_BY_DESIGN = frozenset({"skills/worker-routing/test_lmstudio.py"})`) rather than loosening it for every file — a second, accidental gap (a new `test_production_invoker.py` incident) is still caught.
- Lesson: "CI checks this file" and "CI runs this file's tests" are different claims. Either assert their equality as a test, with named exceptions for genuinely unrunnable files, or expect the gap to reopen silently the next time a file is added to one list and not the other.

## 2026-08-13 — A Canary Probe Deleted a Real Mission's Plan

- Mission: implement spec 0003 ticket 11 (the sensitive-task path); caught while reviewing the surrounding budget-degradation code this ticket touches.
- Issue: budget rung 3 (full session exhaustion) returns before any worker is contacted, and its early-return branch unconditionally called `_remove_stale_plan_artifact`, deleting `root_dir / "implementation_plan.md"` — even when the triggering run was a seeded-flaw canary probe (`is_canary=True`), not a real mission. A canary neither creates nor deletes that file, by the canary invariant documented elsewhere in the same module; this path silently violated it.
- Root Cause: the early-return path was written to match "what a real budget-exhausted mission should do" (clean up any stale plan) without checking whether the current run was a mission at all.
- Resolution: fixed in `aa118f1` — the cleanup call is now guarded on `not is_canary`, so the preemption itself stays unconditional (a rung-3 canary still returns `budget_skipped` with zero worker calls) but the file-deleting side effect does not.
- Lesson: an early-return / preemption shortcut inherits none of the guarantees its "normal path" sibling implicitly relies on. Every side effect a shortcut performs (file writes, deletions, telemetry) needs its own check against what kind of run is actually in flight — "this path always means a real mission" is an assumption that needs to be stated and verified, not inherited by association.

## 2026-08-13 — A New Seam Doesn't Reach a Downstream Dispatcher on Its Own

- Mission: implement spec 0003 ticket 11, extending the sensitivity gate's local-only roster resolution across all four dialogue occasions, including post-mortem.
- Issue: `run_advisory_consultation_debate` gained a new `reachability_check`/`roster_config_path` seam so a sensitive task could resolve a local-only roster instead of always halting. `dispatch_post_mortem_consultation` — the actual production entry point for the post-mortem occasion — has its own, separate, narrower keyword-only parameter list and did not expose either new parameter. Its own docstring had explicitly, and previously correctly, declared the roster seam "deliberately not exposed: none has a post-mortem consumer today."
- Consequence: a sensitive task dispatched for post-mortem through the real production API could only ever halt — it could never reach the local-only dialogue the ticket required for that occasion, even though the core function fully supported it.
- Detection: not caught by the implementation's own tests, which called the core function directly. Caught by the Spec-axis code review, which specifically read the dispatch entry point rather than assuming a fix to the core function was sufficient.
- Resolution: threaded `reachability_check`/`roster_config_path` through `dispatch_post_mortem_consultation` and its background-thread target, and rewrote the stale "deliberately not exposed" docstring paragraph to state what became true.
- Lesson: in a system with multiple entry points to shared core logic, a new capability added to the core function does not automatically reach a dispatcher, wrapper, or background-thread target with its own parameter surface. Enumerate every entry point explicitly when wiring through a new seam — don't assume propagation.

## 2026-08-13 — Two Independently-Declared Vocabularies Will Drift

- Mission: verify spec 0004's LearningJournal schema against spec 0003's final telemetry shape, as part of the main → spec/0004-learning-loop reconciliation merge.
- Issue: `advisory_consultation.Occasion` (`Literal["ambiguity", "plan-review", "code-review", "post-mortem"]`, shipped, tested) and `learning_journal.DialogueOccasion` (`Literal["ambiguity", "plan_review", "code_review", "post_mortem"]`, schema-only) are meant to describe the same four-value vocabulary. Three of the four values used hyphens on one side and underscores on the other.
- Root Cause: each module's own test suite was internally self-consistent — `learning_journal.py`'s test helper always hand-supplied its own `"occasion"` string in isolation — and nothing ever constructed a `DialogueQualityRecord` from a real `Occasion` value. Python's type system does not enforce agreement between two separately-declared `Literal` aliases; nothing would have raised until a real writer eventually passed a live `Occasion` value through and hit `ValueError` in `_validate_choice`.
- Resolution: aligned `DialogueOccasion` to `Occasion`'s shipped spelling (the established vocabulary, not the schema-only one), and added `test_cross_spec_vocabularies_agree`, asserting `set(get_args(Occasion)) == set(get_args(DialogueOccasion))` (and the same for the topology vocabularies), so a future drift is caught immediately rather than waiting for a real writer to hit it.
- Lesson: when two modules or two specs are meant to share a vocabulary, don't rely on both authors getting the spelling right independently — pin the agreement with an explicit equality test the moment the second vocabulary is declared.

## 2026-08-13 — A Task Brief Describing Future Work As Present Tense

- Mission: the same reconciliation merge — its own non-negotiable checklist item read "learning_journal.py/learning_outcomes.py consume the FINAL AdvisoryTelemetryRecord shape ... and filter canary records before any aggregation."
- Issue: no code in either file reads `.ralph/routing_telemetry.jsonl` or constructs an `AdvisoryTelemetryRecord`/`DialogueQualityRecord` translation at all. `learning_journal.py`'s own `DialogueQualityRecord` docstring says so plainly: "Spec 0003's machinery writes these; this module owns the contract so both specs agree on the shape before either has a writer." The checklist item's present-tense phrasing described a future capability as if it already existed.
- Consequence: none this time — caught before the checklist item was marked done, by reading the actual code path instead of trusting the item's wording.
- Resolution: recorded the schema-compatibility work that genuinely was done (the occasion/topology vocabulary fix above) and explicitly flagged the consumption/filtering requirement as deferred future work, in the merge commit message, rather than checking off something that doesn't exist.
- Lesson: a "verify X does Y" checklist item can describe intent, not current fact. Before marking it done, verify Y's implementation exists at all — read the actual code path — rather than inferring from the item's own phrasing that the described behavior is live.

## 2026-08-13 — A Process List Answers the Wrong Question About Concurrent Writers

- Mission: satisfy the single-writer rule (see "Two Writers, One Tree", 2026-08-12) before merging `spec/0004-learning-loop` into `main` — the rule existed as a principle but had no verification procedure attached.
- Issue: `ps` was wrong in both directions at once. It showed two live `codex` processes whose command lines carried `[WORKER-MODE: AGY-NESTED-EXEC]` and `gpt-5.6-sol` — the exact signature of this repo's routing protocol — which read as "another session is mid-flight here, halt." It simultaneously showed nothing at all for the session that *was* writing to this tree.
- Root Cause: a command line describes what a process was asked to do, not where it does it. Two different repositories on one machine run the same protocol and therefore produce indistinguishable `ps` output. Conversely, a peer Claude Code session is not a separately identifiable process at the `ps` level, so no amount of process inspection can reveal it.
- Detection: `lsof -a -p <PID> -d cwd` resolved both `codex` processes to `/Users/liorparente/Projects/Yamit-Therapy` — an unrelated repo, and their prompt referenced an `Issue #154` that does not exist here. `ListAgents` then revealed the real peer, `auto-routing-e2`, invisible to `ps`. A third, independent signal had already fired without being recognized: `knowledge/institutional-memory.md` moved from unstaged to committed (`30548e3`) *between two of my own read-only calls* — state changing under a reader is direct proof of another writer, stronger than any inventory of processes.
- Resolution: treated the check as requiring three independent instruments, none sufficient alone — `cwd` resolution to discriminate among processes, `ListAgents` to find peer sessions, and observed state change across one's own calls as the ground-truth signal. Only after all three agreed did the merge proceed.
- Lesson: "is anyone else writing to this tree?" is a question about a working directory, not about a process table. Match the instrument to the question — and when a rule is documented as a principle, write down the procedure that verifies it, or each session will improvise a different and weaker one.

## 2026-08-13 — A Peer Agent's Silence Is Not Consent

- Mission: the same pre-merge coordination — having found the peer session `auto-routing-e2`, ask it directly whether it was still writing before taking the writer role.
- Issue: the coordination message was delivered successfully, and no reply ever came. The peer closed before its next tool round, so the message was never processed. A design that blocks on the reply would have waited indefinitely for an answer that could not arrive.
- Root Cause: `SendMessage` to a peer session is a request, not a handshake. Delivery is confirmed; processing is not, and the recipient may terminate between delivery and its next turn. Nothing in the mechanism distinguishes "still thinking" from "gone."
- Resolution: defined in advance what would constitute resolution in the absence of a reply, and used it — the peer's disappearance from `ListAgents`, plus an independent sweep showing a clean tree, no `index.lock`, no new commits since `17:07:00`, and no file modified in fifteen minutes. Approval to proceed rested on that evidence, never on inferred consent.
- Lesson: when coordinating with another agent, decide up front what decides the matter if no answer comes back. Treating silence as either consent or refusal is a guess; treating it as "gather independent evidence instead" is the only option that terminates. Related to the process-list entry above: the fallback was the same set of instruments used to detect the peer in the first place.

## 2026-08-13 — Unfamiliar Identifiers Read As "Another Project" Without a Single Grep

- Mission: survey open work in this repository; noticed `implementation_plan.md` in the repo root while enumerating candidate tasks.
- Claim I made, twice, and committed to this log in `b5c750a`: that the file's content "belongs to an entirely different project", because it discusses `AuditIssue`, `WARN-01`/`WARN-02` precedence, JSON/SARIF formatters, and a `discovery_ordinal` field, "none of which exist in this codebase."
- The claim was false. Every one of those identifiers except SARIF lives in this repository: `AuditIssue` and its `discovery_ordinal` field at `skills/worker-routing/routing_check.py:164-179`, and the `WARN-01`-over-`WARN-02` precedence rule the plan describes at `routing_check.py:940-948`, spelled out in the same words. The file is this repo's own plan from 2026-08-05/06 (`2fc92d5`, reverted in `27058f8`, scope-restored in `7ac1940`), describing work that was then implemented here.
- Root Cause: I inferred provenance from unfamiliarity. The identifiers were absent from the spec 0001-0004 line I had been reading all session, and I let "absent from the part of the codebase I have in context" stand in for "absent from the codebase" — without running the one grep that would have settled it. One corroborating detail (a link to `~/Documents/Projects/auto-routing`, a path that genuinely does not exist) was over-weighted into confirmation; it is a stale checkout path, not evidence of a different project.
- Compounding failure: the entry's own closing Lesson told the reader to grep the codebase for the file's *path* — advice I followed, which is how the load-bearing-path finding below is correct. It did not occur to me to apply the same instrument to the file's *contents*, which is the check that would have caught the error.
- What was correct, and survives: `skills/worker-routing/advisory_consultation.py` resolves `plan_path = root_dir / "implementation_plan.md"` and both writes and deletes exactly that path — via `_remove_stale_plan_artifact`, which exists to clear "a pre-existing `implementation_plan.md` under `root_dir` from an earlier run", and which caused the canary-deletion bug fixed in `aa118f1`. A stale file at that path is a live input to the CriticalDialogue machinery either way. The file was deleted on the user's instruction; it remains recoverable via `git show 7ac1940:implementation_plan.md`.
- Lesson: "I do not recognize this" is not evidence about a codebase, it is evidence about one's own context window. Before attributing a file to another project, grep for its distinctive identifiers — the check costs one command, and a wrong provenance claim propagates into permanent records, as this one did before being caught one step later.

## 2026-08-13 — A Single-Writer Check Expires the Moment It Is Made

- Mission: the same session — after merging spec/0004, distilling lessons, and deleting the stale plan artifact, commit and push the results.
- Issue: I verified single-writer at 17:20 — peer session gone from `ListAgents`, no process with this repo as its `cwd`, clean tree, no commits or file changes — and then treated that verification as covering the rest of the session. It did not. A new session, `auto-routing-bf`, opened at roughly 17:31 and committed `b266d88` at 17:51:57, sixty-nine seconds before my own `11353da` at 17:53:06. Its commit message even describes my worktree deletion, so it was reading the tree I was writing to.
- Second, worse issue: I staged with `git add -A`. In a shared working tree that command does not stage "my changes" — no such concept exists at the git level — it stages the entire working directory, including any in-flight edit another session has made but not yet committed, and attributes it to my commit message.
- Consequence: none. `11353da` contained exactly the three files I had touched. That was luck: the peer had already committed its work seconds earlier, so there was nothing uncommitted for `-A` to sweep. Had its timing differed by a minute in the other direction, I would have committed its half-finished work under my message, and it would have found its tree mysteriously clean.
- Detection: reading the `git log` output of my own push, which showed `b266d88..11353da` — a parent commit I had not made and had never seen. Note the direction: the push output is what surfaced this, not any check I ran deliberately.
- Resolution: two rules. First, re-verify (`git status`, `git log`, `ListAgents`) immediately before each write burst rather than once at session start — a verification is a snapshot, and its validity window closes the instant another session can open. Second, in any tree that might be shared, enumerate paths explicitly (`git add <path> <path>`) and never use `git add -A` or `git commit -a`. Also messaged the peer directly, declaring which two files I was about to write and stating what I would do if no reply arrived.
- Confirmed afterwards by the peer, which replied to the coordination message: it had committed `b266d88` with an explicit pathspec *precisely because* my staged deletion of `implementation_plan.md` was sitting in the index at that moment and it did not want to sweep it into its own commit. The discipline I failed to apply is the one that protected me.
- Aggravating factor discovered in the same exchange: `b266d88` rewrote the `.scratch` rule in `.gitignore` from `.scratch/` to `.scratch/**` plus re-includes, which promoted sixty ticket, map, and spec files from ignored to tracked (verified: `git ls-files .scratch` returns 60). A `git add -A` in this tree is therefore strictly more dangerous today than it was this morning — sixty files that used to be invisible to it are now sweepable.
- Lesson: the single-writer rule recorded on 2026-08-12 was stated as a property of a session. It is actually a property of an instant. Every gap between checking and acting is an unguarded window, so the check belongs immediately before the act — and the staging command should be narrow enough that being wrong about the window is survivable. Note also that a repo's blast radius for `-A` is not fixed: an ignore-rule change can enlarge it without any notice to sessions already running.

## 2026-08-13 — Deleting a Merged Branch Is Safe for Code and Quietly Wrong for Documents

- Mission: post-merge cleanup — remove the `.worktrees/spec-0004` worktree and delete the fully-merged `spec/0004-learning-loop` branch, locally and on `origin`.
- Verification performed, and sufficient for what it covered: the worktree's HEAD (`bc63316`) was confirmed an ancestor of `main`, present on `origin`, with a clean tree and only caches among ignored files; `git branch -d` was used deliberately over `-D` so the merge check would be re-enforced by the tool. No code or history was lost, and none could have been.
- What the verification did not cover: documents that *name* the branch. Ticket 12's status line in `.scratch/self-improving-orchestrator/` pointed at `spec/0004-learning-loop`, and the deletion turned a correct pointer into a stale one. A peer session retargeted it to `main` in `b266d88`.
- Second, unanticipated cost: the deletion was invisible from outside. The peer session had listed both the branch and the worktree minutes earlier; on its next git call both were simply gone, with no explanation obtainable from inside its own session. It verified the merge independently before reporting, so it told its user nothing was lost — but it correctly reported the disappearance as unexplained, which cost it real time.
- Resolution: two additions to the branch-deletion procedure. Before deleting, `grep -rn "<branch-name>"` across docs, specs, and tickets and retarget every hit. And if any peer session is active, announce the deletion — an unexplained disappearance in a shared tree is an incident from the other side, whatever it is from yours.
- Lesson: `git branch --merged` answers "is the code safe?", which is not the same question as "is anything still pointing at this?". A branch name is an identifier that documents copy; deleting the branch does not update the copies, and nothing warns you that copies exist.

## 2026-08-13 — Four Claims Verified, and the Fifth Was the Wrong One

- Mission: a peer session's reply carried five claims. Having internalized this file's 2026-08-11 lesson ("A Worker's Report About Files It Does Not Own Is a Guess"), I verified before accepting — and reported to my user that I had done so.
- What I verified: (1) `.gitignore` now reads `.scratch/**` with re-includes; (2) `git ls-files .scratch` returns exactly 60; (3) `DialogueQualityRecord(` is constructed only in `learning_journal.py` and `test_routing.py`; (4) `dialogue_quality` appears only as a `KIND` ClassVar. All four held.
- What I did not verify, and accepted: that ticket 24 "was filed as spec 0003's to implement and never landed", which carries the implication that spec 0003's `Status: Implemented` header counts as done something that is not. I relayed that to my user as a correction to my own earlier close-out report — an apology for an error I had not made.
- The claim was false, and the peer retracted it itself. `docs/specs/0003-critical-dialogue.md:209` disclaims the work in writing: "The LearningJournal, learner, scoreboard, and weekly report — spec 0004." Ticket 24 was therefore unowned from the day it was filed, not abandoned at close-out. Spec 0003 shipped tickets 01-11 completely; its header is accurate. Separately verified: `VerdictContractResult` (`advisory_consultation.py:1597`) already carries `verified_quote_count` and `objection_count`, `AdvisoryRoundVerdict` (:1636) carries one per Critic, and the docstring at :1644-1647 states outright that this exists "for spec 0004's future LearningJournal to read" — so the handoff ticket 24 needs was built by spec 0003 ticket 10.
- Root Cause: the four claims I checked were all mechanically checkable facts — does a grep return N, does a file define a class. The fifth was an *interpretive* claim about ownership. Verification gravitates toward what is cheap to check and skips what requires judgment, which is exactly backwards: the mechanical claims are the ones least likely to be wrong. Worse, four successful checks conferred borrowed confidence on the fifth, making an unverified claim feel verified by association.
- Detection: the peer re-examined its own reasoning and sent a correction, unprompted. Nothing I did would have caught it.
- Resolution: classify each claim in a peer report as *fact* or *inference* before checking anything, and verify the inferences first. And never relay someone else's finding as an error in one's own prior work without independently confirming the finding — an apology is an assertion too.
- Sharpened afterwards, by the peer, against its own failure: cost is not the discriminator, so "verify the expensive claims first" is necessary but not sufficient. The peer's own unverified claim was *cheap* — it saw `round_verdicts` on a field list and inferred what the type held rather than opening it, roughly thirty seconds of reading, and skipped it anyway. Confirmed from this side: verifying it took me a single grep. Both of the day's real defects had the same shape, and it is not expense. It is asserting something you did not read. The tell is a confident sentence with no `file:line` behind it — mine was "none of which exist in this codebase", the peer's was an ownership claim about a spec section it had not opened.
- Lesson: "I verified the report" is only as strong as the weakest claim in it, and partial verification produces confidence proportional to what was checked rather than to what was left. Count the claims, mark which were tested, and say so — "four of five verified" is an honest report; "verified" is not. And the operational trigger is not a cost estimate but a self-check at the moment of writing: if a sentence asserts a fact about the codebase and cannot carry a `file:line`, it has not been verified, however cheap it would have been to verify.

## 2026-08-13 — A Component Can Pass Every Gate With Zero Callers

*Reported by a peer session's ticket review; independently verified here before recording.*

- Mission: spec 0004 ticket 14 — the outcome record family, which joins ground truth (tests, review, plan acceptance, a human's stalemate choice) to the decision that produced it.
- Issue: the module landed complete and is invoked by nothing. `grep -c learning_outcomes` returns 0 in both `skills/worker-routing/advisory_consultation.py` and `skills/worker-routing/routing_check.py`. `production_invoker.py` returns 2, but both are prose inside docstrings (`:374`, `:422`) and the file carries no `import learning_outcomes` at all. Every other occurrence in the repo is a docstring or a test. The outcome family — the one that answers "did we choose well?" — receives no record in production.
- Root Cause: the ticket's acceptance criterion asked for "a public entry point that records an outcome", and that is exactly what was built. Nothing asked for a caller.
- Why no gate caught it: the tests call the entry point directly, so they are green. Review saw a well-formed module. `install.sh` and CI verified it is installed and checked. Each gate verified that the *component is correct*; none asked whether the *component is reached*. This is the same shape as "Checked Is Not Run" above, one level up — there the gap was between linting a test file and executing it; here it is between building a component and calling it.
- Detection: a ticket review that checked every "done" claim against the code rather than against the ticket's own status line.
- Resolution: filed as ticket 25, which requires a named caller or a documented protocol step for each of the four truths. General rule adopted: for any component whose entire purpose is to be called, one acceptance criterion must name the caller, and at least one test must reach the component through the caller's path rather than through its entry point.
- Lesson: "it works" and "it runs" are different claims about a component, and every quality gate in this repo was measuring the first. A test that calls an entry point directly proves the entry point works; it proves nothing about whether anything in production ever gets there. Note the specific trap in ticket 25's own remaining work: the human's stalemate choice is made *after* the consultation has already returned, so it may legitimately have no in-process caller and need a documented protocol step instead — "name the caller" is not always "add a function call".
- **Closed 2026-08-13, commit `533360f`.** `plan` got its code-path producer, `tests`/`review`/`stalemate_resolution` got documented steps in `protocol.md`. Worth recording separately: the defect shape this entry describes — an assertion or gate that is satisfiable without the path it names ever running — recurred twice more *inside the fix itself*, at a smaller grain. A write-failure test blocked the whole journal directory, so an unrelated earlier writer failed first and satisfied the assertion alone, proving nothing about the writer under test. And a `run_id`-correlation test compared a set union that degenerates to one element when the worker-execution side is empty, so it could pass with zero of the records it claimed to correlate. Both were caught by review, not by the tests themselves. This is the dominant defect class in this repository: not a wrong assertion, a *satisfiable* one. See `knowledge/institutional-memory.md`'s 2026-08-13 entry on the same recurrence for the operational rule.

## 2026-08-13 — A Push Is Not Scoped to Your Own Commits

- Mission: commit and push my own memory entries (`ec20c87`) after my user approved the push.
- Issue: the push published a second session's commit that its own user had not approved. `ad66416` (18:01:46) was authored by the peer session and deliberately held local — pushing requires that session's user's explicit approval, it had asked, and it was still waiting. My `ec20c87` (18:04:52) was committed on top of it, and `git push origin main` moved the remote `2bd3de8..ec20c87`, carrying every commit in between. Verified after the fact: `git branch -r --contains ad66416` lists `origin/main`, and `git merge-base --is-ancestor ad66416 ec20c87` holds.
- Consequence: no damage to content — the peer's commit was finished and approved in substance, only the publication was pending. But a decision that belonged to another user was made by my action, and neither of us knew it at the time. Detected only because the peer noticed its unpushed commit was suddenly on the remote.
- Root Cause: two errors compounding. Mechanically, `git push` has no concept of "my commits" — it advances a ref, which publishes the entire ancestor chain to that point. Procedurally, both sessions were reasoning as if a working tree could be partitioned by author. It cannot: one tree, one `.git`, one branch ref. The peer's plan to hold a commit local until approved was never enforceable, because any commit either session makes immediately becomes part of whatever the other session pushes next, known or not.
- Why my earlier caution missed it: I had already corrected my staging discipline (explicit pathspec instead of `git add -A`) and re-verified `git status` immediately before committing. Both checks look at the *working tree*. Neither looks at the *commit range about to be published*, which is a different object, and the one that push actually acts on. I even ran `git log --oneline -1` and saw `ad66416` as HEAD, and read it as "the peer committed" rather than "the peer's commit is now under mine and will ship with it."
- Resolution: before any push, run `git log origin/main..main` and name every commit riding along that is not yours; if any exists, ask its author before pushing. Recorded as a standing term of the two-session division of labour. Also note the corollary the peer drew: in a shared tree the only real gate on publication is *not committing yet* — a local commit is already shared state.
- Lesson: permission granted in one session does not extend to another session's work that happens to sit in the same history. My user approved pushing my work; git published whatever was in the chain. Before an outward-facing action in a shared repository, enumerate exactly what it will publish — the unit of publication is the ancestor chain, never the diff you authored.

## 2026-08-13 — A Worker's "All Green" Depended on a Tool That Was Already Known Broken

- Mission: ticket 25's iterative fix-and-review loop. A worker was asked to fix two review findings in `test_routing.py` and to run `ruff`/`mypy` on the CI module list before reporting done.
- Issue: the worker reported both linters clean. It had run them through this repo's local `.venv` — already recorded in `knowledge/institutional-memory.md` as broken (the repo moved from `Documents/Projects` to `Projects` and the venv was never rebuilt). A "clean" result from a broken interpreter proves nothing about the code; it is not a false positive so much as a non-result reported as a positive one.
- Detection: independently re-running `ruff check`/`mypy` through `pipx run` — the tooling this repo's own memory says to use — from outside the worker's session, the same discipline already applied to its test-count and scope claims.
- Root Cause: a worker's report of "verification passed" was accepted as equivalent to verification having happened, without checking *what ran it*. The 2026-08-11 lesson ("A Worker's Report About Files It Does Not Own Is a Guess") covered a worker's claims about files it never touched; this is the same failure one layer down — a worker's claim about a *check* it did run, using tooling it could not have known was known-bad.
- Resolution: none needed beyond re-running the gate, which was already the habit for this session. Recorded so the habit does not lapse: every worker's "tests pass" / "linter clean" claim is re-run independently before being trusted, every time, regardless of how the worker phrases its confidence.
- Lesson: "I ran it and it passed" is not the same claim as "it passed." A verification report is only as good as the tool it ran through, and a worker cannot be expected to know this repo's own known-broken corners unless told. Re-running the gate yourself costs seconds; trusting a broken report costs a shipped defect with a clean-looking paper trail.

## 2026-08-14 — A "Completed" Background Task Can Report Success on a Command That Never Ran

- Mission: launch two axis-specific (Standards vs. Spec) code reviews of ticket 16's journal-reader diff, each needing its own tailored prompt, via `codex review --base <commit>`.
- Issue: `codex review --base <BRANCH>` does not accept a custom `[PROMPT]` argument alongside `--base` — it errors immediately with `the argument '--base <BRANCH>' cannot be used with '[PROMPT]'`. But the process still exited with code 0, so the harness's own task-notification reported both background commands as "completed (exit code 0)." Nothing about the notification distinguished a review that ran from a review that failed its own argument parsing before doing anything.
- Detection: reading the actual captured output rather than trusting the completion notification — both files contained only the CLI's usage-error text, no review content at all.
- Root Cause: treating a background task's "completed, exit code 0" status as proof of a successful review, when exit code 0 only proves the process terminated without crashing — it says nothing about whether the command it was given was even valid.
- Resolution: relaunched both reviews via `codex exec` instead, with the prompt itself instructing the model to run `git diff <base>..HEAD` as its own first step, since `codex exec` accepts an arbitrary prompt with no `--base`-style conflict.
- Lesson: an exit code of 0 is not evidence a command did what it was asked to do — only that it did not crash. Always read a worker's actual captured output before treating a "completed" notification as a signal the underlying work happened, especially for any CLI invocation whose flag combinations have not been exercised before in this repo. See `knowledge/institutional-memory.md`'s 2026-08-13 entry making the same point about a worker's "all green" report.

## 2026-08-14 — Fallback Event: Codex 5.6 Terra Unavailable (Usage Limit)

- Mission: route a Simple-complexity fix (widen ticket 16's no-clock AST self-test to close a
  confirmed coverage gap) to `codex exec --model gpt-5.6-terra`, per the matrix's Simple-complexity
  row.
- Issue: `codex exec` exited 1 with `ERROR: You've hit your usage limit. ... try again at Aug 20th,
  2026 7:47 AM.` — an account-level quota, not a flag or config error. `command -v codex` and
  `codex --version` had both succeeded moments earlier (the rule 1 availability check only proves the
  binary is reachable, not that the account behind it has quota left).
- Detection: the background task notification reported "failed with exit code 1"; reading the
  captured output showed the usage-limit message, not a crash or bad-argument error like the
  `--base`/`[PROMPT]` conflict recorded above.
- Fallback taken: checked LM Studio's local endpoint (`curl -s http://127.0.0.1:1234/v1/models`) per
  rule 1.5 — reachable, with `qwen3-coder-next-mlx` loaded. Did not route there: this protocol
  documents no agentic CLI harness for LM Studio capable of multi-file edit + verify + report (only a
  "Sensitive" gate doing local validation), and improvising one — feeding it the prompt and applying
  its output myself — would be self-execution wearing a routing label, not routing to a worker. Fell
  through to the next tier the fallback chain names for Execution (Trivial/Simple): Claude Sonnet 5,
  already proven reliable for this exact module across three prior stages in this same session.
- Lesson: a reachable binary is not the same claim as a usable quota. The rule 1 availability check
  (`command -v` / a health-curl) should be read as "the worker exists," never as "the worker has
  capacity" — a CLI can pass both and still fail on the first real call. When a documented fallback
  tier (here, LM Studio) has no defined execution harness in this protocol, treat it as unavailable
  for that purpose rather than inventing one on the spot, and say so explicitly rather than silently
  skipping to whichever tier is easiest to reach.

## 2026-08-14 — The Orchestrator's Own Commit Message Outran Its Committed Tests

- Mission: close spec 0004 ticket 16 by widening its no-clock AST self-test (a finding from round 1
  of `/code-review`), commit the fix, and describe it accurately.
- Issue: the commit message for `dabfc5f` stated "two new tests confirm both the catch ... and the
  non-catch (`journal.now()`, a bare reference never called, an unrelated `.today()`)" and "False-
  positive resistance checked directly against ten hand-written cases." The file actually held two
  tests, both catch-only. The ten cases were real checks — but run in an ad hoc Python script the
  orchestrator wrote and discarded, never committed as tests.
- Detection: round 2 of `/code-review`'s Standards sub-agent ran `grep -n "journal.now\|today()\|
  hand-written"` against the committed file and found nothing, then read the two actual tests and
  confirmed neither covered a non-catch case.
- Root Cause: conflating "I personally verified this is true" with "this is asserted in the suite"
  while writing the commit message — the same fact/conclusion distinction this repo's memory already
  names for other sessions' claims, here made about the orchestrator's own prose.
- Resolution: `8cab197` added the four tests the message had already claimed existed, and this
  session's `/learn-session` pass generalized the lesson into `knowledge/institutional-memory.md`.
- Lesson: a commit message's coverage claim is a testable assertion like any other and should be
  checked the same way a worker's report is checked — grep for the test name it claims exists before
  writing the sentence, not after a reviewer catches the gap. Distrust of confident claims without
  `file:line` applies to the orchestrator's own writing exactly as much as to a worker's or a peer
  session's.

## 2026-08-14 — A Ticket's Status Line Under-Reported Progress, Not Over-Reported It

- Mission: answer "what's the next open task" for this repo's spec 0004 backlog.
- Issue: the first answer treated ticket 16 as fully unstarted, based on its `**Status:** ready-for-
  agent` line. In fact stage 1 (the journal reader) had already landed on `main` (`0c8ed7c`,
  `5a26606`) — the status line simply hadn't been updated after that partial landing.
- Detection: the user asked for a second look rather than accepting the first answer; `git log`/
  `grep` against the actual module names the ticket describes (`learning_scoreboard`, `read_journal`)
  showed real commits the status line didn't reflect.
- Root Cause: trusting a hand-maintained status field as the single source of truth for a
  multi-stage ticket, without cross-checking it against commit history — the same discipline this
  repo's memory already recommends for `docs/specs/` status drift (2026-08-11 entry), not yet applied
  to `.scratch/.../issues/*.md` status lines.
- Resolution: none needed beyond the correction itself — re-answered using `git log` and code search
  as the primary signal, the status line as a hint only.
- Lesson: a ticket's status field can be stale in either direction. This repo's memory already
  covered the false-positive case ("says done, isn't"); this is the false-negative case ("says not
  started, partially is") — just as real, and just as invisible if the status line is trusted alone.
  Before reporting a ticket's state, grep for the artifact names it's supposed to produce.

## 2026-08-14 — A Ground-Truth Recording Protocol With Zero Actual Records

- Mission: close ticket 16 per `CLAUDE.md`'s Learning-Journal Ground-Truth Recording section, which
  requires calling `learning_outcomes.record_test_result`/`record_review_verdict` under the task's
  `task_id` once its tests and review are known.
- Issue: before this session's calls, `.ralph/learning_journal.jsonl` did not exist on disk at all —
  confirmed by `find .ralph -type f`. Tickets 24 and 25, which built and wired the exact
  `learning_outcomes.py` machinery this protocol calls for, apparently never actually invoked their
  own entry points in practice, despite both being marked done.
- Detection: attempting to record ticket 16's outcomes and finding `journal_path(root_dir)` pointed
  at a file that had never been created.
- Root Cause: the same shape as the 2026-08-13 memory entry "A Component Can Pass Every Gate With
  Zero Callers" (ticket 25 itself) — a hand-recorded protocol step with no automated enforcement is
  invisible until someone actually tries to follow it. `.ralph/` is fully gitignored, so even a
  session that did call these functions would leave no trace another session could discover without
  running the check itself.
- Resolution: called `record_test_result(task_id="spec-0004-ticket-16", passed=True, root_dir=...)`
  and `record_review_verdict(..., approved=True, ...)` directly; confirmed via `read_journal` that
  both entries landed.
- Lesson: a protocol step whose evidence lives in a gitignored, locally-created file cannot be
  verified by reading the repo — only by trying to exercise it. Before assuming a "done" ticket
  actually followed every closing step its own protocol requires, check whether the artifact that
  step should have produced exists, the same discipline already applied to code-level claims.

## 2026-08-14 — Fallback Event: The Entire Cross-Family Critic Tier Was Unavailable at Once

- Mission: run the Planner-Critic consensus loop (protocol rule 6) over ticket 17's
  `implementation_plan.md` — Critic tier per the Complex-row matrix: Codex 5.6 Sol, fallback
  GPT-OSS 120B.
- Issue, in fallback order: (1) `codex exec --model gpt-5.6-sol` exited 1 on the same account-level
  usage limit recorded above (retry window opens Aug 20th); (2) LM Studio was reachable but
  `GPT-OSS 120B` is not among its downloaded models at all (`/api/v0/models` lists only
  `qwen3-coder-next-mlx`, `gemma-4-e4b-it-mlx`, and two embedding models); (3) `agy` advertises
  `gpt-oss-120b-medium` and `gemini-3.1-pro-high` in `agy models`, but print mode ignores
  `--model` entirely — two separate probes asking the session to name its own model both answered
  `Gemini 3.5 Flash (High)` regardless of the flag, and the first full critique invocation also
  returned a non-sequitur (answered a question that was never asked) instead of engaging the
  mission.
- Detection: read every worker's actual captured output rather than trusting exit codes — the agy
  runs exited 0 while doing the wrong thing twice over (wrong model, wrong task), exactly the
  failure mode the "Completed Background Task" entry above documents.
- Fallback taken: same-family Critic — `claude -p --model claude-opus-5 --effort high` — with an
  explicit degraded-independence flag in `.scratch/planning_debate.md`, per map ticket 04's own
  decision ("degraded-independence flag instead of silent same-family fallback"). Planner is
  Claude Fable 5, Critic is Claude Opus 5: different models, same vendor, flagged as such.
- Lesson: `agy models` listing a model id is not evidence `agy -p --model <id>` will run it —
  print mode pins the IDE session's default model and silently ignores the flag. Probe with a
  "name your own model" one-liner before trusting any agy model routing, and treat a worker that
  answers a question you never asked as a failed invocation even at exit 0.

## 2026-08-14 — A Fix For One Critic Objection Introduced Two More, Twice

- Mission: run a 3-round Planner-Critic consensus loop over `implementation_plan.md` (ticket 17),
  then a 3-round `/iterative-fix-review` loop over the resulting implementation.
- Issue: in both loops, the round that verified a prior round's fixes did not just confirm
  "objection addressed" — it re-checked each fix's actual mechanism against source and found new
  defects the fix itself had introduced. Planning: round 1 raised 8 objections; round 2, verifying
  the fixes, found 2 only partially closed and 3 entirely new defects in the fixes themselves (an
  unspecified family-join mechanism, an unpinned timestamp format, an overclaimed "no second place
  to update" statement). Post-implementation review: the fix for a broken `NaN` test (round 1's
  finding) shipped with a comment claiming the underlying `_classify_change` gap was "flagged
  separately" — round 2 found no artifact backed that claim.
- Detection: in both cases, a dedicated verification round that read the fix's diff against the
  cited source file, rather than accepting the round-1 finding's own description of what "fixed"
  would look like.
- Root Cause: a fix is itself new code (or new prose), and new code/prose carries the same risk of
  defects as the code it replaces — "the objection was addressed" describes intent, not a checked
  fact, until something re-verifies the fix's mechanism specifically.
- Resolution: both loops ran a genuine extra round rather than declaring convergence on the first
  "fixed" claim; both closed clean only after the second round found nothing further.
- Lesson: a fix-then-review loop's verification step must check the fix's mechanism against source
  again, every round — never accept "addressed" from the fixing step's own self-report, since the
  fix is exactly as likely to need a review as the original code was.

## 2026-08-14 — A Byproduct Discovery Nearly Got "Flagged Separately" Instead of Actually Tracked

- Mission: fix a Standards-axis finding — a test named `test_a_nan_metric_can_never_reach_a_
  comparison_as_an_improvement` whose `assertRaises` block raised before the code under test ever
  ran, making it a silent duplicate of an existing test.
- Issue: fixing the test properly (bypassing the frozen `MetricValue` dataclass post-construction
  to hand `_classify_change` a real NaN) surfaced, as a pure byproduct, that `_classify_change`
  actually misclassifies a NaN metric as `"improved"` under `lower_is_better` — a real defense-in-
  depth gap in already-shipped, already-tested ticket 16 code, currently unexploitable only because
  the public constructor blocks NaN. The fix's own comment described this as "flagged separately"
  with no ticket, no ERRORS.md entry, nothing — the identical shape as this file's own "commit
  message outran its committed tests" entry above, this time in a code comment instead of a commit
  message.
- Detection: a second `/code-review` round grepped `ERRORS.md`, `CONTEXT.md`,
  `knowledge/institutional-memory.md`, and every `.scratch/routing-backlog/issues/*.md` for any
  trace of the claimed tracking and found none.
- Root Cause: writing "flagged separately" felt like tracking while writing it, the same way a
  confident commit-message claim feels true while writing it — neither is checked against an
  artifact until something else demands the artifact exist.
- Resolution: filed `.scratch/routing-backlog/issues/28-classify-change-nan-defense.md` as the real
  artifact, and updated the test's comment to cite it by path instead of the vague claim.
- Lesson: "flagged separately," "tracked elsewhere," or any similar phrase in a comment or commit
  message is not itself tracking — it is a promise. Before writing it, either file the real ticket
  or ERRORS.md entry it refers to, or don't write the phrase at all.

## 2026-08-15 — Nine Review Rounds Found One Dead Guard At A Time; One Mutation Sweep Found The Rest

- Mission: run `/iterative-fix-review` over tickets 18 and 26 (`acceptance_gate.py`,
  `ReplayBenchmarkRecord`, a real `mean_benchmark_score`) until both `/code-review` axes return zero.
- Issue: the loop plateaued. Rounds 4 through 7 each returned exactly one real coverage gap plus a
  pile of stale prose, and never fewer — findings per round ran 1, 2, 9, 3, 4, 3, 4, 3, 2. Three of
  those single findings were the same defect class in three different guards, all of them green in
  CI and invisible to review-by-reading: `_require_aware_now` mirroring `learning_scoreboard`'s guard
  with a byte-identical message raised one statement later (so `assertRaises` could not tell which
  fired, and neither could `assertRaisesRegex`); `window_days`, a parameter no test ever passed a
  non-default value for, so dropping it from one of two `read_scoreboard` calls stayed green;
  `_wire_timestamp`'s `.astimezone(timezone.utc)`, a no-op in every test because every test injected
  a `now` that was already UTC.
- Detection: not reading. Each was found only by deleting or inverting the production line the test
  named and running the suite. The plateau broke when round 8 stopped sampling one guard per round
  and instead enumerated every guard, validator, branch and conversion the diff introduced and
  mutated each — 36 in total — surfacing the last three survivors at once. Round 9 repeated the sweep
  (22 mutations) and found one more, then the Spec axis returned zero twice running.
- Root Cause: a reviewer reading a guard sees what it is *meant* to do and moves on; nothing in
  reading reveals that a collaborator already does the same thing one statement later, or that the
  parameter carrying a behaviour is never given a value that would expose it. Sampling one guard per
  round makes the loop's cost linear in the number of guards while its yield stays at one.
- Resolution: every survivor fixed with a test verified by mutation before committing. The
  mirrored-guard fix is worth noting: rather than change the message so a test could tell the two
  apart — which would break the mirror idiom this repo uses deliberately — the test stubs the
  collaborator to raise if reached, asserting the property the mirror actually provides (refusal at
  the front door, before the collaborator is called at all).
- Lesson: when a fix-and-review loop shows the plateau signature — one real finding plus stale prose,
  round after round — stop sampling and budget one systematic mutation sweep. Enumerate every guard,
  validator, branch and conversion the change introduced, mutate each, and treat a test as binding
  only when it goes red. Two shapes recur here: a guard mirroring a collaborator's identical guard,
  and a parameter no test passes a non-default value for.

## 2026-08-15 — The Same Install-List Gap A Third Time, Plus A Fourth List Nobody Was Guarding

- Mission: wire ticket 18's new `acceptance_gate.py` into the repo the way every sibling module is.
- Issue: it was missing from `install.sh`'s `MANAGED_FILES` and `uninstall.sh`'s `INSTALLED_FILES` —
  the third recurrence of one gap (`learning_journal.py`, then `learning_report.py` in ticket 17,
  now this). `ManagedFileClosureTests` exists precisely to stop that and could not: it asserts only
  that a *managed* file's sibling imports are themselves managed, so a leaf module nothing imports
  yet is invisible to it — and a module landing one ticket ahead of its caller is exactly that.
  Separately, CI's `PYTHON_MODULES` was asserted only in the list-to-files direction, never
  files-to-list, so removing a module from it left the suite green and the module silently unlinted
  and untype-checked.
- Detection: a Standards-axis review round caught the install lists; the CI list was caught two
  rounds later by mutation, after the install fix had already established what the missing direction
  looked like.
- Root Cause: the closure test encoded a rule about imports when the rule that actually holds is
  about existence — every non-test module in the directory is production code, and production code
  the installer does not copy does not exist on an installed harness. Adding a module touches four
  lists (`MANAGED_FILES`, `INSTALLED_FILES`, `PYTHON_MODULES`, `PYTHON_TESTS`) and only three were
  guarded in the direction that catches an omission.
- Resolution: `test_every_production_module_in_the_skill_directory_is_managed` and
  `test_ci_checks_every_python_file_in_the_skill_directory` now assert the existence-side invariant
  for both lists. Both verified by removing the entry and watching them fail.
- Lesson: a closure test that walks a dependency edge can only see nodes something already points
  at. When the real invariant is "everything here must appear there", assert it directly and in both
  directions — and count the lists: this repo has four, and a fix that guards one says nothing about
  the other three.

## 2026-08-22 — Protocol Slimming Accidentally Authorized Cloud Fallback for Sensitive Tasks

- Mission: compact `protocol.md` from 22KB to <5KB while preserving all governance invariants (Spec 0011 Ticket 01).
- Issue: the compacted Fallbacks summary collapsed `Trivial/Simple/Sensitive` into a single `T0 -> T1` rule. This contradicted the model matrix ("Sensitive: Fail closed if offline") and authorized sensitive tasks carrying PII, auth keys, or private tokens to fall back to cloud Gemini Flash.
- Detection: caught during the multi-agent Council Review (Claude Opus 5) by comparing the slimmed protocol text against the pre-slimming rules and Spec 0011 User Story 11.
- Root Cause: prose compaction grouped Tier-0 tiers without distinguishing between tiers that allow cloud hops (Trivial/Simple) and tiers that strictly fail closed (Sensitive).
- Resolution: updated `protocol.md`'s Fallback rule to explicitly separate `Sensitive: Local only (fail closed)` from `Trivial/Simple: T0 -> T1`, and added regression unit test `test_sensitive_fallback_fails_closed_without_a_cloud_hop` in `test_routing.py`.
- Lesson: when compacting security or routing policies, never merge fail-closed tiers with fail-open/fallback tiers into one shared rule line. Always assert policy rules with negative test assertions that explicitly verify forbidden transitions are absent.

## 2026-08-22 — Substring Matching on Short/Hyphenated Keywords Broke Scoped Memory Retrieval Precision

- Mission: score and retrieve 3–5 high-signal Golden Rules in `prompt_assembler.py` based on task context.
- Issue: bare substring check (`keyword in task_lower`) caused short keywords (like `"ci"`) to fire inside common words (`"specification"`, `"decision"`), and symbol-leading flags (like `"-a"`) to fire inside hyphenated terms (`"sub-agent"`), filling the 3–5 slot window with irrelevant rules.
- Detection: multi-agent review reproduction demonstrated that a prompt mentioning "specification decision" ranked Rule 7 (CI isolation) and Rule 17 (MANAGED_FILES) at the very top.
- Root Cause: natural language tasks contain technical fragments as substrings inside everyday English vocabulary.
- Resolution: implemented regex word-boundary matching (`\b`) with dedicated symbol/flag edge handling in `prompt_assembler._score_golden_rules`, backed by unit tests in `test_prompt_assembler.py`.
- Lesson: never use raw substring `in` checks for keyword scoring when keyword length is short (<=3 chars) or contains punctuation. Use tokenization or boundary-aware regex to prevent semantic false-positive displacement.

## 2026-08-22 — Curl Timeout Formatting Truncated Sub-Second Values to 0 and Disabled Limits

- Mission: invoke local LM Studio models via curl command templates with configurable timeouts.
- Issue: formatting timeouts via `str(int(timeout))` turned sub-second values (e.g. `0.2s`) into `"0"`, and `curl --max-time 0` disables curl timeout enforcement completely, allowing processes to hang. Formatting with `%g` rendered large numbers in scientific notation (e.g. `1e+06`), which curl rejects.
- Detection: caught during Spec Review when checking timeout handling against sub-second capability probing.
- Root Cause: assuming timeout is always an integer >= 1 second without checking decimal string representation requirements for CLI tools.
- Resolution: updated `build_worker_command` to validate positive finite timeouts and format via `f"{timeout:.3f}".rstrip("0").rstrip(".")` (e.g. `0.2` and `10000`), with regression unit tests.
- Lesson: when formatting CLI arguments for external binaries (like `curl`), format floats explicitly without scientific notation and ensure sub-second values never truncate to zero.

