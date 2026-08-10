#!/usr/bin/env python3
"""
routing_check.py — the routing audit engine.

Modes:
  --strict               Treat warnings as failures too (exit 1 instead of
                          0 when a 🟡 WARNING is emitted but no 🔴 VIOLATION
                          is found).
  <log_file>              Full audit: parses the log, computes every routing
                          metric strictly within each conversation step's own
                          boundaries, prints a human-readable report to
                          stdout, and exits with the audit verdict.
                          Per-violation detail lines go to stderr.

Log formats:
  *.txt / anything else   Plain text, split on `Step \\d+:` markers — unless
                           the stripped content starts with `{`, in which
                           case it's treated as JSON Lines regardless of
                           extension (Antigravity's own `overview.txt` logs
                           are written this way).
  *.jsonl                 JSON Lines: one step object per line.

  Each JSON Lines step object looks like:
    {"routing": "[ROUTING: Direct — reason: ...]",
     "tool_calls": [{"tool": "run_command", "command_line": "..."},
                    {"tool": "replace_file_content", "target_file": "..."}]}

  Antigravity's own conversation logs use a slightly different shape: the
  tool name is under `name` instead of `tool`, arguments are nested under
  `args` as `TargetFile`/`CommandLine` (sometimes wrapped in literal double
  quotes), and the `[ROUTING: ...]` declaration is embedded in a separate
  step's free-form `content` rather than a dedicated `routing` key.
  `_step_from_dict` accepts both shapes.

Worker-CLI detection only ever looks at the `CommandLine`/`command_line`
value of an actual `run_command` tool call — never at surrounding prose —
so a conversational mention of a worker's name can't be mistaken for routing.
`is_worker_invocation` strips leading environment variable assignments
(e.g. `IN_WORKER_ROUTING=true`) and known wrappers (`script -q /dev/null`,
`bash -c`) before checking whether the command *starts with* a configured
worker pattern — a substring mention mid-command (e.g. `echo codex exec`)
does not count.

Commands that are not worker invocations are checked against
`safe_commands` in routing-config.json via `is_command_safe`. A command
that is neither a worker invocation nor a recognized safe command is an
`unrouted_mutation` and flags its step as a violation even if it wrote no
code files directly (e.g. a redirect, backtick, or `$()` substitution that
could mutate state outside the tracked write tools).

Code-file detection uses `Path(filename).suffix` for an exact extension
match, so `.html` can't be mistaken for `.h`, `package.json` for `.js`, or
`.pyc` for `.py`.

A step that writes a source code file with zero worker CLI calls of its own
is a violation — regardless of what (if anything) its `[ROUTING:]` label
says. A `[ROUTING: heavy_doer ...]` label doesn't excuse an unrouted write
any more than `[ROUTING: Direct ...]` does; only an actual worker `run_command`
call in that same step does.

Exit codes:
  0   Audit ran, no violations (and, in --strict mode, no warnings either).
  1   Audit ran, violations found (or, in --strict mode, warnings found).
  2   The audit itself could not run — missing/unreadable log file, an
      empty log, a log that failed to parse or yielded no steps, a
      routing-config.json that failed to load, or a raw-text cross-check
      that suggests the parser is out of sync with the log format. Fails
      closed rather than silently treating an unreadable/unparseable log
      as clean.
"""
from __future__ import annotations

import fcntl
import json
import re
import shlex
import sys
import traceback
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DIFF_FILE_RE = re.compile(r"^\+\+\+\s+b/(.+)$", re.MULTILINE)


def audit_spec_vs_diff(spec_text: str, diff_text: str) -> list[str]:
    """Parse git diff and flag any modified files that are absent from approved spec components."""
    modified_files = DIFF_FILE_RE.findall(diff_text)
    unapproved_files: list[str] = []
    for filepath in modified_files:
        filepath_clean = filepath.strip()
        if filepath_clean not in spec_text and Path(filepath_clean).name not in spec_text:
            unapproved_files.append(filepath_clean)
    return unapproved_files


def parse_command_tokens(command: str) -> list[str]:
    """Safely tokenize command line arguments using shlex with fallback."""
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def append_jsonl_locked(file_path: str | Path, record: dict[str, Any]) -> None:
    """Safely append a JSON record using advisory file locking (fcntl)."""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class AuditConfig:
    worker_patterns: list[re.Pattern[str]]
    safe_patterns: list[re.Pattern[str]]
    code_extensions: list[str]


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    message: str
    discovery_ordinal: int

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warning"}:
            raise ValueError("severity must be 'error' or 'warning'")

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AuditIssue):
            return NotImplemented
        severity_rank = {"error": 0, "warning": 1}
        return (severity_rank[self.severity], self.discovery_ordinal) < (
            severity_rank[other.severity],
            other.discovery_ordinal,
        )


