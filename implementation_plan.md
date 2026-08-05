# Finalized Implementation Plan — RoutingAuditEngine

Status: Critic-approved for implementation  
Approval basis: 68/68 current tests passing plus the consensus recorded in
`.scratch/planning_debate.md`

## 1. Objective

Refactor `skills/worker-routing/routing_check.py` into a logically modular,
testable `RoutingAuditEngine` while preserving every current default behavior and
directly imported helper. Add deterministic JSON and SARIF reports, end-to-end
CLI option relay, and a single shared HMAC verification contract.

The only approved exception to behavioral preservation is fail-closed handling
for symlinked, non-regular, or oversized workspace key files. That exception is
limited to local key-file safety and does not alter calibration-v1 signed bytes
or acceptance of ordinary existing key files.

The implementation is complete only when legacy text behavior remains unchanged,
the current test suite still passes, the new machine formats are valid and
stream-clean, and verification never creates secret material.

## 2. Non-goals

- Do not create a Python package or new runtime Python module.
- Do not change installer or uninstaller behavior.
- Do not remove or rename any existing `routing_check.py` helper.
- Do not change the versionless calibration-v1 signed bytes.
- Do not claim that v1 authenticates council debate/consensus metadata.
- Do not add replay protection or a new manifest schema in this refactor.
- Do not enforce mode `0600` on pre-existing workspace keys; that requires a
  separately documented migration.
- Do not change routing policy, safe-command policy, worker patterns, or code
  extensions except where a characterization test proves an existing defect and
  the change is separately approved.

## 3. Compatibility invariants

The following behavior is frozen for this mission.

### 3.1 Module API

At minimum, keep these names importable with their current signatures and
defaults:

- mutable `Step(index, routing=None)` with `routing`, `writes`, `commands`,
  `unknown_write_tools`, `calibration_headers`, and
  `calibration_manifests`, plus `index`;
- `CALIBRATION_VIOLATION`;
- `load_config()`, `load_patterns(config)`, `load_code_extensions(config)`, and
  `load_safe_patterns(config)`;
- `is_worker_invocation`, `is_command_safe`, `structural_binding_issues`, and
  `check_structural_binding`;
- `_step_from_dict(index, data)`;
- `parse_steps(log_file, text)`;
- `compute_metrics(steps, code_extensions, worker_patterns, safe_patterns,
  root_dir=None)`;
- `run_audit(config, log_file, strict=False, root_dir=None) -> int`;
- `main()`.

This inventory is non-exhaustive. Preserve every current module-level callable
as a facade for this release, including `_kv_pattern`,
`_strip_command_wrappers`, `split_command_segments`, `_bash_c_payload`,
`command_segments`, `_collect_calibration_manifests`,
`_embedded_json_values`, `_add_calibration_text`, `get_calibration_secret`,
`_canonical_calibration_payload`, `calibration_signature_issue`,
`is_unknown_write_tool`, `_parse_text_steps`, `_dig`, `_strip_quotes`, and
`_parse_jsonl_steps`. Add a characterization test that snapshots the callable
names and the signatures of the externally exercised helpers before refactoring.

`compute_metrics()` must continue to return the existing dictionary keys and
value shapes:

`total_writes`, `code_writes`, `routing_declarations`, `worker_calls`,
`code_write_files`, `violations`, `declaration_drift`, `violation_details`, and
`calibration_markers`.

In particular, `violations` remains one tuple per violating step, not one tuple
per underlying cause.

### 3.2 Parsing and policy

- Leading-`{` content selects JSONL regardless of file extension.
- A `.jsonl` suffix selects JSONL when content does not establish the format.
- Malformed JSONL fails the whole audit; it is never partially accepted.
- Plain-text step indices are sequential parsed occurrences.
- Tool-shape support for `tool`/`name`, direct/nested arguments, and quoted
  Antigravity values remains intact.
- Code extension matching remains exact through `Path(...).suffix`.
- Worker mentions in prose never count as invocations.
- Worker credit remains scoped to the same step.
- If any segment makes one original command unsafe, every worker call found in
  that original command is suppressed.
- Unknown mutation-like tool names remain fail-closed as `LOG-01`.
- Missing, unreadable, empty, malformed, no-step, invalid-config, and
  parser-cross-check failures remain operational failures with exit 2.

