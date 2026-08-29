"""Transitional backward-compatible alias for critical_dialogue.

The complete CriticalDialogue engine now lives in ``critical_dialogue``.
This module remains only until Issue 40 / Ticket 05 removes the legacy path.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if __package__:
    from . import critical_dialogue as _critical_dialogue
else:
    import critical_dialogue as _critical_dialogue  # type: ignore[no-redef]

if TYPE_CHECKING:
    from .critical_dialogue import AdvisoryDebateResult, CanaryFixture  # noqa: F401

# Preserve legacy module identity, including monkeypatch seams, by returning
# the canonical module object for imports through this transitional path.
sys.modules[__name__] = _critical_dialogue