@dataclass(frozen=True)
class AuditResult:
    issues: list[AuditIssue]
    worker_calls: int
    has_worker_calls: bool
    total_steps: int

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def warnings(self) -> list[AuditIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def errors(self) -> list[AuditIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]


@dataclass(frozen=True)
class AuditReport:
    total_writes: int
    code_writes: int
    routing_declarations: int
    worker_calls: int
    violations: list[tuple[int, list[str]]]
    declaration_drift: list[tuple[int, list[str]]]
    violation_details: list[tuple[int, list[str]]]
    calibration_markers: int
    code_write_files: list[str]
    exit_code: int


SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "routing-config.json"

DEFAULT_CODE_EXTENSIONS = ["ts", "tsx", "css", "js", "jsx"]

WRITE_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}

STEP_HEADER_RE = re.compile(r"^Step\s+\d+\s*:", re.MULTILINE)
ROUTING_RE = re.compile(r"\[ROUTING:[^\]\n]*\]")
CALIBRATION_RE = re.compile(r"\[CALIBRATION:\s*([^\]\n]+)\]", re.IGNORECASE)
ROUTING_LABEL_RE = re.compile(
    r"\[ROUTING:\s*(?P<worker>.+?)\s+—\s*complexity:\s*"
    r"(?P<complexity>trivial|simple|medium|complex)\s+—\s*effort:\s*"
    r"(?P<effort>low|medium|high|ultra)\s+—\s*reason:\s*(?P<reason>[^\]\n]+)\]",
    re.IGNORECASE,
)
TOOL_CALL_RE = re.compile(r"Tool call:\s*(\w+)\(")

# Non-role keys that may appear at the top level of routing-config.json
# alongside the worker-role dicts.
NON_ROLE_CONFIG_KEYS = {"code_extensions", "safe_commands", "orchestrator"}

ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S+\s+")
SCRIPT_WRAPPER_RE = re.compile(r"^script\s+(?:-\S+\s+)*\S+\s+")
BASH_C_WRAPPER_RE = re.compile(r"^bash\s+-c\s+")

UNSAFE_SUBSTRINGS = (">", "`", "$(")
MODEL_RE = re.compile(
    r"(?:^|\s)(?:--model(?:=|\s+)|-c\s+model\s*=\s*)[\"']?(?P<model>[^\s'\"]+)",
    re.IGNORECASE,
)
EFFORT_RE = re.compile(
    r"model_reasoning_effort\s*=\s*[\"']?(?P<effort>low|medium|high|ultra)[\"']?",
    re.IGNORECASE,
)
CALIBRATION_FIELDS = ("task_id", "task", "complexity", "effort", "decision", "nonce")
CALIBRATION_VIOLATION = "DEC-05 HMAC calibration signature mismatch"
HMAC_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _kv_pattern(key: str) -> re.Pattern[str]:
    return re.compile(r'"' + key + r'"\s*:\s*"((?:[^"\\]|\\.)*)"')


TARGET_FILE_RE = _kv_pattern("TargetFile")
COMMAND_LINE_RE = _kv_pattern("CommandLine")


def load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def load_patterns(config: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for key, role in config.items():
        if key in NON_ROLE_CONFIG_KEYS or not isinstance(role, dict):
            continue
        patterns.extend(role.get("patterns", []))
    return patterns


def load_code_extensions(config: dict[str, Any]) -> list[str]:
    return config.get("code_extensions", DEFAULT_CODE_EXTENSIONS)  # type: ignore[no-any-return]


def load_safe_patterns(config: dict[str, Any]) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in config.get("safe_commands", [])]


def _strip_command_wrappers(command: str) -> str:
    """Strip leading environment variable assignments and known wrapper
    commands (`script -q /dev/null ...`, `bash -c ...`) so the underlying
    invocation is what gets matched against worker patterns."""
    stripped = command.strip()
    while True:
        without_env = ENV_ASSIGNMENT_RE.sub("", stripped)
        if without_env != stripped:
            stripped = without_env
            continue

        script_match = SCRIPT_WRAPPER_RE.match(stripped)
        if script_match:
            stripped = stripped[script_match.end():]
            continue

        bash_c_match = BASH_C_WRAPPER_RE.match(stripped)
        if bash_c_match:
            stripped = stripped[bash_c_match.end():].strip()
            if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in "\"'":
                stripped = stripped[1:-1]
            continue

        break

    return stripped.strip()


def is_worker_invocation(command: str, worker_patterns: Sequence[str | re.Pattern[str]]) -> bool:
    """True if `command`, once stripped of leading env assignments and
    known wrappers, actually matches a configured worker pattern."""
    stripped = _strip_command_wrappers(command)
    for pattern in worker_patterns:
        if isinstance(pattern, re.Pattern):
            if pattern.match(stripped):
                return True
        else:
            if stripped == pattern or stripped.startswith((pattern + " ", pattern + "\t")):
                return True
    return False


def split_command_segments(command: str) -> list[str]:
    """Split `&&`, `||`, `|`, and `;` only outside quoted text (CMD-01)."""
    parts: list[str] = []
    start = index = 0
    quote: str | None = None
    escaped = False
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in ";&|\r\n":
            parts.append(command[start:index].strip())
            index += 2 if command[index:index + 2] in {"&&", "||"} else 1
            start = index
            continue
        index += 1
    parts.append(command[start:].strip())
    return parts