### 3.3 Default text CLI

For valid legacy invocations, preserve byte-for-byte:

- metric labels and ordering;
- emojis and explanatory prose;
- stdout versus stderr placement;
- detailed source-edit ordering;
- exit 0 for clean audits;
- exit 1 for violations or strict-mode warnings;
- exit 2 for audit/argument/config/log failures.

`--strict` must continue to work before or after `LOG_FILE`.

## 4. Architecture

Keep the physical deployment flat. Define logical components inside
`routing_check.py` and retain module-level compatibility wrappers.

```text
routing_check.py
├── compatibility constants, Step, and legacy functions
├── domain models
│   ├── Severity
│   ├── AuditIssue
│   ├── StepAudit
│   └── AuditReport
├── parsers
│   ├── TextLogParser
│   ├── JsonLinesLogParser
│   └── LogParserRouter
├── policy
│   ├── DeclarationValidator
│   ├── WorkerInvocationMatcher
│   ├── MutationDetector
│   ├── CalibrationValidatorAdapter
│   └── PolicyEvaluator
├── output
│   ├── ConsoleReportFormatter
│   ├── JsonReportFormatter
│   └── SarifReportFormatter
└── RoutingAuditEngine
```

This avoids new sibling-import, bootstrap, installer, and uninstaller failure
modes. `routing_check.py` and `agent_council.py` are already installed together,
so the only cross-file dependency introduced is between existing managed files.

Resolve that dependency lazily inside `CalibrationValidatorAdapter`, after
calibration evidence is detected. Normal parsing and audits without calibration
evidence must not require importing `agent_council.py`. Prefer the ordinary
sibling import used by the executable; provide a file-relative import fallback
based on `SCRIPT_DIR` for `spec_from_file_location()` callers. Register the
fallback module under one stable private name in `sys.modules` before execution
and cache it. If the managed sibling is missing or fails to import when evidence
requires verification, raise a sanitized `AuditOperationalError` and exit 2;
do not misreport a missing verifier implementation as a bad signature.

## 5. Exact file scope

| File | Planned change |
|---|---|
| `skills/worker-routing/routing_check.py` | domain models, logical components, engine, compatibility facades, CLI, formatters |
| `skills/worker-routing/agent_council.py` | public pure signature verifier and hardened read-only key retrieval shared with the auditor |
| `skills/worker-routing/test_routing.py` | loader fix, characterization, equivalence, CLI/format/HMAC/wrapper tests; preserve current uncommitted additions |
| `skills/worker-routing/routing-audit.sh` | deterministic option parsing and end-to-end relay |
| `README.md` | document options, formats, schemas, exit codes, root/config semantics, and v1 trust boundary |

No change is planned for `install.sh`, `uninstall.sh`, `routing-config.json`,
`protocol.md`, or skill copies. If implementation discovers that one of those
must change, stop and return the plan for another review rather than expanding
scope silently.

## 6. Domain model and semantics

Use `str, Enum` for stable severity values and frozen dataclasses with tuple
fields:

```python
class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    VIOLATION = "violation"

@dataclass(frozen=True)
class AuditIssue:
    code: str
    severity: Severity
    message: str
    step_index: int | None
    target_files: tuple[str, ...] = ()

@dataclass(frozen=True)
class StepAudit:
    step_index: int
    target_files: tuple[str, ...]
    issues: tuple[AuditIssue, ...]
    is_violation: bool
```

`AuditReport` contains:

- all five counters, including `calibration_markers`;
- `code_write_files` in encounter order;
- `violating_steps` as `tuple[StepAudit, ...]`;
- `declaration_drift` as step-scoped issue groups;
- aggregate warnings as `tuple[AuditIssue, ...]`;
- `to_legacy_metrics()` for the exact current dictionary projection;
- `exit_code(strict: bool)`, calculated after warning policy is known.

Do not store a single unconditional `exit_code` in the policy result. The same
warning-only report exits 0 normally and 1 under `--strict`.

Operational failures are represented by a typed `AuditOperationalError`, not by
a partially populated `AuditReport`. The compatibility CLI catches that type,
formats the appropriate diagnostic, and returns 2.

Individual causes and violating steps are separate concepts. Multiple
`AuditIssue` values in one step still produce one legacy violation entry.

