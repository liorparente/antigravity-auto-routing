"""Isolated, failure-aware transport for Planner/Critic worker processes.

The transport deliberately owns no dialogue policy.  It delegates the actual
argv construction and subprocess lifecycle to :mod:`production_invoker`,
whose invocation seam supplies an explicit ``DEVNULL`` stdin, a per-process
environment, and a native timeout watchdog.  Keeping that boundary here makes
the dialogue loop injectable while ensuring production workers never inherit
an interactive terminal.
"""
from __future__ import annotations

import enum
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

if __package__:
    from . import production_invoker
    from .debate_state_machine import CriticResponse
else:
    import production_invoker  # type: ignore[no-redef]
    from debate_state_machine import CriticResponse  # type: ignore[no-redef]


def _current_production_invoker() -> Any:
    if __package__:
        package_invoker = sys.modules.get(f"{__package__}.production_invoker")
        if package_invoker is not None:
            return package_invoker
    return sys.modules.get("production_invoker", production_invoker)


__all__ = [
    "DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS",
    "DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "ESCALATION_FAILURE_THRESHOLD",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "CriticResponse",
    "DebateTransport",
    "ProcessSupervisor",
    "RecurringFailureNotifier",
    "Runner",
    "SyncWorkerProcess",
]

ESCALATION_FAILURE_THRESHOLD = 2
DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD = 3
DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0
Runner = Callable[..., subprocess.CompletedProcess[str]]


class SyncWorkerProcess(Protocol):
    """The slice of `subprocess.Popen` this module depends on.

    Naming the seam as a `Protocol` (rather than importing
    `subprocess.Popen[str]` directly) is what makes it fakeable: a test
    double only has to implement `communicate`/`wait`/`kill`/`pid`, not
    subclass the real `Popen`. Mirrors `production_invoker.AsyncWorkerProcess`,
    the same seam for the asyncio subprocess API.
    """

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def communicate(self, timeout: float | None = ...) -> tuple[str, str]: ...

    def wait(self, timeout: float | None = ...) -> int: ...

    def kill(self) -> None: ...


