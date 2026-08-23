"""Hermetic unit tests for the isolated debate worker transport."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import debate_transport as transport
else:
    import debate_transport as transport  # type: ignore[no-redef]


class DebateTransportTests(unittest.TestCase):
    def test_successful_invocation_returns_output_and_enforces_process_boundary(self) -> None:
        calls: list[dict[str, object]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append({"command": command, **kwargs})
            return subprocess.CompletedProcess(command, 0, stdout="worker output", stderr="")

        notifier = transport.RecurringFailureNotifier()
        worker = transport.DebateTransport(runner=runner, timeout_seconds=12.5, notifier=notifier)

        self.assertEqual(worker.invoke_worker("gpt-5.6-sol", "high", "Review"), "worker output")
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["timeout"], 12.5)
        self.assertIs(call["stdin"], subprocess.DEVNULL)
        self.assertEqual(call["env"]["IN_WORKER_ROUTING"], "true")  # type: ignore[index]
        self.assertIn("[WORKER-MODE: NESTED-EXEC]", call["command"][-1])  # type: ignore[index]

    def test_timeout_becomes_safe_abstention(self) -> None:
        def timeout_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("codex", 4.0, output=b"partial", stderr=b"stuck")

        worker = transport.DebateTransport(runner=timeout_runner, timeout_seconds=4.0)
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review", "critic-a")

        self.assertEqual(result.critic_id, "critic-a")
        self.assertEqual(result.verdict, "abstain")
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("timed out after 4.0 seconds", result.response)

    def test_unhandled_worker_exception_becomes_safe_abstention(self) -> None:
        def broken_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise OSError("worker executable missing")

        worker = transport.DebateTransport(runner=broken_runner)
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review")

        self.assertEqual(result.verdict, "abstain")
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("worker executable missing", result.response)

    def test_successful_critic_uses_normalized_vote_and_confidence(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    '{"vote": "revise", "confidence": 0.75, '
                    '"candidate_hash": "candidate-1", "findings": '
                    '[{"severity": "high", "confidence": 0.9}]}'
                ),
                stderr="",
            )

        result = transport.DebateTransport(runner=runner).invoke_critic_safe(
            "gpt-5.6-sol", "high", "Review"
        )
        self.assertEqual((result.verdict, result.confidence), ("revise", 0.75))
        self.assertEqual(result.candidate_hash, "candidate-1")
        self.assertEqual(result.findings[0]["severity"], "high")

    def test_mock_patching_intercepts_production_invoker(self) -> None:
        """A patch on ``production_invoker`` must be seen by the transport, always.

        The retired ``_load_sibling`` by-path loader could leave the transport
        holding a module identity a caller's patch never touched, so a
        monkeypatched ``invoke_worker`` would silently miss every call and a
        real subprocess would launch underneath the mock. A package-relative
        import always resolves ``production_invoker`` through ``sys.modules``,
        so ``patch.object`` on the exact module the transport calls reaches
        it -- and never falls through to a real ``subprocess.run``.
        """
        def never_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("a real subprocess must never run while production_invoker is mocked")

        worker = transport.DebateTransport(runner=never_runner)
        invoker_mod = transport._current_production_invoker()
        with patch.object(invoker_mod, "invoke_worker", return_value="mocked output") as mock_invoke:
            self.assertEqual(worker.invoke_worker("gpt-5.6-sol", "high", "Review"), "mocked output")
        mock_invoke.assert_called_once_with(
            "gpt-5.6-sol", "high", "Review", timeout=worker.timeout_seconds, runner=never_runner
        )

        with patch.object(
            transport.production_invoker, "invoke_worker", return_value="direct mock output"
        ) as direct_mock:
            self.assertEqual(worker.invoke_worker("gpt-5.6-sol", "high", "Review"), "direct mock output")
        direct_mock.assert_called_once_with(
            "gpt-5.6-sol", "high", "Review", timeout=worker.timeout_seconds, runner=never_runner
        )


class RecurringFailureNotifierTests(unittest.TestCase):
    def test_alert_triggers_at_threshold_and_repeated_errors_append(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            error_log = root / "ERRORS.md"
            error_log.write_text("# Existing notes\n", encoding="utf-8")
            notifier = transport.RecurringFailureNotifier(threshold=2)

            self.assertIsNone(notifier.record_failure("model-a", "first", root))
            alert = notifier.record_failure("model-a", "second", root)
            self.assertIn("failed 2 times consecutively", alert or "")
            notifier.record_failure("model-a", "third", root)

            contents = error_log.read_text(encoding="utf-8")
            self.assertTrue(contents.startswith("# Existing notes\n"))
            self.assertEqual(contents.count("## Recurring worker failure"), 2)
            self.assertIn("- Error: second", contents)
            self.assertIn("- Error: third", contents)

    def test_success_resets_failure_count(self) -> None:
        notifier = transport.RecurringFailureNotifier(threshold=2)
        self.assertIsNone(notifier.record_failure("model-a", "first"))
        notifier.record_success("model-a")
        self.assertIsNone(notifier.record_failure("model-a", "new first"))
        self.assertIn("failed 2 times", notifier.record_failure("model-a", "new second") or "")

    def test_creates_error_log_without_destroying_existing_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notifier = transport.RecurringFailureNotifier(threshold=1)

            notifier.record_failure("model-a", "offline", root)
            self.assertTrue((root / "ERRORS.md").is_file())
            self.assertIn("offline", (root / "ERRORS.md").read_text(encoding="utf-8"))

    def test_unwritable_destination_does_not_mask_alert(self) -> None:
        notifier = transport.RecurringFailureNotifier(threshold=1)
        with patch.object(transport.os, "access", return_value=False):
            alert = notifier.record_failure("model-a", "offline", Path.cwd())
        self.assertIn("could not be written", alert or "")


class _FakeTimeoutProc:
    """A `Popen`-shaped double whose first `communicate()` times out.

    Mirrors what a real child does once killed: the second `communicate()`
    call succeeds and drains whatever partial output survived termination.
    `wait()` is separately scriptable so tests can force the SIGTERM-only
    and SIGTERM-then-SIGKILL escalation paths.
    """

    def __init__(self, pid: int = 4242, *, wait_raises: bool = False) -> None:
        self.pid = pid
        self.returncode = -9
        self._communicate_calls = 0
        self._wait_raises = wait_raises

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self._communicate_calls += 1
        if self._communicate_calls == 1:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 0.0)
        return "partial-out", "partial-err"

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 0.0)
        return self.returncode

    def kill(self) -> None:
        pass


class ProcessSupervisorTests(unittest.TestCase):
    def test_timeout_kills_process_group_with_sigterm_first(self) -> None:
        fake_proc = _FakeTimeoutProc(pid=4242)
        killed: list[tuple[int, int]] = []

        def fake_popen(argv: list[str], **kwargs: object) -> _FakeTimeoutProc:
            self.assertTrue(kwargs.get("start_new_session"))
            return fake_proc

        supervisor = transport.ProcessSupervisor(popen=fake_popen, kill_grace_seconds=0.05)
        with patch.object(transport.os, "getpgid", return_value=9999) as fake_getpgid, \
             patch.object(transport.os, "killpg", side_effect=lambda pgid, sig: killed.append((pgid, sig))), \
             self.assertRaises(subprocess.TimeoutExpired) as ctx:
            supervisor(["worker"], timeout=0.05)

        fake_getpgid.assert_called_once_with(4242)
        self.assertEqual(killed, [(9999, signal.SIGTERM)])
        self.assertEqual(ctx.exception.output, "partial-out")
        self.assertEqual(ctx.exception.stderr, "partial-err")

    def test_escalates_to_sigkill_when_group_survives_sigterm(self) -> None:
        fake_proc = _FakeTimeoutProc(pid=555, wait_raises=True)
        killed: list[tuple[int, int]] = []

        def fake_popen(argv: list[str], **kwargs: object) -> _FakeTimeoutProc:
            return fake_proc

        supervisor = transport.ProcessSupervisor(popen=fake_popen, kill_grace_seconds=0.01)
        with patch.object(transport.os, "getpgid", return_value=4321), \
             patch.object(transport.os, "killpg", side_effect=lambda pgid, sig: killed.append((pgid, sig))), \
             self.assertRaises(subprocess.TimeoutExpired):
            supervisor(["worker"], timeout=0.01)

        self.assertEqual(killed, [(4321, signal.SIGTERM), (4321, signal.SIGKILL)])

    def test_process_group_already_reaped_is_not_an_error(self) -> None:
        fake_proc = _FakeTimeoutProc(pid=777)

        def fake_popen(argv: list[str], **kwargs: object) -> _FakeTimeoutProc:
            return fake_proc

        supervisor = transport.ProcessSupervisor(popen=fake_popen)
        with patch.object(transport.os, "getpgid", side_effect=ProcessLookupError), \
             patch.object(transport.os, "killpg") as fake_killpg, \
             self.assertRaises(subprocess.TimeoutExpired):
            supervisor(["worker"], timeout=0.01)

        fake_killpg.assert_not_called()

    def test_successful_call_passes_through_a_completed_process(self) -> None:
        class _SuccessfulProc:
            pid = 1
            returncode = 0

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                return "ok", ""

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self) -> None:
                pass

        def fake_popen(argv: list[str], **kwargs: object) -> _SuccessfulProc:
            return _SuccessfulProc()

        supervisor = transport.ProcessSupervisor(popen=fake_popen)
        result = supervisor(["worker"], timeout=5.0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "ok")

    @unittest.skipUnless(os.name == "posix", "process-group kill is only implemented for POSIX")
    def test_real_subprocess_group_is_terminated_on_timeout(self) -> None:
        """End-to-end: a grandchild spawned by the timed-out worker also dies."""
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            script = (
                "import subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
                "time.sleep(30)\n"
            )
            supervisor = transport.ProcessSupervisor(kill_grace_seconds=2.0)

            with self.assertRaises(subprocess.TimeoutExpired):
                supervisor([sys.executable, "-c", script], timeout=1.0)

            deadline = time.monotonic() + 5.0
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(pid_file.exists(), "grandchild never reported its pid before timeout")
            child_pid = int(pid_file.read_text().strip())

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            else:
                self.fail("grandchild process outlived its process group's timeout kill")


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_after_threshold_then_recovers_through_half_open_probe(self) -> None:
        clock = {"now": 0.0}
        breaker = transport.CircuitBreaker(
            failure_threshold=3, cooldown_seconds=60.0, clock=lambda: clock["now"]
        )
        model = "gpt-5.6-sol"

        self.assertTrue(breaker.is_available(model))
        breaker.record_failure(model)
        breaker.record_failure(model)
        self.assertEqual(breaker.get_status(model), transport.CircuitState.CLOSED)
        breaker.record_failure(model)
        self.assertEqual(breaker.get_status(model), transport.CircuitState.OPEN)
        self.assertFalse(breaker.is_available(model))

        clock["now"] += 59.0
        self.assertFalse(breaker.is_available(model))

        clock["now"] += 1.0
        self.assertTrue(breaker.is_available(model))
        self.assertEqual(breaker.get_status(model), transport.CircuitState.HALF_OPEN)
        # A second, concurrent caller must not also get a probe slot.
        self.assertFalse(breaker.is_available(model))

        breaker.record_success(model)
        self.assertEqual(breaker.get_status(model), transport.CircuitState.CLOSED)
        self.assertTrue(breaker.is_available(model))
        self.assertEqual(breaker.get_status(model), transport.CircuitState.CLOSED)

    def test_failed_probe_reopens_circuit_with_a_fresh_cooldown(self) -> None:
        clock = {"now": 0.0}
        breaker = transport.CircuitBreaker(
            failure_threshold=3, cooldown_seconds=10.0, clock=lambda: clock["now"]
        )
        model = "gpt-5.6-sol"
        for _ in range(3):
            breaker.record_failure(model)
        self.assertEqual(breaker.get_status(model), transport.CircuitState.OPEN)

        clock["now"] += 10.0
        self.assertTrue(breaker.is_available(model))  # enters HALF_OPEN

        breaker.record_failure(model)
        self.assertEqual(breaker.get_status(model), transport.CircuitState.OPEN)
        self.assertFalse(breaker.is_available(model))

        clock["now"] += 9.0
        self.assertFalse(breaker.is_available(model))
        clock["now"] += 1.0
        self.assertTrue(breaker.is_available(model))

    def test_independent_models_do_not_affect_each_other(self) -> None:
        breaker = transport.CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0)
        breaker.record_failure("model-a")
        self.assertFalse(breaker.is_available("model-a"))
        self.assertTrue(breaker.is_available("model-b"))

    def test_rejects_invalid_configuration(self) -> None:
        with self.assertRaises(ValueError):
            transport.CircuitBreaker(failure_threshold=0)
        with self.assertRaises(ValueError):
            transport.CircuitBreaker(cooldown_seconds=0)

    def test_thread_safe_under_concurrent_failures(self) -> None:
        breaker = transport.CircuitBreaker(failure_threshold=1_000, cooldown_seconds=60.0)
        model = "gpt-5.6-sol"

        def hammer() -> None:
            for _ in range(200):
                breaker.record_failure(model)

        threads = [threading.Thread(target=hammer) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # 5 threads x 200 failures each = exactly `failure_threshold` failures.
        # If the shared counter raced, the circuit would open early or never;
        # observing OPEN here (via public behavior only) confirms every
        # increment was serialized correctly under concurrent access.
        self.assertEqual(breaker.get_status(model), transport.CircuitState.OPEN)
        self.assertFalse(breaker.is_available(model))


class DebateTransportCircuitBreakerTests(unittest.TestCase):
    def test_repeated_failures_open_the_circuit_and_stop_invoking_the_runner(self) -> None:
        call_count = {"n": 0}

        def failing_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            call_count["n"] += 1
            raise OSError("worker executable missing")

        breaker = transport.CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
        worker = transport.DebateTransport(runner=failing_runner, circuit_breaker=breaker)

        for _ in range(3):
            result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review")
            self.assertEqual(result.verdict, "abstain")

        self.assertEqual(call_count["n"], 3)
        self.assertEqual(breaker.get_status("gpt-5.6-sol"), transport.CircuitState.OPEN)

        # The circuit is open: the fourth call must short-circuit before the
        # runner is invoked again.
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review")
        self.assertEqual(result.verdict, "abstain")
        self.assertIn("circuit-broken", result.response)
        self.assertEqual(call_count["n"], 3)

    def test_invoke_worker_raises_circuit_breaker_open_error_directly(self) -> None:
        breaker = transport.CircuitBreaker(failure_threshold=1, cooldown_seconds=60.0)
        breaker.record_failure("gpt-5.6-sol")

        def never_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise AssertionError("runner must not be called for a circuit-broken model")

        worker = transport.DebateTransport(runner=never_runner, circuit_breaker=breaker)
        with self.assertRaises(transport.CircuitBreakerOpenError):
            worker.invoke_worker("gpt-5.6-sol", "high", "Review")

    def test_cooldown_expiry_allows_a_recovery_probe_to_close_the_circuit(self) -> None:
        clock = {"now": 0.0}
        breaker = transport.CircuitBreaker(
            failure_threshold=1, cooldown_seconds=30.0, clock=lambda: clock["now"]
        )
        outcomes = iter([OSError("down"), None])

        def flaky_runner(
            command: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            outcome = next(outcomes)
            if outcome is not None:
                raise outcome
            return subprocess.CompletedProcess(command, 0, stdout="recovered", stderr="")

        worker = transport.DebateTransport(runner=flaky_runner, circuit_breaker=breaker)

        with self.assertRaises(OSError):
            worker.invoke_worker("gpt-5.6-sol", "high", "Review")
        self.assertEqual(breaker.get_status("gpt-5.6-sol"), transport.CircuitState.OPEN)

        with self.assertRaises(transport.CircuitBreakerOpenError):
            worker.invoke_worker("gpt-5.6-sol", "high", "Review")

        clock["now"] += 30.0
        self.assertEqual(worker.invoke_worker("gpt-5.6-sol", "high", "Review"), "recovered")
        self.assertEqual(breaker.get_status("gpt-5.6-sol"), transport.CircuitState.CLOSED)

    def test_default_circuit_breaker_never_blocks_existing_callers(self) -> None:
        """Backwards compatibility: a `DebateTransport` built without an
        explicit `circuit_breaker` behaves exactly as before for a single
        failure, since the default threshold is 3."""

        def timeout_runner(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("codex", 4.0, output=b"partial", stderr=b"stuck")

        worker = transport.DebateTransport(runner=timeout_runner, timeout_seconds=4.0)
        result = worker.invoke_critic_safe("gpt-5.6-sol", "high", "Review", "critic-a")
        self.assertEqual(result.verdict, "abstain")
        self.assertIn("timed out after 4.0 seconds", result.response)
        self.assertEqual(worker.circuit_breaker.get_status("gpt-5.6-sol"), transport.CircuitState.CLOSED)


if __name__ == "__main__":
    unittest.main()
