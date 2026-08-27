# 0014 — Generated Institutional Memory & Single-Source Golden Rule Catalog

* **Date:** 2026-08-27
* **Status:** Approved (`ready-for-agent`)
* **Target Backlog Tickets:** Tickets 54–59 → GitHub issues #30–#35 (label `ready-for-agent`)
* **Related:** Spec 0011 ticket 03 (origin of the dual-store contract), ADR 0010 (atomic bounded memory lesson accumulation)

---

## Problem Statement

The system keeps its distilled operating wisdom in two places that are required to be identical
twins, and both of them are written by hand.

One is the `GOLDEN_RULES` catalog inside the `prompt_assembler` module: a tuple of structured,
categorised rules carrying the keywords and file patterns that `extract_scoped_memory` scores
against a task description, injecting only the top 3–5 into a [[Worker]]'s mission brief. This is
the only one of the two that any running code consumes.

The other is `knowledge/institutional-memory.md`: a human-readable Markdown rendering of the same
rules, introduced by spec 0011 ticket 03. No production code path opens this file. The only reader
in the entire repository is the sync test that exists to prove the two stay identical.

The two have now drifted apart three separate times, always the same way. A `/learn-session` run
appends new numbered rules to the Markdown document and never touches the catalog in code
(`3815eea`, `d4934ac`, `12054a0`). Twice the drift was repaired after the fact — once by
back-filling the missing rules into the catalog (`b03d510`), once by deleting the appended rules
from the document (`932e748`). The third occurrence is live: the document currently parses to 35
numbered rules while the catalog holds 25, and the sync test fails with `35 != 25`. The repository's
own failure log already recorded the lesson on 2026-08-24, and the drift recurred anyway.

The reason it recurs is structural, not a matter of discipline:

1. **Two hand-written copies of one truth.** Any invariant maintained by remembering to update a
   second file eventually fails.
2. **The writing process does not know the contract exists.** The `/learn-session` skill instructs
   its operator to prepend insights to `institutional-memory.md` and contains no mention of
   `GOLDEN_RULES`, the catalog, or the sync requirement.
3. **The skill is not governed by this repository.** `/learn-session` lives only in the operator's
   home directory. `install.sh` synchronises `worker-routing` and `council-review` and nothing else,
   so the skill is neither version-controlled nor distributed, and a fix applied to it is a fix
   applied to exactly one machine.
4. **The guard fires after the damage.** The sync test detects drift only once it is already
   committed, and a test that stays red across sessions trains every subsequent review round to
   ignore failures in that file.

The user-visible consequences: rules that a session genuinely learned never reach any [[Worker]] —
the ten currently in the document are inert documentation; the catalog grows without any
retirement mechanism, so over time good rules compete for the 3–5 injection slots against stale
ones; and the operator has no trustworthy place to look when they want to know what the workers
actually know.

---

## Solution

Make the drift impossible to express rather than detectable after the fact.

1. **One source of truth.** The `GOLDEN_RULES` catalog in code becomes the sole authority. It is the
   only artifact any running code reads, so it is the only one whose content can be wrong in a way
   that matters.

2. **The document becomes a build output.** `knowledge/institutional-memory.md` is regenerated from
   the catalog by a pure renderer and checked in. It keeps its path, its name, and its human
   readability — it remains the operator's insurance policy, the one place to open when asking what
   the workers know — but it stops being a thing anyone edits. Hand-written prose added to it is
   overwritten by the next regeneration, which is precisely the property that makes the drift
   unexpressible.

3. **The sync guard becomes exact.** The existing repository-level sync test stops counting rules
   against a hardcoded number and instead asserts that the checked-in document is byte-for-byte
   equal to the renderer's output over the real catalog. The magic number disappears; no future
   expansion of the catalog requires editing a test.

4. **The learning process is brought into the repository and taught the contract.** `/learn-session`
   is vendored into this repository's `skills/` directory, distributed by `install.sh` alongside the
   existing skills, and rewritten so that adding a lesson means adding a catalog entry and
   regenerating the document — never appending prose to the document. Per the operator's explicit
   decision, it does this autonomously, without pausing for approval.

5. **Retirement gets a trigger.** The catalog carries a last-reviewed date. A pure, clock-free
   staleness check reports when the configured review interval has elapsed, so the periodic cleanup
   is a scheduled event with a visible due signal rather than an intention.

6. **The current red test is contained immediately.** Ahead of the full implementation, the sync
   test is narrowed to keep an active guard over the 25 rules that genuinely reach workers, while
   the total-count assertion is neutralised with an explicit pointer to this spec. The suite returns
   to green without the guard being switched off wholesale, and the ten in-flight document rules are
   left untouched.

---

## User Stories