class ProcessSupervisor:
    """A `Runner` that isolates each worker in its own process group.

    `production_invoker.invoke_worker` calls its `runner` exactly like
    `subprocess.run`: positional `command`, then `check`/`capture_output`/
    `text`/`stdin`/`env`/`timeout` as keywords. `ProcessSupervisor.__call__`
    matches that shape so it drops in as a `Runner` unchanged — the only
    difference a caller observes is what happens on timeout.

    `subprocess.run`'s own timeout handling only ever reaches the direct
    child: a CLI worker that forks (or execs a wrapper script that forks)
    can leave grandchildren running past the timeout, orphaned in the
    process table. Spawning with `start_new_session=True` (POSIX) puts the
    child and everything it forks in a fresh process group headed by the
    child's own pid, so a timeout can be answered with one `os.killpg`
    against that whole group instead of a single `Popen.kill()` that only
    ever reaches the direct child.
    """

    def __init__(
        self,
        # `type[Popen[AnyStr]]`'s own generic can't unify with the
        # narrower `SyncWorkerProcess` seam statically -- the same gap
        # `subprocess.Popen` always has against a Protocol seam.
        popen: Callable[..., SyncWorkerProcess] = subprocess.Popen,  # type: ignore[assignment]
        *,
        kill_grace_seconds: float = 5.0,
    ) -> None:
        if kill_grace_seconds < 0:
            raise ValueError("kill_grace_seconds must be greater than or equal to 0")
        self._popen = popen
        self._kill_grace_seconds = kill_grace_seconds

    def __call__(
        self,
        command: Sequence[str],
        *,
        check: bool = False,
        capture_output: bool = True,
        text: bool = True,
        stdin: int | None = subprocess.DEVNULL,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        **extra_popen_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        argv = list(command)
        popen_kwargs: dict[str, Any] = {
            "stdin": stdin,
            "stdout": subprocess.PIPE if capture_output else None,
            "stderr": subprocess.PIPE if capture_output else None,
            "text": text,
            "env": env,
            **extra_popen_kwargs,
        }
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True

        proc = self._popen(argv, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self._kill_process_group(proc)
            # The child is already dead or dying by this point, so a second
            # `communicate()` only drains whatever partial output it wrote
            # before termination -- it must never block the caller further.
            stdout, stderr = proc.communicate()
            # `communicate(timeout=timeout)` cannot raise `TimeoutExpired`
            # with `timeout=None` (a `None` timeout blocks forever), so this
            # branch is only ever reached with a real deadline in hand.
            assert timeout is not None
            raise subprocess.TimeoutExpired(
                argv, timeout, output=stdout, stderr=stderr
            ) from error

        # `communicate()` returning means the child has exited, so
        # `returncode` is always set by this point -- the `Optional` in the
        # Protocol only covers the "still running" state.
        returncode = proc.returncode
        assert returncode is not None
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode, argv, output=stdout, stderr=stderr
            )
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    def _kill_process_group(self, proc: SyncWorkerProcess) -> None:
        """Escalate SIGTERM then SIGKILL against ``proc``'s whole process group.

        Every step is guarded against `ProcessLookupError`: the process (or
        the group) may already have exited in the race between the timeout
        firing and this method running, and that race losing is a success,
        not a failure, for a supervisor whose only job is "make sure it's
        dead."
        """
        if os.name != "posix":
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return

        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            proc.wait(timeout=self._kill_grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class CircuitState(enum.Enum):
    """A model's health as tracked by `CircuitBreaker`."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(RuntimeError):
    """Raised by `DebateTransport.invoke_worker` for a circuit-broken model."""


@dataclass
class _CircuitEntry:
    """A single model's circuit-breaker bookkeeping, grouped so state stays coherent."""

    consecutive_failures: int = 0
    state: CircuitState = field(default=CircuitState.CLOSED)
    opened_at: float = 0.0
    probing: bool = False


class CircuitBreaker:
    """Per-model failure tracking that stops calling a model that keeps failing.

    Three states per model, transitioned under a single lock so concurrent
    Planner/Critic invocations never race each other's bookkeeping:

    - **CLOSED** (healthy): every call is allowed through; each failure
      increments a consecutive-failure counter and each success resets it.
    - **OPEN** (degraded): reached once that counter hits
      ``failure_threshold``. Calls are rejected outright -- no subprocess is
      spawned -- until ``cooldown_seconds`` have elapsed, which is what lets
      a caller iterating a fallback chain move to the next worker
      immediately instead of paying for another timeout against one that is
      already known to be down.
    - **HALF_OPEN** (probing): the first `is_available` check after the
      cooldown elapses flips the model into this state and returns `True`
      to exactly that one caller, so exactly one probe request goes out.
      Every other concurrent caller sees `False` until the probe resolves.
      `record_success` closes the circuit; `record_failure` reopens it with
      a fresh cooldown window.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be greater than 0")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _CircuitEntry] = {}

    def record_success(self, model: str) -> None:
        """Close the circuit for ``model``, clearing any failure history."""
        with self._lock:
            self._entries[model] = _CircuitEntry()

    def record_failure(self, model: str) -> None:
        """Record one failure for ``model``, opening the circuit at the threshold.

        A failure while `model` is being probed (`HALF_OPEN`) reopens the
        circuit immediately regardless of the consecutive count, since a
        failed probe is definitive evidence the model is still unhealthy --
        it must not have to fail `failure_threshold` more times before the
        breaker trusts that result.
        """
        with self._lock:
            entry = self._entries.setdefault(model, _CircuitEntry())
            was_probing = entry.probing
            entry.probing = False
            entry.consecutive_failures += 1
            if was_probing or entry.consecutive_failures >= self.failure_threshold:
                entry.state = CircuitState.OPEN
                entry.opened_at = self._clock()

    def is_available(self, model: str) -> bool:
        """True if a caller may invoke ``model`` right now.

        Also performs the CLOSED/OPEN/HALF_OPEN state transition: an OPEN
        model whose cooldown has elapsed flips to HALF_OPEN and returns
        `True` to this one caller, atomically under the same lock, so two
        threads racing this check can never both receive the single probe
        slot.
        """
        with self._lock:
            entry = self._entries.setdefault(model, _CircuitEntry())
            if entry.state == CircuitState.CLOSED:
                return True
            if entry.state == CircuitState.HALF_OPEN:
                return False
            if self._clock() - entry.opened_at < self.cooldown_seconds:
                return False
            entry.state = CircuitState.HALF_OPEN
            entry.probing = True
            return True

    def get_status(self, model: str) -> CircuitState:
        """The last-recorded state for ``model`` (CLOSED if never seen)."""
        with self._lock:
            entry = self._entries.get(model)
            return entry.state if entry is not None else CircuitState.CLOSED


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
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.notifier = notifier if notifier is not None else RecurringFailureNotifier()
        self.root_dir = Path(root_dir) if root_dir is not None else None
        self.circuit_breaker = circuit_breaker if circuit_breaker is not None else CircuitBreaker()

    def invoke_worker(self, model: str, effort: str, prompt: str) -> str:
        """Run one worker through the production subprocess transport.

        ``production_invoker.invoke_worker`` is intentionally the sole
        process implementation: it uses no shell, supplies ``DEVNULL`` as
        stdin (the programmatic equivalent of ``< /dev/null``), and passes a
        native ``subprocess.run`` timeout watchdog to the child process.

        A circuit-broken ``model`` is rejected before any process is
        spawned, with a descriptive `CircuitBreakerOpenError` -- a caller
        walking a fallback chain sees a fast, explicit failure instead of
        stalling on a worker already known to be unhealthy.
        """
        if not self.circuit_breaker.is_available(model):
            status = self.circuit_breaker.get_status(model)
            raise CircuitBreakerOpenError(
                f"Worker {model!r} is circuit-broken (state={status.value}); "
                "skipping invocation until its cooldown elapses"
            )
        kwargs: dict[str, Any] = {"timeout": self.timeout_seconds}
        if self.runner is not None:
            kwargs["runner"] = self.runner
        try:
            output = _current_production_invoker().invoke_worker(model, effort, prompt, **kwargs)
        except Exception as exc:
            self.notifier.record_failure(model, str(exc), self.root_dir)
            self.circuit_breaker.record_failure(model)
            raise
        self.notifier.record_success(model)
        self.circuit_breaker.record_success(model)
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