def _bash_c_payload(command: str) -> str | None:
    """Return an explicitly quoted ``bash -c`` payload, if this is one.

    ``bash -c 'worker && unsafe'`` is a nested shell program; treating it as
    one opaque worker segment would let its unsafe tail evade CMD-01.
    """
    stripped = command.strip()
    while True:
        without_env = ENV_ASSIGNMENT_RE.sub("", stripped)
        if without_env != stripped:
            stripped = without_env
            continue
        script_match = SCRIPT_WRAPPER_RE.match(stripped)
        if script_match:
            stripped = stripped[script_match.end():]
            continue
        break
    match = BASH_C_WRAPPER_RE.match(stripped)
    if not match:
        return None
    payload = stripped[match.end():].strip()
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in "'\"":
        return payload[1:-1]
    return payload


def command_segments(command: str, depth: int = 0) -> list[str]:
    """Return logical segments, recursively unpacking quoted ``bash -c``."""
    if depth > 8:  # Fail closed on wrapper recursion rather than guessing.
        return [command]
    segments: list[str] = []
    for segment in split_command_segments(command):
        payload = _bash_c_payload(segment)
        if payload is None:
            segments.append(segment)
        else:
            segments.extend(command_segments(payload, depth + 1))
    return segments


def is_command_safe(command: str, safe_patterns: list[re.Pattern[str]]) -> bool:
    """True if `command` contains no redirect/substitution shell metacharacters
    and every `||`/`&&`/`|`/`;`-separated part matches a configured safe
    pattern."""
    if any(token in command for token in UNSAFE_SUBSTRINGS):
        return False

    parts = command_segments(command)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not any(pattern.match(part) for pattern in safe_patterns):
            return False
    return True


def structural_binding_issues(
    routing_str: str | None,
    commands: list[str],
    worker_patterns: Sequence[str | re.Pattern[str]],
) -> list[str]:
    """Bind a complete declaration to each actual worker segment (DEC-01..04)."""
    if not routing_str:
        return []
    declaration = ROUTING_LABEL_RE.search(routing_str)
    if not declaration:
        return []
    declared_worker = declaration.group("worker").strip().lower()
    declared_effort = declaration.group("effort").lower()
    issues: list[str] = []
    for command in commands:
        # CMD-02/CMD-04 own unsafe shell syntax; do not let it participate in
        # structural validation as if it were an unmodified worker call.
        if any(marker in command for marker in UNSAFE_SUBSTRINGS):
            continue
        for segment in command_segments(command):
            if not segment or not is_worker_invocation(segment, worker_patterns):
                continue
            model_match = MODEL_RE.search(segment)
            effort_match = EFFORT_RE.search(segment)
            executable = _strip_command_wrappers(segment).split(maxsplit=1)[0].lower()
            # agy and codex review are valid protocol calls without the
            # execution calibration flags.  Codex exec/Claude execution must
            # bind both flags whenever a full declaration is present.
            requires_flags = (
                executable == "codex"
                and _strip_command_wrappers(segment).startswith("codex exec")
                or executable == "claude"
            )
            if requires_flags and (not model_match or not effort_match):
                issues.append("DEC-04 missing --model or model_reasoning_effort")
                continue
            if not requires_flags and not model_match and not effort_match:
                continue
            model = model_match.group("model").lower() if model_match else ""
            actual_effort = effort_match.group("effort").lower() if effort_match else None
            expected_tier = next(
                (tier for tier in ("sol", "terra", "luna") if tier in declared_worker),
                None,
            )
            if "codex" in declared_worker or expected_tier:
                worker_matches = (
                    executable == "codex"
                    and (expected_tier is None or expected_tier in model)
                )
            elif any(
                name in declared_worker
                for name in (
                    "claude",
                    "sonnet",
                    "opus",
                    "fable",
                    "heavy_doer",
                    "planner",
                )
            ):
                family = next(
                    (
                        name
                        for name in ("sonnet", "opus", "fable")
                        if name in declared_worker
                    ),
                    None,
                )
                ver_match = re.search(r"\b\d+(?:\.\d+)?\b", declared_worker)
                expected_ver = ver_match.group(0) if ver_match else None
                worker_matches = (
                    executable == "claude"
                    and (family is None or family in model)
                    and (expected_ver is None or expected_ver in model)
                )
            elif any(name in declared_worker for name in ("agy", "gemini", "context_specialist")):
                worker_matches = executable == "agy"
            else:
                worker_matches = False
            if not worker_matches:
                issues.append("DEC-01 declaration worker/model drift")
            if actual_effort is not None and actual_effort != declared_effort:
                issues.append("DEC-02 declaration effort drift")
    return issues


class Step:
    """One logical unit of a conversation log."""

    __slots__ = (
        "calibration_headers",
        "calibration_manifests",
        "commands",
        "index",
        "routing",
        "unknown_write_tools",
        "writes",
    )

    index: int
    routing: str | None
    writes: list[str]
    commands: list[str]
    unknown_write_tools: list[str]
    calibration_headers: list[str]
    calibration_manifests: list[dict[str, Any]]

    def __init__(self, index: int, routing: str | None = None) -> None:
        self.index = index
        self.routing = routing
        self.writes = []  # TargetFile strings from write-tool calls
        self.commands = []  # CommandLine strings from run_command calls
        self.unknown_write_tools = []  # unapproved mutating tool names (LOG-01)
        self.calibration_headers = []
        self.calibration_manifests = []


