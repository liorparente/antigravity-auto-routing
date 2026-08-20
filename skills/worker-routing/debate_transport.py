"""Isolated, failure-aware transport for Planner/Critic worker processes.

The transport deliberately owns no dialogue policy.  It delegates the actual
argv construction and subprocess lifecycle to :mod:`production_invoker`,
whose invocation seam supplies an explicit ``DEVNULL`` stdin, a per-process
environment, and a native timeout watchdog.  Keeping that boundary here makes
the dialogue loop injectable while ensuring production workers never inherit
an interactive terminal.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from debate_state_machine import CriticResponse


def _load_sibling(name: str) -> Any:
    """Load a sibling whether this file is imported or loaded by path."""
    try:
        return __import__(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
    sibling_dir = str(Path(__file__).resolve().parent)
    if sibling_dir not in sys.path:
        # ``production_invoker`` has an ordinary sibling import
        # (``learning_journal``), so path-loaded harnesses need this same
        # directory on the standard importer path before it is executed.
        sys.path.insert(0, sibling_dir)
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_dialogue_contracts = _load_sibling("dialogue_contracts")
_debate_state_machine = _load_sibling("debate_state_machine")
_production_invoker = _load_sibling("production_invoker")


def _current_production_invoker() -> Any:
    """Resolve ``production_invoker`` fresh from ``sys.modules`` on every call.

    A harness that path-loads its own copy of ``production_invoker`` (a test
    module doing the same by-path load this file's own ``_load_sibling``
    does) and registers it under ``sys.modules["production_invoker"]`` after
    this module was first imported would otherwise leave the module-level
    ``_production_invoker`` pointing at a stale, unpatchable copy — a caller
    monkeypatching ``production_invoker.invoke_worker`` for a hermetic test
    would silently miss every call this transport makes. Re-resolving here
    mirrors what a fresh ``import production_invoker`` statement does on
    every execution: it always binds to whatever ``sys.modules`` currently
    holds, never a snapshot from this module's own load time.
    """
    return sys.modules.get("production_invoker", _production_invoker)


if not TYPE_CHECKING:
    CriticResponse = _debate_state_machine.CriticResponse
ESCALATION_FAILURE_THRESHOLD = 2
Runner = Callable[..., subprocess.CompletedProcess[str]]


class RecurringFailureNotifier:
    """Track worker failures and surface repeated outages without overwriting logs."""

    def __init__(self, threshold: int = ESCALATION_FAILURE_THRESHOLD) -> None:
        if threshold < 1:
            raise ValueError("threshold must be at least 1")
        self.threshold = threshold
        self._consecutive_failures: dict[str, int] = {}
        self._lock = threading.Lock()

    def record_success(self, model: str) -> None:
        """Reset ``model`` after a successful invocation."""
        with self._lock:
            self._consecutive_failures.pop(model, None)

    def record_failure(
        self, model: str, error: str, root_dir: Path | None = None
    ) -> str | None:
        """Record a failure and append a durable alert once the threshold is met."""
        with self._lock:
            count = self._consecutive_failures.get(model, 0) + 1
            self._consecutive_failures[model] = count

        if count < self.threshold:
            return None

        appended = self._append_error(root_dir, model, count, error)
        destination = "ERRORS.md" if appended else "ERRORS.md could not be written"
        return (
            f"[RECURRING WORKER FAILURE ALERT: Model {model!r} has failed "
            f"{count} times consecutively. Details appended to {destination}]"
        )

    @staticmethod
    def _append_error(root_dir: Path | None, model: str, count: int, error: str) -> bool:
        """Best-effort append only; failure reporting must never mask the worker error."""
        if root_dir is None:
            return False
        try:
            root = Path(root_dir)
            if not root.is_dir() or not os.access(root, os.W_OK):
                return False
            timestamp = datetime.now(timezone.utc).isoformat()
            entry = (
                "\n## Recurring worker failure\n\n"
                f"- Timestamp (UTC): {timestamp}\n"
                f"- Model: `{model}`\n"
                f"- Consecutive failures: {count}\n"
                f"- Error: {error}\n"
            )
            with (root / "ERRORS.md").open("a", encoding="utf-8") as handle:
                handle.write(entry)
            return True
        except OSError:
            return False


class DebateTransport:
    """Invoke isolated workers and turn Critic transport failures into abstentions."""

    def __init__(
        self,
        runner: Runner | None = None,
        timeout_seconds: float = 300.0,
        notifier: RecurringFailureNotifier | None = None,
        root_dir: Path | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.notifier = notifier if notifier is not None else RecurringFailureNotifier()
        self.root_dir = Path(root_dir) if root_dir is not None else None

    def invoke_worker(self, model: str, effort: str, prompt: str) -> str:
        """Run one worker through the production subprocess transport.

        ``production_invoker.invoke_worker`` is intentionally the sole
        process implementation: it uses no shell, supplies ``DEVNULL`` as
        stdin (the programmatic equivalent of ``< /dev/null``), and passes a
        native ``subprocess.run`` timeout watchdog to the child process.
        """
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.runner is not None:
            kwargs["runner"] = self.runner
        try:
            output = _current_production_invoker().invoke_worker(model, effort, prompt, **kwargs)
        except Exception as exc:
            self.notifier.record_failure(model, str(exc), self.root_dir)
            raise
        self.notifier.record_success(model)
        return output

    def invoke_critic_safe(
        self, model: str, effort: str, prompt: str, critic_id: str = "critic"
    ) -> CriticResponse:
        """Return an abstention instead of allowing a transient Critic failure to escape."""
        try:
            output = self.invoke_worker(model, effort, prompt)
        except Exception as exc:  # noqa: BLE001 - transport is the safety boundary.
            return CriticResponse(
                critic_id=critic_id,
                response=f"[TRANSPORT ERROR: {exc}]",
                verdict="abstain",
                confidence=0.0,
            )

        payload = _current_production_invoker().extract_review_payload(output)
        vote = str(payload.get("vote", "abstain")).strip().casefold()
        verdict = vote if vote in {"approve", "revise"} else "abstain"
        confidence = float(payload.get("confidence", 0.0))
        if verdict == "abstain":
            confidence = 0.0
        return CriticResponse(
            critic_id=critic_id,
            response=output,
            verdict=verdict,
            confidence=confidence,
            candidate_hash=payload.get("candidate_hash"),
            findings=tuple(payload.get("findings", ())),
        )


__all__ = [
    "ESCALATION_FAILURE_THRESHOLD",
    "CriticResponse",
    "DebateTransport",
    "RecurringFailureNotifier",
]