### 6.1 Stable machine rule IDs

Use existing emitted codes unchanged where they exist:

- `DEC-01` — declaration worker/model drift;
- `DEC-02` — declaration effort drift;
- `DEC-04` — missing model or effort calibration;
- `DEC-05` — calibration HMAC failure;
- `LOG-01` — unknown mutation-like tool.

Add serialization-only IDs for previously uncoded outcomes without changing
legacy console prose:

- `ROUTE-01` — source-code write without same-step worker credit;
- `CMD-01` — unsafe or non-allowlisted command mutation;
- `WARN-01` — code writes exceed worker calls;
- `WARN-02` — writes occurred without a routing declaration.

Keep the finer existing CMD detection taxonomy in internal properties when
available; do not rename an emitted existing code merely to fit this list.

## 7. Engine and compatibility facades

### 7.1 Construction

```python
RoutingAuditEngine(
    config_path: Path | None = None,
    root_dir: Path | None = None,
)
```

- Default config is the current script-sibling `routing-config.json`.
- A relative explicit config path is resolved from the caller’s current
  directory.
- Default `root_dir` is a snapshot of `Path.cwd()` at construction.
- Never infer root from the log path; wrapper logs live outside the audited
  workspace.
- Load and validate configuration once per engine instance.
- Add `RoutingAuditEngine.from_config(config, *, root_dir=None)` as the internal
  compatibility constructor used by legacy `run_audit()`. It receives the
  already-loaded mapping, makes a defensive read-only copy, and is mutually
  exclusive with `config_path`; it does not reload the sibling config.

### 7.2 Audit flow

`audit_log(log_path: Path) -> AuditReport`:

1. read with the current UTF-8 and replacement behavior;
2. reject missing, unreadable, empty input with a typed operational error;
3. select the parser with current content/suffix precedence;
4. reject malformed or zero-step results;
5. run the existing raw-text parser-drift cross-check;
6. snapshot calibration secret once if any evidence exists;
7. evaluate every step without cross-step worker or calibration leakage;
8. build one immutable report.

The CLI translates typed operational errors to exit 2 and preserves the default
text diagnostics. Legacy `run_audit()` retains its current print-and-return
contract by calling `RoutingAuditEngine.from_config()` with its supplied config
and delegating.

`parse_steps()` and `compute_metrics()` delegate to the corresponding parser and
policy components, then convert back to existing mutable `Step` and dictionary
shapes where required.

## 8. HMAC integration

### 8.1 Public shared primitive

Add a pure function in `agent_council.py`:

```python
verify_calibration_signature(
    manifest: Mapping[str, Any],
    secret: bytes,
) -> bool
```

It must:

- require exactly the six signed values to be strings;
- canonicalize those six values with `sort_keys=True` and
  `separators=(",", ":")`, encoded as UTF-8;
- require a 64-hex-character signature;
- accept uppercase or lowercase hex input;
- compare decoded 32-byte digests with `hmac.compare_digest`;
- ignore unrelated v1 fields;
- never log or return secret/digest material.

Reuse the same canonical-payload helper in
`generate_calibration_signature()`. Keep signer output lowercase.

Versionless manifests are calibration v1. Continue to ignore every unrelated v1
field, even if it is named `schema_version` or `algorithm`; interpreting those
names now would be a compatibility change. Do not emit, dispatch, or partially
support v2 in this work. A future v2 requires a separately approved,
unambiguous envelope/discriminator, domain separation, and signatures over
every semantically trusted field.

JSON objects with duplicate keys retain Python’s current last-value parsing
semantics in this compatibility refactor. Add a fixed regression test showing
which value is authenticated, and document that a future strict manifest schema
may reject duplicate signed keys. Do not silently introduce a second JSON
decoder policy only for calibration evidence.

### 8.2 Evidence semantics

Preserve:

- no header and no manifest: no calibration issue and no key lookup;
- a valid manifest without a header: accepted;
- a header: requires valid same-step manifest evidence with that signature;
- signature-only objects: invalid;
- any malformed manifest, malformed header, mismatch, or invalid duplicate:
  the step fails;
- at most one `DEC-05 HMAC calibration signature mismatch` per step;
- extra council fields are not presented as authenticated by v1.

### 8.3 Secret retrieval and file safety