1. As an operator, I want a single place that decides what my workers know, so that I never have to reason about which of two files is currently correct.
2. As an operator, I want the human-readable memory document to always match what the workers actually receive, so that opening it tells me the truth rather than a stale snapshot.
3. As an operator, I want the memory document to keep existing even though no code reads it, so that I retain a readable record for the day I need to audit what the system has learned.
4. As an operator, I want the document regenerated rather than hand-edited, so that the two copies cannot silently diverge no matter who edits what.
5. As an operator, I want a lesson learned in a session to actually reach my workers, so that the learning loop produces behaviour change and not just documentation.
6. As an operator, I want the learning process to update both the catalog and the document by itself, so that I do not have to remember a second step at the end of every session.
7. As an operator, I want the learning process to be version-controlled in this repository, so that a fix to it survives beyond the single machine it was applied on.
8. As an operator, I want the learning process distributed by the existing installer, so that every [[Harness]] I use sees the same behaviour.
9. As an operator, I want to be told when the rule catalog is due for review, so that periodic cleanup actually happens instead of being perpetually deferred.
10. As an operator, I want the review reminder driven by elapsed time rather than by rule count, so that the trigger is predictable and does not fire in the middle of a burst of legitimate additions.
11. As an operator, I want the test suite to be green while this work is pending, so that a genuinely new failure in the same module is immediately visible instead of buried under a known one.
12. As an operator, I want the known gap recorded in the interim test itself, so that anyone reading the code learns why the assertion is narrowed and where the full fix is specified.
13. As an operator, I want the ten currently-appended rules left in place until this spec is implemented, so that a concurrent session's uncommitted work is never discarded.
14. As a [[Worker]], I want the rules injected into my mission brief to be the complete current set, so that I am not missing a lesson the system already learned.
15. As a [[Worker]], I want each rule to carry its keywords and file patterns, so that the rules I receive are the ones relevant to my task rather than an arbitrary slice.
16. As an [[Orchestrator]], I want the rule catalog to remain a plain in-process constant with no filesystem access, so that assembling a prompt stays fast and cannot fail on I/O.
17. As a developer extending the catalog, I want to add a rule in exactly one place, so that there is no second edit to forget.
18. As a developer extending the catalog, I want the document regeneration to be a single command, so that producing the checked-in output is mechanical.
19. As a developer, I want the sync test to fail loudly when the checked-in document is out of date, so that a forgotten regeneration is caught before it is committed.
20. As a developer, I want the sync test to require no edit when the catalog grows, so that expanding institutional memory does not carry a test-maintenance tax.
21. As a developer, I want the renderer to be a pure function of the catalog, so that I can test the exact output shape without touching the filesystem.
22. As a developer, I want the staleness check separated from the renderer, so that the generated document stays identical from one day to the next and the byte-for-byte comparison remains meaningful.
23. As a developer, I want the staleness check to take the current time as an argument, so that it obeys the repository's existing clock-free test invariant.
24. As a reviewer, I want the categories in the document to match the categories in the catalog, so that a rule filed under the wrong heading is caught mechanically rather than by eye.
25. As a reviewer, I want each of the ten pending rules explicitly adjudicated — promoted, merged, or rejected — so that no rule is silently dropped during the migration.
26. As a reviewer, I want rules that describe a single past incident distinguished from rules that generalise, so that the injection slots are not consumed by four facets of one story.
27. As an operator, I want to know that the learning process no longer pauses for my approval, so that the loss of that checkpoint is a decision I made rather than a change I discover later.
28. As an operator, I want incident narrative to keep going to the failure log rather than into the catalog, so that the catalog stays a set of directives and not a diary.
29. As an operator, I want the document's stated rule count in its own title to be generated, so that it can never again claim twenty-five while listing thirty-five.
30. As a future maintainer, I want this spec's decisions reflected in the domain glossary, so that the vocabulary for this area is defined in one place.

---

## Implementation Decisions

### Source of truth and direction of generation

- The `GOLDEN_RULES` catalog in the `prompt_assembler` module is the single authority. The Markdown
  document is derived from it. The reverse direction is removed entirely — nothing parses the
  document to produce catalog entries.
- `knowledge/institutional-memory.md` keeps its current path and filename. Nothing outside this
  repository refers to it, so there is no compatibility constraint on its location, but keeping it
  stable preserves the operator's habit and every existing reference in the failure log and specs.
- The document gains a generated banner identifying it as build output and naming the command that
  regenerates it, so a human who opens it and starts typing is told immediately that their edit will
  not survive.

### The renderer

- A new pure function renders the complete document from the catalog plus its metadata block. It
  performs no filesystem access, no subprocess, no network, and no clock reads. This follows the
  existing convention for report generation in this repository — pure, clock-free rendering with
  thin atomic I/O at the edge.
- The renderer owns the document's entire structure: the title (including the rule count, which is
  therefore always correct by construction), the metadata block, the category headings, and the
  ordering of rules within categories.
