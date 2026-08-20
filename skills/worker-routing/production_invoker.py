#!/usr/bin/env python3
"""Production implementation of AdvisoryConsultation's worker callable.

The consultation loop accepts an injected ``(model, effort, prompt) -> str``
callable so it remains fully testable offline.  This module provides the
production adapter: it resolves a provider-specific argv list and launches it
without a shell or interactive stdin.

``make_journaled_invoke_worker`` is the instrumentation this module adds for
spec 0004: ``invoke_worker`` itself stays untouched (its signature is the
seam ``AdvisoryConsultation`` depends on, and it has no ``root_dir`` or task
identity to journal with), and the factory wraps it from the outside instead,
closing over the journal's context and handing back a plain ``(model,
effort, prompt) -> str`` callable that fits the same seam. See that
function's docstring for the journaling contract.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import learning_journal

WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"
DEFAULT_TIMEOUT_SECONDS = 300.0

# The routing protocol uses human-readable names, while the worker CLIs need
# stable model identifiers. Keep both the documented labels and already
# normalized IDs explicit so arbitrary strings never select a provider.
MODEL_ALIASES = {
    "Claude Opus 5 (Thinking)": "claude-opus-5",
    "Claude Sonnet 5 (Thinking)": "claude-sonnet-5",
    "Claude Fable 5": "claude-fable-5",
    "Codex 5.6 Luna": "gpt-5.6-luna",
    "Codex 5.6 Terra": "gpt-5.6-terra",
    "Codex 5.6 Sol": "gpt-5.6-sol",
    "GPT-OSS 120B (Medium)": "gpt-oss-120b",
    "Gemini 3.6 Flash (High)": "gemini-3.6-flash",
    "Gemini 3.6 Flash (Medium)": "gemini-3.6-flash",
    "Gemini 3.6 Flash (Low)": "gemini-3.6-flash",
    "Gemini 3.5 Flash (High)": "gemini-3.5-flash",
    "Gemini 3.5 Flash (Medium)": "gemini-3.5-flash",
    "Gemini 3.5 Flash (Low)": "gemini-3.5-flash",
    "Gemini 3.1 Pro (High)": "gemini-3.1-pro",
    "Gemini 3.1 Pro (Low)": "gemini-3.1-pro",
    "claude-opus-5": "claude-opus-5",
    "claude-sonnet-5": "claude-sonnet-5",
    "claude-fable-5": "claude-fable-5",
    "gpt-5.6-luna": "gpt-5.6-luna",
    "gpt-5.6-terra": "gpt-5.6-terra",
    "gpt-5.6-sol": "gpt-5.6-sol",
    "gpt-oss-120b": "gpt-oss-120b",
    "gemini-3.6-flash": "gemini-3.6-flash",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3.1-pro": "gemini-3.1-pro",
    "agy": "agy",
}

CODEX_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-oss-120b"})
CLAUDE_MODELS = frozenset({"claude-opus-5", "claude-sonnet-5", "claude-fable-5"})
AGY_MODELS = frozenset({"agy", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-pro"})

Runner = Callable[..., subprocess.CompletedProcess[str]]


class AsyncWorkerProcess(Protocol):
    """The slice of ``asyncio.subprocess.Process`` this module depends on.

    Naming the seam as a `Protocol` (rather than importing
    `asyncio.subprocess.Process` directly) is what makes it fakeable: a test
    double only has to implement `communicate`/`kill`/`wait`/`returncode`,
    not subclass an object the real event loop constructs.
    """

    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


# An injectable seam standing in for ``asyncio.create_subprocess_exec``: the
# real one is called as ``runner(*argv, **kwargs)`` and returns an awaitable
# process handle, so tests substitute a fake coroutine function rather than
# patching the event loop's subprocess machinery.
AsyncRunner = Callable[..., Awaitable[AsyncWorkerProcess]]

# One routed invocation request, the unit ``invoke_workers_parallel`` batches.
WorkerRequest = tuple[str, str, str]


async def _kill_and_reap_process(proc: AsyncWorkerProcess) -> None:
    """Terminate ``proc`` and wait for it to be reaped when possible."""
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        await proc.wait()
    except ProcessLookupError:
        pass


@dataclass(frozen=True)
class WorkerExecutionResult:
    """Structured outcome data for a single worker invocation."""

    raw_output: str
    duration_ms: int
    cost_estimate_usd: float
    success: bool
    error: str | None = None
    parsed_payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be greater than or equal to 0")
        if self.cost_estimate_usd < 0.0:
            raise ValueError("cost_estimate_usd must be greater than or equal to 0.0")


def extract_review_payload(
    raw_output: str, *, default_candidate_hash: str = "synth1"
) -> dict[str, Any]:
    """Extract a normalized review payload from worker stdout.

    A JSON object embedded in prose (including a Markdown code block) takes
    precedence. Otherwise, a small deterministic set of verdict keywords
    gives callers a safe, backwards-compatible payload.
    """
    defaults: dict[str, Any] = {
        "vote": "approve",
        "confidence": 1.0,
        "findings": [],
        "candidate_hash": default_candidate_hash,
    }
    if not raw_output.strip():
        return defaults

    normalized_text = raw_output.casefold()
    security_indicators = (
        "critical vulnerability",
        "sql injection",
        "cwe-",
        "cve-",
    )
    security_resolution_re = re.compile(
        r"(?:\bno\s+(?:critical\s+vulnerability|sql\s+injection|cwe-|cve-)|"
        r"(?<!not\s)\b(?:prevented|prevents|resolved|parameteri[sz]ed|"
        r"parameteri[sz]ation)\b)"
    )

    def has_unnegated_security_indicator() -> bool:
        return any(
            any(indicator in line for indicator in security_indicators)
            and security_resolution_re.search(line) is None
            for line in normalized_text.splitlines()
        )

    def populate_prose_veto(payload: dict[str, Any]) -> dict[str, Any]:
        vote = str(payload.get("vote", "")).casefold()
        should_veto = "security_halt" in normalized_text or (
            vote in {"block", "revise"} and has_unnegated_security_indicator()
        )
        if should_veto:
            prose_veto = {
                "id": "PROSE-VETO",
                "severity": "critical",
                "confidence": 1.0,
                "claim": "Critical security finding detected in review prose",
            }
            findings = payload.get("findings")
            if not isinstance(findings, list):
                findings = []
            if not any(
                isinstance(finding, dict) and finding.get("id") == "PROSE-VETO"
                for finding in findings
            ):
                findings = [*findings, prose_veto]
            payload["findings"] = findings
        return payload

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw_output):
        try:
            # `raw_decode` consumes one complete object from this opening
            # brace, so nested dictionaries and trailing prose do not alter
            # the object boundary (unlike a non-greedy `\{.*?\}` match).
            parsed, _ = decoder.raw_decode(raw_output, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            result = {**defaults, **parsed}
            result["vote"] = str(result["vote"]).lower()
            try:
                result["confidence"] = float(result["confidence"])
            except (TypeError, ValueError):
                result["confidence"] = 1.0
            if not isinstance(result["findings"], list):
                result["findings"] = []
            if not isinstance(result["candidate_hash"], str):
                result["candidate_hash"] = default_candidate_hash
            return populate_prose_veto(result)

    text = normalized_text
    if any(keyword in text for keyword in ("block", "security_halt", "critical")):
        return populate_prose_veto({**defaults, "vote": "block", "confidence": -1.0})
    if re.search(r"\brevise\b", text) or "changes requested" in text:
        return populate_prose_veto({**defaults, "vote": "revise", "confidence": -0.3})
    return populate_prose_veto(defaults)


def _with_worker_mode_token(prompt: str) -> str:
    """Return ``prompt`` with the nested-worker marker present exactly once."""
    if prompt.startswith(WORKER_MODE_TOKEN):
        return prompt
    return f"{WORKER_MODE_TOKEN} {prompt}"


def _validated_effort(effort: str) -> learning_journal.EffortLevel:
    """Narrow ``effort`` to the one vocabulary this module accepts.

    That vocabulary is `learning_journal.VALID_EFFORTS` — the same
    `low|medium|high|ultra` the routing protocol's `[ROUTING:]` declarations
    use and `routing_check.EFFORT_RE` audits — imported rather than
    re-listed so the CLI, the audit, and the journal can never disagree
    about what an effort level is.

    Rejecting here rather than passing an arbitrary string through to
    `codex -c model_reasoning_effort="..."` mirrors how an unknown *model*
    is already rejected, and it is what makes
    `make_journaled_invoke_worker`'s "exactly one record per invocation"
    guarantee hold: an effort the journal cannot represent would otherwise
    produce an invocation with no record at all. Failing closed before any
    process is launched means there is no invocation to owe a record for.

    The `cast` is the narrowing `Literal` membership cannot express to mypy;
    the `in` test immediately above it is the real check, and
    `WorkerExecutionRecord.__post_init__` re-runs it at construction.
    """
    if effort not in learning_journal.VALID_EFFORTS:
        raise ValueError(
            f"Unsupported worker effort: {effort!r} "
            f"(expected one of {sorted(learning_journal.VALID_EFFORTS)})"
        )
    return cast("learning_journal.EffortLevel", effort)


def _validated_run_id(run_id: str | None) -> None:
    """Reject a malformed caller-supplied ``run_id`` before any worker runs.

    The same rule `TaskLabel.for_task` applies to `task_id` — both are
    carried identifiers, not caller prose, and both are checked against
    `learning_journal.TASK_ID_RE` (see `_validate_carried_identifier`, the
    validator this mirrors without importing). `None` means the caller
    supplied no run identity and is left alone: `make_journaled_invoke_worker`
    then generates one with `secrets.token_hex(8)`, which is valid by
    construction and never reaches this check.
    """
    if run_id is None:
        return
    if not learning_journal.TASK_ID_RE.fullmatch(run_id):
        raise ValueError(
            f"run_id must match {learning_journal.TASK_ID_RE.pattern} — the "
            "journal carries identifiers only, never task text, prompt "
            "text, or paths"
        )


def build_worker_command(model: str, effort: str, prompt: str) -> list[str]:
    """Build the documented CLI argv for a routed worker.

    Unknown models and unknown effort levels are rejected rather than
    guessed: launching an arbitrary executable, or handing a provider a
    reasoning-effort string it has never heard of, would both violate the
    consultation's fail-closed contract.
    """
    routed_prompt = _with_worker_mode_token(prompt)
    _validated_effort(effort)
    normalized_model = MODEL_ALIASES.get(model)
    if normalized_model is None:
        raise ValueError(f"Unsupported worker model: {model!r}")

    if normalized_model in CODEX_MODELS:
        return [
            "codex",
            "exec",
            "--model",
            normalized_model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-s",
            "workspace-write",
            routed_prompt,
        ]
    if normalized_model in CLAUDE_MODELS:
        return [
            "claude",
            "-p",
            "--no-session-persistence",
            "--model",
            normalized_model,
            "--effort",
            effort,
            "--allow-dangerously-skip-permissions",
            "--permission-mode",
            "bypassPermissions",
            routed_prompt,
        ]
    if normalized_model in AGY_MODELS:
        return ["agy", "-p", routed_prompt]

    # Kept for defensive exhaustiveness if a future alias is added without a
    # corresponding CLI provider template.
    raise ValueError(f"Unsupported worker model: {model!r}")


def _diagnostic_text(value: str | bytes | None) -> str:
    """Normalize subprocess diagnostic fields, including timeout byte output."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


