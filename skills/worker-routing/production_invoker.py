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

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

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


def _with_worker_mode_token(prompt: str) -> str:
    """Return ``prompt`` with the nested-worker marker present exactly once."""
    if prompt.startswith(WORKER_MODE_TOKEN):
        return prompt
    return f"{WORKER_MODE_TOKEN} {prompt}"


def build_worker_command(model: str, effort: str, prompt: str) -> list[str]:
    """Build the documented CLI argv for a routed worker.

    Unknown models are rejected rather than guessed: launching an arbitrary
    executable would violate the consultation's fail-closed contract.
    """
    routed_prompt = _with_worker_mode_token(prompt)
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


# --- worker-execution journaling (spec 0004) ---------------------------------
#
# No pricing source exists anywhere in this repo. The choice here is an
# explicit, documented rate table keyed by the same normalized model id
# `MODEL_ALIASES` resolves to, rather than either inventing a single
# made-up-looking number or silently recording zero. Zero is the dangerous
# default: it is a plausible-looking lie an operator has no reason to
# question. These per-second rates are rough placeholders, not billing
# data — replace the table wholesale the day a real pricing source exists;
# until then it is the one place the estimate can be read, checked, or
# corrected, and `test_routing.py`/`test_production_invoker.py` pin its
# shape so a model added to `CODEX_MODELS`/`CLAUDE_MODELS`/`AGY_MODELS`
# without a matching entry here is a test failure, not a silent gap.
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

# The rate a model missing from `USD_PER_SECOND` is billed at. Deliberately
# not 0.0 and deliberately far outside any real per-second rate: a missing
# entry must read as "the rate table has a gap" in the weekly report, not as
# "this call was free." A model can only reach this path if it is already a
# member of `CODEX_MODELS`, `CLAUDE_MODELS`, or `AGY_MODELS` (an unknown
# model to `MODEL_ALIASES` never invokes anything — see
# `_resolve_model_id_and_family`), so hitting it is always a maintenance gap,
# never a routine occurrence.
_UNKNOWN_MODEL_RATE_USD_PER_SECOND = 9_999.0


def estimate_cost_usd(model_id: str, duration_ms: int) -> float:
    """A named derivation over ``(model_id, duration_ms)``, never a guess.

    Named ``estimate`` because that is what it honestly is: rate times wall
    time, not a real invoice. `WorkerExecutionRecord.cost_estimate_usd`'s own
    docstring says not to rename that field until a real billing source
    backs it — this function is the derivation that name promises.
    """
    rate = USD_PER_SECOND.get(model_id, _UNKNOWN_MODEL_RATE_USD_PER_SECOND)
    return round(rate * (duration_ms / 1000.0), 6)


def _resolve_model_id_and_family(model: str) -> tuple[str, str]:
    """Normalize ``model`` and classify it by the partitions ``invoke_worker`` uses.

    Reuses `MODEL_ALIASES` / `CODEX_MODELS` / `CLAUDE_MODELS` / `AGY_MODELS`
    rather than inventing a second mapping, per this module's own contract.
    A model `MODEL_ALIASES` would itself reject returns
    ``("unrecognized-model", "unknown")`` — a caller-composed display name
    may contain spaces or parentheses, which `learning_journal`'s identifier
    validation rejects, so the raw string is never carried into the record.
    """
    normalized = MODEL_ALIASES.get(model)
    if normalized is None:
        return "unrecognized-model", "unknown"
    if normalized in CLAUDE_MODELS:
        return normalized, "claude"
    if normalized in CODEX_MODELS:
        return normalized, "codex"
    if normalized in AGY_MODELS:
        return normalized, "agy"
    return normalized, "unknown"


def make_journaled_invoke_worker(
    task: learning_journal.TaskLabel,
    *,
    root_dir: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[str, str, str], str]:
    """Build a ``(model, effort, prompt) -> str`` callable that journals every call.

    This is the seam `AdvisoryConsultation` depends on — nothing about its
    shape changes — with the journal's context (`task`, `root_dir`) closed
    over rather than threaded through it, because neither fits the
    three-argument contract. Each call to the returned callable appends
    exactly one `WorkerExecutionRecord` to the journal under `root_dir`,
    whether the underlying `invoke_worker` succeeds, exits non-zero, or
    times out.

    Retry count is always 0: `invoke_worker` performs no retries today, and
    this records that honestly rather than a value a future retry mechanism
    would imply.

    Two failure modes stay strictly separate:

    - `invoke_worker` raising is the worker's own outcome. It is journaled
      as `success=False` and then re-raised unchanged, so the caller sees
      exactly the exception it would have without instrumentation.
    - Anything going wrong in the journaling itself — record construction
      rejecting a value, or `append_journal_record` failing to write — is
      swallowed here. A broken disk, or a caller-supplied `effort` that
      does not fit the journal's enumerated vocabulary, must degrade the
      learning loop, never the invocation it was merely observing. This is
      deliberately broader than "write failures only": a validation error
      raised while *building* the record is just as much "the journal's
      problem" as an `OSError` while writing it, and neither may replace or
      mask the worker's real result.
    """

    def _journaled_invoke_worker(model: str, effort: str, prompt: str) -> str:
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
                effort=effort,  # type: ignore[arg-type]
                model_id=model_id,
                model_family=model_family,
            )
            learning_journal.append_journal_record(record, root_dir=root_dir)
        except Exception:  # noqa: BLE001, S110 - journaling must never break the observed invocation.
            pass

        if error is not None:
            raise error
        return output

    return _journaled_invoke_worker


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MODEL_ALIASES",
    "USD_PER_SECOND",
    "WORKER_MODE_TOKEN",
    "build_worker_command",
    "estimate_cost_usd",
    "invoke_worker",
    "make_journaled_invoke_worker",
]