- Environment `AGY_CALIBRATION_SECRET` has precedence.
- Otherwise read `<resolved-root>/.ralph/cache/calibration.key`.
- Verification always uses `create=False` and never creates directories, keys,
  manifests, or cache files.
- Missing, empty, unreadable, non-regular, symlinked, or oversized key material
  with calibration evidence yields DEC-05, not exit 2.
- Read a workspace key through one descriptor, using `O_NOFOLLOW` where
  available plus `fstat` regular-file and bounded-size checks. Document the
  platform fallback.
- Load the secret once per audit and pass that snapshot to every step.
- Preserve `generate_calibration_signature(secret=None)` failing closed when no
  environment secret or council workspace is available.
- Continue creating new council keys atomically with exclusive mode `0600`.
- Do not reject an existing regular key solely for permissive mode in this
  compatibility refactor. Emit no new warning until a migration policy exists.

Rejecting symlinked, non-regular, and oversized workspace key files is an
intentional security exception to legacy `Path.read_bytes()` behavior. Set and
document a conservative byte limit that safely exceeds generated and supported
user-provided keys; apply it only to workspace files, not the environment
secret. On rejection, calibration evidence yields one DEC-05 and no state is
created. Add release-note text to README so this change is not mistaken for
accidental drift.

## 9. CLI contract

### 9.1 Python entry point

```text
routing_check.py [--strict]
                 [--format {text,json,sarif}]
                 [--config PATH]
                 [--root-dir PATH]
                 [--]
                 LOG_FILE
```

Rules:

- options may precede or follow `LOG_FILE`;
- repeated `--strict` is idempotent;
- `--help` prints help to stdout and exits 0;
- unknown flags, missing flag values, missing log, or extra positionals print a
  concise usage diagnostic and exit 2;
- `--` permits a dash-prefixed log filename;
- default format is `text`;
- default config is script-relative;
- default root is the captured current directory;
- `strict` affects verdict exit status, not issue detection.

Use a parser wrapper if needed to retain the capitalized legacy `Usage:` line
and stream placement for the no-argument case. Do not let an incidental
`argparse` default alter valid legacy output.

### 9.2 Exit and stream matrix

| Audit outcome | normal | strict |
|---|---:|---:|
| clean | 0 | 0 |
| warning only | 0 | 1 |
| violation, including DEC-05 | 1 | 1 |
| CLI/config/read/parse/parser-drift failure | 2 | 2 |

For a completed JSON or SARIF audit:

- stdout contains exactly one document and one trailing newline;
- stderr is empty;
- issues and step details are embedded in the document;
- no banner, emoji prose, secret, key path contents, or signature digest appears.

For machine-mode operational failure:

- stdout is empty;
- one sanitized diagnostic is written to stderr;
- exit is 2.

## 10. Output formats

### 10.1 JSON schema version 1

Top-level keys:

```json
{
  "schemaVersion": 1,
  "tool": "routing-audit",
  "verdict": "clean|warning|violation",
  "strict": false,
  "exitCode": 0,
  "metrics": {},
  "violatingSteps": [],
  "issues": [],
  "warnings": []
}
```

`metrics` includes all legacy metric names. Tuple values serialize as arrays.
Each issue contains `code`, `severity`, `message`, nullable `stepIndex`, and
`targetFiles`. Sort object keys; order issues by step, severity/rule, and
discovery ordinal so repeated runs over identical input are byte-identical.

### 10.2 SARIF 2.1.0

Emit:

- `$schema` and `version: "2.1.0"`;
- one run with a stable tool driver and rule descriptors for every emitted ID;
- one result per issue, with `error`, `warning`, or `note` level;
- `properties.stepIndex` where applicable;
- artifact locations for target files without fabricating source line numbers;
- one invocation with `executionSuccessful: true` for every completed audit and
  the actual exit code.

Artifact URI conversion must be host-independent and must not query the
filesystem:

- retain the exact input target in `location.properties.originalTarget`;
- encode Unicode as UTF-8 and percent-encode spaces, percent signs, and other
  URI-unsafe bytes;
- retain `/` as the separator for relative POSIX-style targets without
  resolving `.`, `..`, or `~`;