def invoke_worker(
    model: str,
    effort: str,
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
) -> str:
    """Run a worker and return its stdout, failing closed on process errors.

    ``runner`` is injectable for offline unit tests. The real runner always
    receives a merged environment that marks the child as nested execution,
    an explicit EOF on stdin, and captured text diagnostics.
    """
    command = build_worker_command(model, effort, prompt)
    environment = {**os.environ, "IN_WORKER_ROUTING": "true"}

    try:
        result = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=environment,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        timeout_stdout = getattr(error, "stdout", None)
        stdout = _diagnostic_text(
            timeout_stdout if timeout_stdout is not None else error.output
        )
        stderr = _diagnostic_text(getattr(error, "stderr", None))
        raise RuntimeError(
            f"Worker {model!r} timed out after {timeout} seconds; "
            f"stdout: {stdout!r}; stderr: {stderr!r}"
        ) from error

    if result.returncode != 0:
        raise RuntimeError(
            f"Worker {model!r} exited with exit code {result.returncode}; "
            f"stdout: {_diagnostic_text(result.stdout)!r}; "
            f"stderr: {_diagnostic_text(result.stderr)!r}"
        )

    return _diagnostic_text(result.stdout)


async def invoke_worker_async(
    model: str,
    effort: str,
    prompt: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: AsyncRunner = asyncio.create_subprocess_exec,
    clock: Callable[[], float] = time.monotonic,
) -> WorkerExecutionResult:
    """Run a worker without blocking the event loop, never raising for its outcome.

    This is the async counterpart to `invoke_worker`, and deliberately
    behaves differently from it at the one point their contracts diverge:
    `invoke_worker` raises `RuntimeError` on a non-zero exit or a timeout,
    because it is the seam `AdvisoryConsultation`'s single-worker debate loop
    depends on and that loop already treats an exception as "this worker's
    turn failed." This function instead reports the same two outcomes as a
    `WorkerExecutionResult` with `success=False` and a populated `error`,
    because its callers are batches — `invoke_workers_parallel`, and the
    review panels ticket 03 point at it — where one worker's non-zero exit
    or timeout must not abort every sibling awaited alongside it.

    A malformed request is the one exception to that: an unsupported model
    or an uncalibrated effort still raises, synchronously, before any
    process is spawned. `build_worker_command` performs that validation, and
    both are call-site bugs distinguishable from a worker's runtime outcome
    only by never having reached a worker at all (spec 0005 user story 6).
    `invoke_workers_parallel` is the layer that turns even this raise into a
    failed result per-request, so a single bad request in a batch cannot
    cancel its siblings; called directly, this function still fails closed.

    ``runner`` and ``clock`` are both injectable so tests exercise success,
    non-zero exit, and timeout reaping without a real subprocess or a real
    clock. On timeout the child is explicitly killed and reaped
    (`proc.kill()` then `await proc.wait()`) before this function returns, so
    no caller can observe a result without the process lifecycle having
    already been closed out — the guarantee spec 0005 asks for.
    """
    command = build_worker_command(model, effort, prompt)
    model_id, _ = _resolve_model_id_and_family(model)
    environment = {**os.environ, "IN_WORKER_ROUTING": "true"}

    start = clock()
    try:
        proc = await runner(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
    except Exception as error:  # noqa: BLE001 - a missing binary or spawn failure is a worker outcome, not a call-site bug.
        duration_ms = max(0, round((clock() - start) * 1000))
        return WorkerExecutionResult(
            raw_output="",
            duration_ms=duration_ms,
            cost_estimate_usd=0.0,
            success=False,
            error=f"Worker {model!r} failed to spawn: {error}",
        )

    def failed_execution_result(error_message: str) -> WorkerExecutionResult:
        duration_ms = max(0, round((clock() - start) * 1000))
        return WorkerExecutionResult(
            raw_output="",
            duration_ms=duration_ms,
            cost_estimate_usd=estimate_cost_usd(model_id, duration_ms),
            success=False,
            error=error_message,
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_and_reap_process(proc)
        return failed_execution_result(
            f"Worker {model!r} timed out after {timeout} seconds"
        )
    except Exception as error:  # noqa: BLE001 - a communicate() failure is a worker outcome, not a call-site bug.
        await _kill_and_reap_process(proc)
        return failed_execution_result(
            f"Worker {model!r} failed during execution: {error}"
        )

    duration_ms = max(0, round((clock() - start) * 1000))
    stdout_text = _diagnostic_text(stdout_bytes)
    stderr_text = _diagnostic_text(stderr_bytes)

    if proc.returncode != 0:
        return WorkerExecutionResult(
            raw_output=stdout_text,
            duration_ms=duration_ms,
            cost_estimate_usd=estimate_cost_usd(model_id, duration_ms),
            success=False,
            error=(
                f"Worker {model!r} exited with exit code {proc.returncode}; "
                f"stderr: {stderr_text!r}"
            ),
        )

    return WorkerExecutionResult(
        raw_output=stdout_text,
        duration_ms=duration_ms,
        cost_estimate_usd=estimate_cost_usd(model_id, duration_ms),
        success=True,
        parsed_payload=extract_review_payload(stdout_text),
    )


async def invoke_workers_parallel(
    requests: Sequence[WorkerRequest],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: AsyncRunner = asyncio.create_subprocess_exec,
    clock: Callable[[], float] = time.monotonic,
) -> list[WorkerExecutionResult]:
    """Run a batch of `(model, effort, prompt)` requests concurrently.

    Every request runs through `invoke_worker_async`, so it inherits that
    function's "never raise for a worker's own outcome" contract. The one
    gap that contract leaves open — a malformed request whose
    `build_worker_command` call raises before a process exists — is closed
    here: this is the layer review panels and multi-model batches actually
    call (spec 0005 user story 2), and a single mistyped model name in a
    ten-member panel must not cancel the other nine awaited alongside it.

    Results come back in one list, positionally aligned with ``requests``
    (`asyncio.gather` preserves input order regardless of completion order),
    so a caller can zip the two sequences back together without threading an
    identifier through the result type.
    """

    async def _invoke_one(request: WorkerRequest) -> WorkerExecutionResult:
        model, effort, prompt = request
        try:
            return await invoke_worker_async(
                model, effort, prompt, timeout=timeout, runner=runner, clock=clock
            )
        except ValueError as error:
            return WorkerExecutionResult(
                raw_output="",
                duration_ms=0,
                cost_estimate_usd=0.0,
                success=False,
                error=str(error),
            )

    return list(await asyncio.gather(*(_invoke_one(request) for request in requests)))


# --- worker-execution journaling (spec 0004) ---------------------------------
#
# No pricing source exists anywhere in this repo. The choice here is an
# explicit, documented rate table keyed by the same normalized model id
# `MODEL_ALIASES` resolves to, rather than inventing a single
# made-up-looking number. These per-second rates are rough placeholders,
# not billing data — replace the table wholesale the day a real pricing
# source exists; until then it is the one place the estimate can be read,
# checked, or corrected.
#
# The table must stay *total* over every model that can actually be
# invoked. `test_production_invoker.py`'s
# `test_rate_table_prices_every_invocable_model` asserts exactly that
# against `MODEL_ALIASES`' values, so a model added to
# `CODEX_MODELS`/`CLAUDE_MODELS`/`AGY_MODELS` without a matching entry here
# is a test failure rather than a silent gap. That totality is what lets
# `estimate_cost_usd` treat "no rate" as "no invocation" — see its
# docstring.
USD_PER_SECOND: dict[str, float] = {
    "claude-opus-5": 0.0150,
    "claude-sonnet-5": 0.0060,
    "claude-fable-5": 0.0020,
    "gpt-5.6-sol": 0.0120,
    "gpt-5.6-terra": 0.0060,
    "gpt-5.6-luna": 0.0020,
    "gpt-oss-120b": 0.0010,
    "agy": 0.0040,
    "gemini-3.6-flash": 0.0010,
    "gemini-3.5-flash": 0.0010,
    "gemini-3.1-pro": 0.0060,
}

# The `model_id` a record carries when `MODEL_ALIASES` did not recognize the
# caller's model at all. It is not a model name — no provider answers to it —
# and that is the point: it is the record's own marker that the routed model
# was never identified, and therefore that nothing about this call's cost is
# known. `_validate_identifier` accepts it (no spaces, no parentheses) where
# a caller-composed display name like "Claude Opus 5 (Thinking)" would be
# rejected, which is why the raw string is never carried into the record.
UNPRICED_MODEL_ID = "unrecognized-model"


def estimate_cost_usd(model_id: str, duration_ms: int) -> float:
    """A named derivation over ``(model_id, duration_ms)``, never a guess.

    Named ``estimate`` because that is what it honestly is: rate times wall
    time, not a real invoice. `WorkerExecutionRecord.cost_estimate_usd`'s own
    docstring says not to rename that field until a real billing source
    backs it — this function is the derivation that name promises.

    **How a reader tells "we cannot price this" from "this was expensive."**
    Not by the amount. `cost_estimate_usd` is one number in a field the
    scoreboard averages as cost per completed task, so any in-band sentinel
    amount is a number that silently becomes part of that average — a
    previous version billed an unpriced model at 9,999 USD/second, which
    turned a single 300-second call into a $2,999,700 entry indistinguishable
    from a real estimate and capable of dominating a whole week's mean. The
    marker is `model_id == UNPRICED_MODEL_ID` instead: a reader filters on
    the field that actually says "unidentified model", and the amount stays
    an amount.

    That leaves `0.0` to mean what it truthfully means here. An unpriced id
    is only ever `UNPRICED_MODEL_ID`, and reaching it means `MODEL_ALIASES`
    rejected the model, which means `build_worker_command` raised before any
    process was launched — no provider ran, so nothing was spent. Zero is the
    measurement, not a default standing in for a missing one. What makes that
    reasoning hold rather than merely sound plausible is the totality of
    `USD_PER_SECOND` over every invocable model, which
    `test_rate_table_prices_every_invocable_model` pins: no real model can
    fall through to the no-rate branch at all.

    Note the honest limit of that: a *priced* model can still estimate 0.0,
    for a call whose measured duration rounds to zero milliseconds. That is
    also a true statement about a call that took no measurable time, and it
    is why the marker is `model_id` and not the amount — reading "unpriced"
    off a zero would confuse the two, reading it off `model_id` cannot.
    """
    rate = USD_PER_SECOND.get(model_id)
    if rate is None:
        return 0.0
    return round(rate * (duration_ms / 1000.0), 6)


def _resolve_model_id_and_family(model: str) -> tuple[str, str]:
    """Normalize ``model`` and classify it by the partitions ``invoke_worker`` uses.

    Reuses `MODEL_ALIASES` / `CODEX_MODELS` / `CLAUDE_MODELS` / `AGY_MODELS`
    rather than inventing a second mapping, per this module's own contract.
    A model `MODEL_ALIASES` would itself reject returns
    ``(UNPRICED_MODEL_ID, "unknown")`` — a caller-composed display name may
    contain spaces or parentheses, which `learning_journal`'s identifier
    validation rejects, so the raw string is never carried into the record.
    That pair is also what tells a reader the record's cost is unknown; see
    `estimate_cost_usd`.
    """
    normalized = MODEL_ALIASES.get(model)
    if normalized is None:
        return UNPRICED_MODEL_ID, "unknown"
    if normalized in CLAUDE_MODELS:
        return normalized, "claude"
    if normalized in CODEX_MODELS:
        return normalized, "codex"
    if normalized in AGY_MODELS:
        return normalized, "agy"
    return normalized, "unknown"


def report_journal_error_to_stderr(message: str) -> None:
    """Default journal-failure sink: one line on stderr, and nothing else.

    Deliberately the loudest thing available that cannot disturb the worker
    call being observed. stderr rather than stdout because a routed worker's
    stdout is the consultation's actual payload — a warning printed into it
    would become part of a Planner's plan.
    """
    print(f"⚠️  {message}", file=sys.stderr)


def make_journaled_invoke_worker(
    task_id: str,
    *,
    root_dir: Path,
    task_type: learning_journal.TaskType | None = None,
    run_id: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    report_journal_error: Callable[[str], None] = report_journal_error_to_stderr,
) -> Callable[[str, str, str], str]:
    """Build a ``(model, effort, prompt) -> str`` callable that journals every call.

    This is the seam `AdvisoryConsultation` depends on — nothing about its
    shape changes — with the journal's context (`task_id`, `root_dir`) closed
    over rather than threaded through it, because neither fits the
    three-argument contract. Each call to the returned callable appends
    exactly one `WorkerExecutionRecord` to the journal under `root_dir`,
    whether the underlying `invoke_worker` succeeds, exits non-zero, or
    times out.

    **A `task_id`, not a built `TaskLabel`, and that is a rule rather than a
    convenience.** `learning_outcomes.record_*` already took the id, with a
    documented argument for why (a caller far from the schema should not have
    to import `learning_journal` to record something about work it just did).
    This factory demanded the label, which made that argument false for
    exactly one caller: `advisory_consultation` grew an
    `import learning_journal` solely to build a label to hand back. The id is
    the boundary type for every entry point into this loop; `TaskLabel` is
    the journal's internal invariant-carrier and is built here, one line
    below, by the code that owns the schema. `task_type` rides along as the
    optional tag `TaskLabel.for_task` itself takes, so the coarse tag stays
    reachable from production rather than becoming a field only tests can
    set.

    An unjournalable `task_id` therefore raises **here**, at wiring time,
    before any worker runs — not once per invocation afterwards.
    `advisory_consultation` already wraps this call in the try that degrades
    to "journaling disabled for this run", so a bad id costs the run its
    instrumentation and nothing else. `TaskLabel.for_halted_task` has no
    counterpart parameter on purpose: a sensitivity halt returns before any
    worker is contacted, so no invocation of a halted task exists to journal
    (see that constructor's own docstring).

    A caller-supplied `run_id` faces the same fate, at the same moment, for
    the same reason: `_validated_run_id` runs immediately below, so a typo'd
    `run_id` and a typo'd `task_id` both fail the factory build rather than
    one failing loudly here and the other failing quietly, once per
    invocation, the first time a record is built from it. The two identifiers
    used to be checked at two different times for no reason a reader could
    infer from the code; they no longer are.

    `run_id` defaults to a fresh random identity per factory call, which is
    the honest granularity: one factory instance is built per consultation,
    so "this factory's records" and "this run's records" are the same set.
    Two consultations of the same task share a `task_id` — it is a stable
    digest of the task text — and would otherwise be indistinguishable in the
    journal, summing their costs as one run and making rework invisible. A
    caller that already has a run identity (a future orchestrator correlating
    a dialogue, its invocations, and its outcome) passes it instead.

    Retry count is always 0, and stays a different question from rework:
    `invoke_worker` performs no retries today, so no invocation is ever
    attempt two of *itself*, and this records that honestly rather than a
    value a future retry mechanism would imply. A second consultation of the
    same task is not a retry — it is a second run, which is what `run_id`
    counts.

    Three failure modes, three different handlings — this is the module's
    half of the one contract `learning_journal`'s docstring states and
    `routing_check._persist_compliance_record` and `learning_outcomes`
    implement for the other two record families:

    - `invoke_worker` raising is the worker's own outcome, not a journal
      failure. It is journaled as `success=False` and then re-raised
      unchanged, so the caller sees exactly the exception it would have
      seen without instrumentation.
    - A record that cannot be *built* is a call-site bug, and by the
      journal's contract a call-site bug must be loud. Here it cannot be
      loud by raising: the worker has already run by the time the record is
      built, and raising would destroy a real result to report a
      bookkeeping fault. So it is loud by being *reported* — through
      `report_journal_error`, naming itself a call-site bug — which is the
      one thing the previous `except Exception: pass` did not do. Silence
      was the actual defect: the journal could go dark, or every record for
      a whole run could be rejected, with nothing anywhere saying so.
    - A record that cannot be *written* is the environment, and
      `append_journal_record` already returns rather than raises for it.
      That returned message was being discarded; it now goes to the same
      sink.

    Neither journal failure ever reaches the caller. `report_journal_error`
    is the seam that makes them observable instead — tests inject a
    collector, production gets one stderr line per failure — and a sink that
    raises would defeat the whole guarantee, so a caller supplying one owns
    keeping it total.

    `effort` is the one journal-facing value validated *before* the
    invocation rather than after it. An effort outside the journal's
    vocabulary cannot be represented in a record at all, so validating it
    afterwards would leave exactly the hole this docstring's first sentence
    denies: an invocation that happened and journaled nothing. Rejected up
    front, there is no invocation, and "exactly one record per invocation"
    stays true rather than nearly true. `build_worker_command` rejects the
    same value identically for callers that skip this wrapper.
    """
    task = learning_journal.TaskLabel.for_task(task_id, task_type=task_type)
    _validated_run_id(run_id)
    journal_run_id = run_id if run_id is not None else secrets.token_hex(8)

    def _journaled_invoke_worker(model: str, effort: str, prompt: str) -> str:
        journal_effort = _validated_effort(effort)

        start = clock()
        error: Exception | None = None
        output = ""
        try:
            output = invoke_worker(model, effort, prompt, timeout=timeout, runner=runner)
        except Exception as exc:  # noqa: BLE001 - re-raised unchanged below; only journaled here.
            error = exc
        duration_ms = max(0, round((clock() - start) * 1000))

        try:
            model_id, model_family = _resolve_model_id_and_family(model)
            record = learning_journal.WorkerExecutionRecord(
                task=task,
                duration_ms=duration_ms,
                cost_estimate_usd=estimate_cost_usd(model_id, duration_ms),
                success=error is None,
                retry_count=0,
                effort=journal_effort,
                model_id=model_id,
                model_family=model_family,
                run_id=journal_run_id,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised: see this factory's docstring.
            report_journal_error(
                f"learning journal record for a {model!r} invocation could not "
                f"be built (call-site bug, the invocation itself was "
                f"unaffected): {exc}"
            )
        else:
            write_error = learning_journal.append_journal_record(
                record, root_dir=root_dir
            )
            if write_error is not None:
                report_journal_error(write_error)

        if error is not None:
            raise error
        return output

    return _journaled_invoke_worker


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MODEL_ALIASES",
    "UNPRICED_MODEL_ID",
    "USD_PER_SECOND",
    "WORKER_MODE_TOKEN",
    "AsyncRunner",
    "AsyncWorkerProcess",
    "WorkerExecutionResult",
    "WorkerRequest",
    "build_worker_command",
    "estimate_cost_usd",
    "extract_review_payload",
    "invoke_worker",
    "invoke_worker_async",
    "invoke_workers_parallel",
    "make_journaled_invoke_worker",
    "report_journal_error_to_stderr",
]