- Rules are grouped under their category heading and emitted in ascending id order within each
  group. The current document orders some categories by insertion rather than by id; the generated
  output normalises this. The one-time reordering is expected and is part of the migration diff.
- The `GoldenRule` record already carries `id`, `category`, `title`, `directive`, `keywords`, and
  `file_patterns`. Only `directive` and `title` are rendered as prose; `keywords` and
  `file_patterns` are retrieval metadata and are not printed, matching the current document's shape.

### Catalog metadata and retirement

- A small module-level metadata record accompanies the catalog, carrying at minimum the last-reviewed
  date and the review interval. It is a frozen record alongside `GOLDEN_RULES`, not a file read at
  runtime.
- A separate pure function answers whether a review is due, taking the metadata and the current time
  as an explicit argument. It must not be folded into the renderer: a document whose content depended
  on the current date would change daily and would defeat the byte-for-byte sync test.
- The due signal is surfaced to the operator, not acted upon automatically. Retirement of a rule is a
  human decision; this spec provides only the trigger.
- No rule is auto-expired by age. The chosen trigger is elapsed time since the last review, which
  schedules a human pass over the whole catalog.

### The learning process

- `/learn-session` is vendored into this repository under `skills/`, becoming the canonical copy, and
  `install.sh` is extended to distribute it to the same [[Harness]] targets it already serves. Its
  existing copies outside the repository become mirrors and are not edited directly.
- The skill's write path for institutional memory changes from "prepend prose to the Markdown
  document" to "add a structured entry to the catalog in code, then regenerate the document". The
  Markdown document is never written to directly by the skill.
- Per the operator's explicit decision, the skill performs this without pausing for approval. This
  removes a checkpoint that exists today — the skill currently presents its classified insight list
  for confirmation before writing anything. The consequence is recorded here deliberately: an
  unreviewed lesson can enter every worker's mission brief and will remain until the next scheduled
  review. The scheduled review is the compensating control.
- Routing of insight categories is unchanged in every other respect: incident narrative continues to
  `ERRORS.md`, domain vocabulary continues to `CONTEXT.md`. Only genuinely reusable directives become
  catalog entries.

### Migration of the ten pending rules

- Rules 26–35 currently in the document are adjudicated one at a time during implementation. Each is
  either promoted to a catalog entry, merged into another entry, or rejected with its content left in
  the failure log.
- Two are already known to be filed under a category that does not match their content (both are
  governance rules sitting under the subprocess-safety heading) and must be re-categorised on
  promotion, since the sync guard compares categories as well as ids.
- Four of the ten derive from a single review incident. They are candidates for consolidation rather
  than four separate injection-slot competitors, but the adjudication is per-rule and must be recorded.
- At the time of writing, four of the ten are uncommitted work belonging to a concurrent session
  sharing this working tree. Implementation must not stage, revert, or reorder them before that work
  is committed.

### Domain glossary

- Two terms are added to the glossary: one naming the catalog as the authoritative structured set of
  distilled directives that `extract_scoped_memory` scores, and one naming the generated Markdown
  document as its rendering. The glossary currently has no vocabulary for either, which is part of why
  the relationship between them keeps being misunderstood.

### Interim containment

- Ahead of the above, the failing sync test is split: an active assertion that the 25 catalog rules
  are present in the document with matching ids and categories, and a neutralised assertion for total
  parity carrying an explicit skip reason naming this spec.
- The narrowed test must be named and documented so that it claims exactly what it checks. A test that
  keeps its original name while checking a subset is the failure mode this repository has recorded
  before — an assertion that passes without exercising the path it names.
- The interim change touches test code only. No rule is added, removed, or moved in either the document
  or the catalog.

### Ticket breakdown

Ordered by dependency; blockers first. Each ticket cuts a complete, independently verifiable path.

- **54 — Interim containment.** Narrow the sync guard so that every catalog rule must appear in the
  document with a matching id and category, and neutralise the total-parity assertion with an explicit
  pointer to this spec. The subset direction is deliberate: it stays green as the catalog grows, so it
  gates no later ticket. *Blocked by: none.*
- **55 — Catalog metadata and staleness check.** Add the frozen metadata record carrying the
  last-reviewed date and review interval, plus the pure staleness function taking the current time as
  an explicit argument. Precedes the renderer because the renderer emits that date.
  *Blocked by: none.*
- **56 — Adjudicate and migrate rules 26–35.** Decide each pending rule — promote, merge, or reject —
  and add the promoted ones to the catalog under the correct category. Must precede the first
  regeneration, which would otherwise delete their prose. *Blocked by: 54.*
- **57 — Pure document renderer.** The pure function producing the complete document from the catalog
  and its metadata, including the title's rule count. *Blocked by: 55.*