- convert `\` to `/` only in the SARIF URI projection for recognized Windows
  drive or UNC forms, while retaining the original string in properties;
- encode absolute POSIX paths as `file:///...`, Windows drive paths as
  `file:///C:/...`, and UNC paths as `file://host/share/...`;
- leave other relative targets as relative URIs and do not attach a fabricated
  base URI.

Use one tested conversion helper so absolute, relative, spaced, Unicode, tilde,
Windows-style, and duplicate targets serialize deterministically.

A policy violation is a successfully executed audit even when its process exit
is 1. Do not emit a partial SARIF document for an operational exit-2 failure.

## 11. Shell-wrapper contract

Extend the wrapper syntax to:

```text
routing-audit.sh [--strict]
                 [--format {text,json,sarif}]
                 [--config PATH]
                 [--root-dir PATH]
                 [conversation-id]
```

Implement a deterministic `while`/`case` parser:

- accept options in any supported order;
- reject unknown options, missing option values, or multiple conversation IDs
  with exit 2;
- preserve explicit-ID and latest-conversation selection;
- preserve `overview.txt` precedence over `transcript.jsonl`;
- quote every relayed value and return the Python child’s exact exit code;
- print the current audit banner unchanged in text mode;
- suppress the banner in JSON and SARIF modes;
- keep legacy text errors on their existing stream, but use stderr with empty
  stdout for machine-mode discovery/argument failures.

## 12. Ordered implementation phases

### Phase 0 — Characterize before refactoring

1. Preserve the user’s current uncommitted test additions.
2. Register both dynamically loaded modules (`routing_check` and
   `agent_council`) in `sys.modules` before `exec_module()` in the test loader;
   remove the corresponding entry if execution fails.
3. Capture exact stdout, stderr, and exit results for clean, warning,
   strict-warning, violation, missing, empty, malformed, and parser-drift cases.
4. Add engine-independent expectations for every existing fixture.

Gate: all 68 baseline tests plus characterization tests pass.

### Phase 1 — Introduce domain models and engine seams

1. Add immutable report types with tuple fields.
2. Move existing parsing bodies behind the two parser classes without changing
   recovery rules.
3. Move policy calculations behind `PolicyEvaluator`.
4. Build `RoutingAuditEngine` and a lossless legacy metrics projection.
5. Convert existing functions into thin compatibility facades.
6. Keep `ConsoleReportFormatter` behavior identical to current `run_audit()`.

Gate: every fixture produces equal legacy metrics and exact text output before
and after delegation.

### Phase 2 — Unify HMAC verification

1. Extract shared canonicalization in `agent_council.py`.
2. Add the pure verifier and safe read-only key path.
3. Inject one secret snapshot into the audit policy.
4. Preserve DEC-05 message, evidence rules, and signer behavior.
5. Add fixed cross-module canonical vectors before deleting duplicated verifier
   code from `routing_check.py`.

Gate: all old and new calibration tests pass with no workspace state created by
verification.

### Phase 3 — Add CLI and machine formatters

1. Add explicit CLI parsing and root/config overrides.
2. Add deterministic JSON formatter.
3. Add SARIF formatter and stable rules.
4. Enforce the stream/exit matrix.

Gate: text golden tests are unchanged; JSON/SARIF parse and are deterministic.

### Phase 4 — Wrapper and documentation

1. Extend `routing-audit.sh` parsing and relay.
2. Suppress machine-mode banners and sanitize errors.
3. Update README examples, schemas, root behavior, exits, and v1 trust boundary.

Gate: wrapper integration tests pass for explicit/latest conversation and every
supported flag.

### Phase 5 — Zero-defect verification

Run all quality gates below, inspect the diff for scope, and route the
implementation through an independent high-effort code review.

## 13. Required test matrix

### Legacy and equivalence

- Keep all 68 current tests and their assertions.
- Preserve the uncommitted protocol-documentation and GEMINI sync tests.
- Golden-test exact text stdout/stderr/exits for clean, warning, strict warning,
  violation, missing, empty, malformed, and parser-drift logs.
- Compare engine reports converted through `to_legacy_metrics()` with current
  expected dictionaries for every fixture.
- Cover duplicate basenames, uppercase/exact suffixes, Unicode and spaced paths,
  and zero-target command violations.

### Parser and policy

