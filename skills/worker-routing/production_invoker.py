#!/usr/bin/env python3
"""Production implementation of AdvisoryConsultation's worker callable.

The consultation loop accepts an injected ``(model, effort, prompt) -> str``
callable so it remains fully testable offline.  This module provides the
production adapter: it resolves a provider-specific argv list and launches it
without a shell or interactive stdin.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable

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


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MODEL_ALIASES",
    "WORKER_MODE_TOKEN",
    "build_worker_command",
    "invoke_worker",
]