- **58 — Regeneration and exact sync guard.** Add the regeneration entry point, check in the generated
  document, convert the sync test to byte-for-byte equality, delete the bespoke Markdown parser and the
  hardcoded count, and add the two glossary terms. *Blocked by: 56, 57.*
- **59 — Vendor the learning skill and distribute it.** Move `/learn-session` into this repository's
  skills directory, extend `install.sh` to distribute it alongside the existing skills, and rewrite its
  memory write path to add a catalog entry and regenerate the document rather than appending prose.
  *Blocked by: 58.*

---

## Testing Decisions

A good test here asserts externally observable behaviour: what the renderer produces for a given
catalog, whether the checked-in artifact matches, and whether the staleness check reports due for a
given pair of dates. None of these require knowledge of how the renderer builds its string, how the
catalog is stored in memory, or which helper functions exist.

The design deliberately minimises seams. Three exist, one of which already exists today.

**Seam 1 — the pure renderer (new).** Called directly with a small synthetic catalog and metadata,
asserting the exact document text. Because it is pure and clock-free, the assertion can be on the
complete output rather than on fragments. This is the highest available seam for "the document is
generated": everything downstream of it is a file write.

**Seam 2 — the repository-level sync test (existing, strengthened).** The current
`InstitutionalMemorySyncTests` case in the learned-state test module already reads the real
`knowledge/institutional-memory.md` and compares it against the real catalog. It is reused rather than
replaced. Its assertion changes from three separate checks (parsed count, catalog count, id-to-category
mapping) to a single byte-for-byte equality against the renderer's output. The equality subsumes all
three and, unlike them, requires no update when the catalog grows. The bespoke Markdown rule parser in
the test module is deleted along with the count assertions — with generation in place there is nothing
left to parse.

**Seam 3 — the staleness check (new, small).** Called directly with a metadata record and an explicit
current time, asserting the due/not-due boundary in both directions. Kept separate from the renderer
specifically so that generated output does not vary with the date.

No seam is introduced for the file write itself. A thin write of an already-computed string is covered
by seam 2, which fails if the bytes on disk are not what the renderer produces.

Prior art in this repository to follow:

- The learning-report HTML tests are the closest analogue for rendering: a pure, clock-free generator
  tested without touching disk. Note that their convention is substring assertions rather than
  full-document equality, because that report is extended frequently. This spec deliberately departs
  from that convention for the memory document, where exact equality is the entire point.
- The existing prompt-assembler tests are prior art for exercising the catalog and
  `extract_scoped_memory` directly, and remain valid unchanged.
- The repository's AST-level guards for injected-time arguments are prior art for seam 3's clock-free
  requirement.

Explicitly not tested: that `/learn-session` behaves correctly. It is a prompt document, not code. Its
correctness is enforced structurally — a run that writes prose to the document instead of a catalog
entry produces a document that no longer equals the renderer's output, and seam 2 fails.

---

## Out of Scope

- **The dormant learning automation.** The [[LearnerWorker]], [[LearnedState]] store, and
  [[RiskTieredApplication]] tiering describe a separate, richer mechanism for turning session evidence
  into adopted memory documents. The `learned-state/` store has never been created in this repository
  and the worker has never run. Its relationship to the golden-rule catalog is a real design question
  and is deliberately deferred to a later spec.
- **Changing what `extract_scoped_memory` injects.** The 3–5 rule cap, the scoring weights, and the
  keyword/file-pattern matching are unchanged.
- **Automatic retirement of rules.** This spec delivers a due signal only. Deciding which rule dies is
  a human act.
- **Restoring an approval checkpoint to the learning process.** The operator chose autonomous writing.
  Reintroducing a gate is a future decision, not a hidden part of this work.
- **Migrating the archived legacy memory document.** It remains a frozen historical record.
- **Any change to the dashboard specification (0013) or its open tickets.** This spec shares the
  backlog directory with them and nothing else.

---

## Further Notes

- The drift this spec eliminates has a documented three-occurrence history, and the failure log entry
  written after the second occurrence did not prevent the third. That is the evidence for choosing a
  structural fix over a stronger warning.
- The one real cost of generation is that the document can no longer carry free-form prose that has no
  catalog entry. This was weighed and accepted: the operator does not read the document in normal
  operation and values it as an accurate future reference, which generation serves better than
  hand-editing.
- The first regeneration will produce a large diff, because rules within some categories are currently
  ordered by insertion rather than by id. This is expected once and should not be mistaken for a
  content change.
- The generated document's title currently hardcodes a rule count that has been wrong since the third
  drift. After this work the count is produced by the renderer and cannot be wrong.
- Sequencing note: ticket 58 adjudicates rules that are, at the time of writing, partly uncommitted in a
  concurrent session's working tree. It should not start until that session's changes are committed.