- leading-`{` content versus suffix detection;
- blank lines and malformed/non-object JSONL values;
- multiple text headers and misleading literal step numbers;
- direct and Antigravity tool-call shapes;
- prose-only worker mentions;
- same-step versus cross-step worker calls;
- nested shell, newline, background, redirect, backtick, and substitution tails;
- suppression across every segment of one original command;
- unknown mutation-like tools.

### HMAC

- fixed canonical vectors with Unicode, escaping, and reordered object keys;
- duplicate signed JSON keys, locking the current last-value interpretation;
- missing, wrong-type, or mutated value for each of six signed fields;
- lower/uppercase, non-hex, wrong-length, and mismatched signatures;
- manifest-only, header-plus-manifest, header-only, signature-only, duplicate,
  mixed-validity, and cross-step evidence;
- versionless v1 plus proof that unrelated extra fields, including version-like
  names, retain current ignored-field behavior;
- environment precedence over workspace key;
- explicit root and default captured-CWD root;
- missing, empty, unreadable, symlink, non-regular, and oversized key;
- no key or directory creation during verification;
- one key snapshot reused across multiple steps;
- standalone signing without a secret;
- council-generated manifest verified by the audit helper;
- no secret or digest in text, JSON, SARIF, exceptions, or tracebacks.

### CLI, formats, and wrapper

- `--strict` before/after path and repeated;
- help, missing args, unknown args, missing option values, extra positionals,
  `--`, relative/absolute/dash-prefixed paths, spaces, and Unicode;
- default/explicit valid config plus missing, malformed JSON, invalid regex, and
  invalid schema;
- text/JSON/SARIF crossed with clean/warning/violation/strict;
- JSON schema keys, value types, ordering, one trailing newline, and repeatable
  bytes;
- SARIF version, rules, levels, results, artifact URIs, step properties, and
  invocation status;
- empty machine stdout on every operational failure;
- wrapper explicit/latest ID, overview precedence, strict relay, root/config
  relay, machine banner suppression, unknown/multiple args, and exact exit relay.

## 14. Quality gates

Run from the repository root:

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest skills/worker-routing/test_routing.py -v`
2. `shellcheck skills/worker-routing/routing-audit.sh` when ShellCheck is
   available; absence must be reported, not treated as a pass.
3. Compile both Python sources with bytecode directed to a temporary directory,
   not the repository.
4. Parse every JSON/SARIF fixture with Python’s JSON decoder and assert the
   specified schema contracts.
5. Run deterministic-output cases twice and compare exact bytes.
6. Inspect `git diff --check`.
7. Inspect `git status --short` and confirm implementation changes are limited to
   the five scoped files while preserving all pre-existing user changes.
8. Obtain an independent high-effort code review of the final diff.

## 15. Risks and rollback

| Risk | Mitigation |
|---|---|
| helper import breakage | compatibility facades and unchanged-signature tests |
| dataclass import failure | register dynamic test module before execution |
| changed console output | byte-level golden tests before moving code |
| issue-count inflation | separate `AuditIssue` from `StepAudit` |
| machine stdout corruption | formatter/wrapper stream integration tests |
| signer/verifier drift | one canonical helper and fixed cross-module vectors |
| key rotation mid-audit | one secret snapshot per audit |
| key-file race or symlink | descriptor-based bounded regular-file read |
| accidental installer expansion | no new runtime module; hard scope gate |
| user changes overwritten | review diff against the initial dirty-worktree list |

Rollback is low-risk because existing entry points remain facades. The engine,
formatters, and wrapper options can be reverted without a data migration.
Calibration v1 remains readable and byte-compatible throughout.

## 16. Definition of done

Implementation is done only when:

- all pre-existing and new tests pass;
- legacy valid invocations have identical text stdout, stderr, and exits;
- all directly imported names and legacy metric shapes remain compatible;
- JSON and SARIF are deterministic, valid, uncontaminated, and redacted;
- wrapper behavior is compatible in text mode and correct in machine modes;
- calibration v1 cross-verifies with `agent_council.py`;
- audit verification performs no filesystem writes;
- one audit uses one captured root, config, and secret snapshot;
- no new Python module or installer change was introduced;
- README matches actual CLI and schema behavior;
- diff scope and existing user changes are preserved;
- an independent reviewer finds no unresolved blocking or major issue.
