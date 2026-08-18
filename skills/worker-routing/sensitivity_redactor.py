"""Pure sensitivity detection and safe task identities.

The returned marker is always the configured marker, never surrounding task
text.  Halted task identities are random rather than derived from input.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from collections.abc import Callable, Sequence

SENSITIVITY_MARKERS: tuple[str, ...] = (
    "AGY_CALIBRATION_SECRET", "api_key", "sk-", "bearer ",
    "BEGIN PRIVATE KEY", "password", "secret", "[SENSITIVE]",
)


@dataclass(frozen=True)
class TaskIdentity:
    """Safe identity returned by a sensitivity decision.

    ``marker`` is the matched configured marker or ``None``; task text is
    intentionally never retained by this value.
    """

    task_id: str
    sensitivity_halted: bool
    marker: str | None
    caller_supplied: bool


def scan_sensitivity_markers(text: str, markers: Sequence[str] = SENSITIVITY_MARKERS) -> str | None:
    """Return the first configured marker contained in ``text`` (case-insensitive)."""
    lowered = text.lower()
    for marker in markers:
        if marker.lower() in lowered:
            return marker
    return None


def derive_safe_task_identity(
    task_description: str,
    task_id: str | None = None,
    *,
    outcome: str | None = None,
    token_factory: Callable[[int], str] = secrets.token_hex,
) -> TaskIdentity:
    """Return a safe identity without deriving halted sensitive task identities.

    The optional factory keeps the random boundary deterministic in unit tests.
    """
    if task_id is not None:
        marker = detect_sensitivity_marker(task_description)
        return TaskIdentity(task_id, marker is not None, marker, True)
    marker = detect_sensitivity_marker(task_description)
    if marker is not None and outcome == "sensitivity_halt":
        return TaskIdentity(token_factory(8), True, marker, False)
    if outcome == "canary":
        return TaskIdentity(token_factory(8), marker is not None, marker, False)
    if outcome == "sensitivity_halt":
        return TaskIdentity(token_factory(8), marker is not None, marker, False)
    import hashlib

    return TaskIdentity(
        hashlib.sha256(task_description.encode("utf-8")).hexdigest()[:16],
        marker is not None,
        marker,
        False,
    )


def detect_sensitivity_marker(text: str) -> str | None:
    """Compatibility spelling for :func:`scan_sensitivity_markers`."""
    return scan_sensitivity_markers(text)


_detect_sensitivity_marker = detect_sensitivity_marker