def _collect_calibration_manifests(value: Any) -> list[dict[str, Any]]:
    """Collect every signature-bearing object without trusting its shape."""
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "signature" in value:
            found.append(value)
        for nested in value.values():
            found.extend(_collect_calibration_manifests(nested))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_collect_calibration_manifests(nested))
    return found


def _embedded_json_values(text: str) -> list[Any]:
    """Decode JSON values embedded in ordinary transcript content."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    index = 0
    while True:
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        values.append(value)
        index = start + consumed
    return values


def _add_calibration_text(step: Step, text: str) -> None:
    step.calibration_headers.extend(
        match.group(1).strip() for match in CALIBRATION_RE.finditer(text)
    )
    for value in _embedded_json_values(text):
        step.calibration_manifests.extend(_collect_calibration_manifests(value))


def get_calibration_secret(root_dir: str | Path | None = None) -> bytes | None:
    """Read the verifier secret without creating or modifying project state."""
    # Keep this import lazy: routing_check.py is loaded directly by path in
    # standalone audit contexts, before its sibling module is on sys.path.
    from agent_council import AgentCouncil

    try:
        return AgentCouncil.load_secret(root_dir=root_dir, read_only=True)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class SecurityContext:
    """Encapsulates calibration secret resolution and HMAC verification."""

    root_dir: str | Path | None
    secret: bytes | None

    @classmethod
    def create(
        cls, root_dir: str | Path | None = None, secret: bytes | None = None
    ) -> SecurityContext:
        """Resolve the verifier secret before constructing an immutable context."""
        return cls(
            root_dir=root_dir,
            secret=secret if secret is not None else get_calibration_secret(root_dir),
        )

    def verify_manifest(self, manifest: dict[str, Any]) -> bool:
        if self.secret is None:
            return False
        # See get_calibration_secret: direct-path execution needs this sibling
        # import to remain lazy until the caller has installed it.
        from agent_council import AgentCouncil

        return AgentCouncil.verify_signature(
            manifest, secret=self.secret, root_dir=self.root_dir
        )

    def validate_step(self, step: Step) -> str | None:
        if not step.calibration_headers and not step.calibration_manifests:
            return None
        if self.secret is None:
            return CALIBRATION_VIOLATION

        validation_by_signature: dict[str, bool] = {}
        invalid = False
        for manifest in step.calibration_manifests:
            signature = manifest.get("signature")
            valid = self.verify_manifest(manifest)
            if isinstance(signature, str):
                previous = validation_by_signature.get(signature.lower(), True)
                validation_by_signature[signature.lower()] = previous and valid
            if not valid:
                invalid = True

        for header in step.calibration_headers:
            normalized = header.lower()
            if (
                HMAC_SHA256_RE.fullmatch(header) is None
                or not validation_by_signature.get(normalized, False)
            ):
                invalid = True
        return CALIBRATION_VIOLATION if invalid else None


@dataclass(frozen=True)
class StepAnalysis:
    """Isolated policy evaluation metrics and issues for one step."""

    index: int
    has_routing_declaration: bool
    worker_calls: int
    has_unrouted_mutation: bool
    issues: list[str]
    code_writes: list[str]
    total_writes: int
    calibration_markers: int

    @property
    def has_violations(self) -> bool:
        return (
            self.has_unrouted_mutation
            or bool(self.issues)
            or (bool(self.code_writes) and self.worker_calls == 0)
        )


def _canonical_calibration_payload(manifest: dict[str, Any]) -> bytes | None:
    payload: dict[str, str] = {}
    for field in CALIBRATION_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str):
            return None
        payload[field] = value
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def calibration_signature_issue(step: Step, security_ctx: SecurityContext) -> str | None:
    """Verify all same-step headers/evidence and return DEC-05 at most once."""
    return security_ctx.validate_step(step)


def is_unknown_write_tool(tool_name: Any) -> bool:
    """Fail closed for unapproved tools whose name represents a file mutation."""
    if not isinstance(tool_name, str) or tool_name in WRITE_TOOLS:
        return False
    lowered = tool_name.lower()
    mutation_terms = ("write", "edit", "replace", "patch", "apply")
    return any(term in lowered for term in mutation_terms)


def _parse_text_steps(text: str) -> list[Step]:
    headers = list(STEP_HEADER_RE.finditer(text))
    steps: list[Step] = []

    for i, header in enumerate(headers):
        start = header.start()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        chunk = text[start:end]

        routing_match = ROUTING_RE.search(chunk)
        step = Step(i + 1, routing_match.group(0) if routing_match else None)
        _add_calibration_text(step, chunk)

        calls = list(TOOL_CALL_RE.finditer(chunk))
        for j, call in enumerate(calls):
            tool_name = call.group(1)
            seg_start = call.end()
            seg_end = calls[j + 1].start() if j + 1 < len(calls) else len(chunk)
            segment = chunk[seg_start:seg_end]

            if tool_name in WRITE_TOOLS:
                m = TARGET_FILE_RE.search(segment)
                if m:
                    step.writes.append(m.group(1))
            elif tool_name == "run_command":
                m = COMMAND_LINE_RE.search(segment)
                if m:
                    step.commands.append(m.group(1))
            elif is_unknown_write_tool(tool_name):
                step.unknown_write_tools.append(tool_name)

        steps.append(step)

    return steps


def _dig(data: dict[str, Any], *path: str) -> Any:
    value: Any = data
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _strip_quotes(value: Any) -> Any:
    return value.strip('"') if isinstance(value, str) else value


def _step_from_dict(index: int, data: dict[str, Any]) -> Step:
    routing = data.get("routing")
    if "routing" not in data:
        content = data.get("content")
        if isinstance(content, str):
            match = ROUTING_RE.search(content)
            routing = match.group(0) if match else None

    step = Step(index, routing)
    step.calibration_manifests.extend(_collect_calibration_manifests(data))
    for value in data.values():
        if isinstance(value, str):
            _add_calibration_text(step, value)
    for call in data.get("tool_calls") or []:
        tool_name = call.get("tool") or call.get("name")
        target_file = _strip_quotes(call.get("target_file") or _dig(call, "args", "TargetFile"))
        command_line = _strip_quotes(call.get("command_line") or _dig(call, "args", "CommandLine"))

        if tool_name in WRITE_TOOLS and target_file:
            step.writes.append(target_file)
        elif tool_name == "run_command" and command_line:
            step.commands.append(command_line)
        elif is_unknown_write_tool(tool_name):
            step.unknown_write_tools.append(str(tool_name))
    return step


def _parse_jsonl_steps(text: str) -> list[Step]:
    steps: list[Step] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        steps.append(_step_from_dict(len(steps) + 1, json.loads(line)))
    return steps


def parse_steps(log_file: str, text: str) -> list[Step]:
    if text.strip().startswith("{"):
        return _parse_jsonl_steps(text)
    if Path(log_file).suffix.lower() == ".jsonl":
        return _parse_jsonl_steps(text)
    return _parse_text_steps(text)


def _analyze_step(
    step: Step,
    code_extensions: list[str],
    worker_patterns: Sequence[str | re.Pattern[str]],
    safe_patterns: list[re.Pattern[str]],
    security_ctx: SecurityContext,
) -> StepAnalysis:
    """Analyze a single conversation step in isolation (ADR-0003)."""
    code_ext_set = {e.lower().lstrip(".") for e in code_extensions}

    has_routing_declaration = bool(step.routing)
    step_worker_calls = 0
    step_has_unrouted_mutation = False
    step_issues: list[str] = []

    for command in step.commands:
        command_worker_calls = 0
        command_has_unrouted_mutation = any(
            marker in command for marker in UNSAFE_SUBSTRINGS
        )
        for part in command_segments(command):
            if not part:
                continue
            if is_worker_invocation(part, worker_patterns):
                command_worker_calls += 1
            elif not is_command_safe(part, safe_patterns):
                command_has_unrouted_mutation = True
        if command_has_unrouted_mutation:
            step_has_unrouted_mutation = True
        else:
            step_worker_calls += command_worker_calls

    step_issues.extend(
        structural_binding_issues(step.routing, step.commands, worker_patterns)
    )
    calibration_markers = len(step.calibration_headers) + len(
        step.calibration_manifests
    )

    calibration_issue = security_ctx.validate_step(step)
    if calibration_issue:
        step_issues.append(calibration_issue)

    if step.unknown_write_tools:
        step_issues.append(
            "LOG-01 unknown write tool: " + ", ".join(step.unknown_write_tools)
        )

    step_code_writes: list[str] = []
    total_writes = len(step.writes)
    for target_file in step.writes:
        suffix = Path(target_file).suffix.lower().lstrip(".")
        if suffix in code_ext_set:
            step_code_writes.append(target_file)

    return StepAnalysis(
        index=step.index,
        has_routing_declaration=has_routing_declaration,
        worker_calls=step_worker_calls,
        has_unrouted_mutation=step_has_unrouted_mutation,
        issues=step_issues,
        code_writes=step_code_writes,
        total_writes=total_writes,
        calibration_markers=calibration_markers,
    )


def compute_metrics(
    steps: list[Step],
    code_extensions: list[str],
    worker_patterns: Sequence[str | re.Pattern[str]],
    safe_patterns: list[re.Pattern[str]],
    security_ctx: SecurityContext,
) -> dict[str, Any]:
    total_writes = 0
    code_writes = 0
    routing_declarations = 0
    worker_calls = 0
    code_write_files: list[str] = []
    violations: list[tuple[int, list[str]]] = []
    violation_details: list[tuple[int, list[str]]] = []
    declaration_drift: list[tuple[int, list[str]]] = []
    calibration_markers = 0

    for step in steps:
        analysis = _analyze_step(
            step,
            code_extensions,
            worker_patterns,
            safe_patterns,
            security_ctx=security_ctx,
        )

        if analysis.has_routing_declaration:
            routing_declarations += 1
        worker_calls += analysis.worker_calls
        total_writes += analysis.total_writes
        code_writes += len(analysis.code_writes)
        code_write_files.extend(analysis.code_writes)
        calibration_markers += analysis.calibration_markers

        if analysis.issues:
            declaration_drift.append((analysis.index, analysis.issues))
        if analysis.has_violations:
            violations.append((analysis.index, analysis.code_writes))
            violation_details.append((analysis.index, analysis.issues))

    return {
        "total_writes": total_writes,
        "code_writes": code_writes,
        "routing_declarations": routing_declarations,
        "worker_calls": worker_calls,
        "code_write_files": code_write_files,
        "violations": violations,
        "declaration_drift": declaration_drift,
        "violation_details": violation_details,
        "calibration_markers": calibration_markers,
    }


class LogParserAdapter:
    """Base interface for parsing conversation log steps."""

    def parse(self, log_file: str, text: str) -> list[Step]:
        raise NotImplementedError


class TextLogParser(LogParserAdapter):
    """Parses plain text conversation logs split on Step headers."""

    def parse(self, log_file: str, text: str) -> list[Step]:
        return _parse_text_steps(text)


class JsonLinesLogParser(LogParserAdapter):
    """Parses JSON Lines log format."""

    def parse(self, log_file: str, text: str) -> list[Step]:
        return _parse_jsonl_steps(text)


class PolicyEvaluator:
    """Evaluates steps against routing policy configuration."""

    def __init__(
        self,
        config: dict[str, Any],
        security_ctx: SecurityContext | None = None,
    ) -> None:
        self.config = config
        self.security_ctx = security_ctx or SecurityContext.create()
        self.worker_patterns = load_patterns(config)
        self.code_extensions = load_code_extensions(config)
        self.safe_patterns = load_safe_patterns(config)

    def evaluate(self, steps: list[Step]) -> dict[str, Any]:
        return compute_metrics(
            steps,
            self.code_extensions,
            self.worker_patterns,
            self.safe_patterns,
            security_ctx=self.security_ctx,
        )


class RoutingAuditEngine:
    """Deep engine orchestrating log parsing, policy evaluation, and reporting."""

    def __init__(
        self,
        config_path: str | Path | AuditConfig | None = None,
        root_dir: str | Path | None = None,
        *,
        config: AuditConfig | None = None,
    ) -> None:
        if isinstance(config_path, AuditConfig):
            if config is not None:
                raise TypeError("config was provided both positionally and by keyword")
            config = config_path
            config_path = None
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.root_dir = root_dir
        self.security_ctx = SecurityContext.create(root_dir=root_dir)
        self._legacy_config: dict[str, Any] | None = None

        if config is None:
            with open(self.config_path, "r", encoding="utf-8") as f:
                legacy_config: dict[str, Any] = json.load(f)
            self._legacy_config = legacy_config
            config = AuditConfig(
                worker_patterns=[
                    re.compile(r"^" + re.escape(pattern) + r"(?:\s|$)")
                    for pattern in load_patterns(legacy_config)
                ],
                safe_patterns=load_safe_patterns(legacy_config),
                code_extensions=load_code_extensions(legacy_config),
            )
        self.config = config

    def audit(self, steps: list[Step]) -> AuditResult:
        """Evaluate already-parsed steps delegating to _analyze_step (ADR-0003)."""
        issues: list[AuditIssue] = []
        worker_calls = 0
        total_writes = 0
        code_writes = 0
        routing_declarations = 0

        for discovery_ordinal, step in enumerate(steps):
            analysis = _analyze_step(
                step,
                self.config.code_extensions,
                self.config.worker_patterns,
                self.config.safe_patterns,
                security_ctx=self.security_ctx,
            )

            if analysis.has_routing_declaration:
                routing_declarations += 1

            worker_calls += analysis.worker_calls
            total_writes += analysis.total_writes
            code_writes += len(analysis.code_writes)

            if analysis.has_unrouted_mutation:
                issues.append(
                    AuditIssue(
                        "error",
                        f"Step {step.index}: unsafe or unrouted command execution",
                        discovery_ordinal,
                    )
                )

            for message in self._structural_issues(step):
                issues.append(AuditIssue("error", message, discovery_ordinal))

            # _structural_issues already covers DEC-01/02/03/04; analysis.issues
            # is the only source for DEC-05 (calibration signature) and LOG-01
            # (unknown write tool) — without this, audit() silently drops both.
            for message in analysis.issues:
                if message.startswith(("DEC-05", "LOG-01")):
                    issues.append(
                        AuditIssue(
                            "error", f"Step {step.index}: {message}", discovery_ordinal
                        )
                    )

            if analysis.code_writes and analysis.worker_calls == 0:
                issues.append(
                    AuditIssue(
                        "error",
                        f"Step {step.index}: unrouted code edit detected "
                        f"({analysis.code_writes})",
                        discovery_ordinal,
                    )
                )

        if not any(issue.severity == "error" for issue in issues):
            if code_writes > worker_calls:
                issues.append(
                    AuditIssue(
                        "warning",
                        f"WARN-01 more code edits ({code_writes}) than worker "
                        f"calls ({worker_calls})",
                        len(steps),
                    )
                )
            elif routing_declarations == 0 and total_writes > 0:
                issues.append(
                    AuditIssue(
                        "warning",
                        f"WARN-02 no routing declarations for {total_writes} "
                        "file writes",
                        len(steps),
                    )
                )

        return AuditResult(
            issues=sorted(issues),
            worker_calls=worker_calls,
            has_worker_calls=worker_calls > 0,
            total_steps=len(steps),
        )

    def _is_worker_invocation(self, command: str) -> bool:
        stripped = _strip_command_wrappers(command)
        return any(pattern.match(stripped) for pattern in self.config.worker_patterns)

    def _structural_issues(self, step: Step) -> list[str]:
        if not step.routing:
            return []

        declaration = ROUTING_LABEL_RE.search(step.routing)
        if declaration is None:
            direct_declaration = re.fullmatch(
                r"\[ROUTING:\s*Direct\s+—\s*reason:\s*[^\]\n]+\]",
                step.routing,
                re.IGNORECASE,
            )
            if direct_declaration is None:
                return [f"Step {step.index}: DEC-03 invalid routing declaration"]
            return []

        declared_worker = declaration.group("worker").strip().lower()
        declared_effort = declaration.group("effort").lower()
        issues: list[str] = []

        for command in step.commands:
            if any(marker in command for marker in UNSAFE_SUBSTRINGS):
                continue
            for segment in command_segments(command):
                if not segment or not self._is_worker_invocation(segment):
                    continue

                stripped = _strip_command_wrappers(segment)
                executable = stripped.split(maxsplit=1)[0].lower()
                model_match = MODEL_RE.search(segment)
                effort_match = EFFORT_RE.search(segment)
                requires_flags = (
                    executable == "codex" and stripped.startswith("codex exec")
                ) or executable == "claude"

                if requires_flags and (not model_match or not effort_match):
                    issues.append(
                        f"Step {step.index}: DEC-04 missing --model or "
                        "model_reasoning_effort"
                    )
                    continue
                model = model_match.group("model").lower() if model_match else ""
                actual_effort = (
                    effort_match.group("effort").lower() if effort_match else None
                )
                if not self._worker_matches_declaration(
                    executable, model, declared_worker
                ):
                    issues.append(
                        f"Step {step.index}: DEC-01 declaration worker/model drift"
                    )

                # Flagless worker forms are valid, but must still undergo the
                # DEC-01 declaration check above.  They have no calibration
                # flags to validate afterwards.
                if not requires_flags and not model_match and not effort_match:
                    continue
                if actual_effort is not None and actual_effort != declared_effort:
                    issues.append(
                        f"Step {step.index}: DEC-02 declaration effort drift"
                    )

        return issues

    @staticmethod
    def _worker_matches_declaration(
        executable: str, model: str, declared_worker: str
    ) -> bool:
        expected_tier = next(
            (tier for tier in ("sol", "terra", "luna") if tier in declared_worker),
            None,
        )
        if "codex" in declared_worker or expected_tier:
            return executable == "codex" and (
                # ``codex review`` is intentionally flagless, so DEC-01 can
                # establish only the executable family in that case.  Model
                # tier validation remains enforced whenever a model is given.
                expected_tier is None or not model or expected_tier in model
            )
        if any(
            name in declared_worker
            for name in (
                "claude",
                "sonnet",
                "opus",
                "fable",
                "heavy_doer",
                "planner",
            )
        ):
            family = next(
                (
                    name
                    for name in ("sonnet", "opus", "fable")
                    if name in declared_worker
                ),
                None,
            )
            version_match = re.search(r"\b\d+(?:\.\d+)?\b", declared_worker)
            expected_version = version_match.group(0) if version_match else None
            return (
                executable == "claude"
                and (family is None or family in model)
                and (expected_version is None or expected_version in model)
            )
        if any(
            name in declared_worker
            for name in ("agy", "gemini", "context_specialist")
        ):
            return executable == "agy"
        return False

    def get_parser(self, log_file: str, text: str) -> LogParserAdapter:
        if text.strip().startswith("{") or Path(log_file).suffix.lower() == ".jsonl":
            return JsonLinesLogParser()
        return TextLogParser()

    def audit_log(self, log_file: str | Path, strict: bool = False) -> AuditReport:
        log_path = str(log_file)
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

        if not text.strip():
            raise ValueError(f"Empty log: {log_path}")

        parser = self.get_parser(log_path, text)
        steps = parser.parse(log_path, text)
        if not steps:
            raise ValueError(f"No steps parsed from log: {log_path}")

        legacy_config = self._legacy_config
        if legacy_config is None:
            with open(self.config_path, "r", encoding="utf-8") as f:
                legacy_config = json.load(f)
        evaluator = PolicyEvaluator(legacy_config, security_ctx=self.security_ctx)
        metrics = evaluator.evaluate(steps)

        violation_count = len(metrics["violations"])
        violation = (
            metrics["code_writes"] > 0 and metrics["worker_calls"] == 0
        ) or violation_count > 0
        warning = (
            metrics["code_writes"] > metrics["worker_calls"] and not violation
        ) or (
            metrics["routing_declarations"] == 0 and metrics["total_writes"] > 0 and not violation
        )

        exit_code = 1 if violation or (strict and warning) else 0

        return AuditReport(
            total_writes=metrics["total_writes"],
            code_writes=metrics["code_writes"],
            routing_declarations=metrics["routing_declarations"],
            worker_calls=metrics["worker_calls"],
            violations=metrics["violations"],
            declaration_drift=metrics["declaration_drift"],
            violation_details=metrics["violation_details"],
            calibration_markers=metrics["calibration_markers"],
            code_write_files=metrics["code_write_files"],
            exit_code=exit_code,
        )



def run_audit(
    config: dict[str, Any],
    log_file: str,
    security_ctx: SecurityContext,
    strict: bool = False,
) -> int:
    worker_patterns = load_patterns(config)
    code_extensions = load_code_extensions(config)
    safe_patterns = load_safe_patterns(config)

    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        print(f"❌ No log found: {log_file}")
        return 2

    if not text.strip():
        print(f"❌ Empty log: {log_file}")
        return 2

    try:
        steps = parse_steps(log_file, text)
    except Exception:  # noqa: BLE001 - the audit must fail closed on malformed logs.
        traceback.print_exc(file=sys.stderr)
        print(f"❌ Failed to parse log: {log_file}")
        return 2

    if not steps:
        print(f"❌ No steps parsed from log: {log_file}")
        return 2

    metrics = compute_metrics(
        steps, code_extensions, worker_patterns, safe_patterns, security_ctx=security_ctx
    )

    raw_has_writes = any(t in text for t in WRITE_TOOLS)
    raw_has_routing = "[ROUTING:" in text
    raw_has_calibration = (
        CALIBRATION_RE.search(text) is not None or '"signature"' in text
    )
    if (raw_has_writes and metrics["total_writes"] == 0) or (
        raw_has_routing and metrics["routing_declarations"] == 0
    ) or (
        raw_has_calibration and metrics["calibration_markers"] == 0
    ):
        print("❌ Parser out of sync with log format.")
        return 2

    violation_count = len(metrics["violations"])

    print("📊 Results:")
    print(f"  {'Total file write tool calls:':<33} {metrics['total_writes']}")
    print(f"  {'Writes to source code files:':<33} {metrics['code_writes']}")
    print(f"  {'ROUTING declarations found:':<33} {metrics['routing_declarations']}")
    print(f"  {'Worker CLI calls found:':<33} {metrics['worker_calls']}")
    print(f"  {'Unrouted code edit violations:':<33} {violation_count}")
    print()

    violation = False

    if metrics["code_writes"] > 0 and metrics["worker_calls"] == 0:
        print(f"🔴 VIOLATION: {metrics['code_writes']} source code edits with 0 worker calls.")
        print("   Antigravity executed code changes directly without routing.")
        violation = True

    if violation_count > 0:
        print(f"🔴 VIOLATION: Unrouted code edit detected in {violation_count} step(s).")
        print("   Every step that writes a source code file must also contain a worker CLI call,")
        print("   regardless of what its [ROUTING:] label says.")
        for step_index, files in metrics["violations"]:
            print(
                f"  ⚠️  Step {step_index}: unrouted code edit detected ({files})",
                file=sys.stderr,
            )
        violation = True

    warning = False
    if metrics["code_writes"] > metrics["worker_calls"] and not violation:
        print(
            "🟡 WARNING: More code edits "
            f"({metrics['code_writes']}) than worker calls "
            f"({metrics['worker_calls']})."
        )
        print("   Some edits may not have been properly routed.")
        warning = True
    elif metrics["routing_declarations"] == 0 and metrics["total_writes"] > 0 and not violation:
        print(
            "🟡 WARNING: No [ROUTING:] declarations found, but "
            f"{metrics['total_writes']} file writes occurred."
        )
        warning = True
    elif not violation:
        print("✅ No violations detected.")

    print()
    print("--- Detailed source code edits ---")
    counts = Counter(Path(f).name for f in metrics["code_write_files"])
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{count:>7} {name}")

    if violation:
        return 1
    if strict and warning:
        return 1
    return 0


def main() -> None:
    strict = "--strict" in sys.argv
    if strict:
        sys.argv.remove("--strict")

    try:
        config = load_config()
    except Exception:  # noqa: BLE001 - configuration failures must exit closed.
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    if len(sys.argv) < 2:
        print("Usage: routing_check.py [--strict] <log_file>", file=sys.stderr)
        sys.exit(2)

    security_ctx = SecurityContext.create()
    sys.exit(
        run_audit(
            config,
            sys.argv[1],
            security_ctx=security_ctx,
            strict=strict,
        )
    )


if __name__ == "__main__":
    main()
