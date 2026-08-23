"""Deterministic policy for the advisory dialogue budget ladder.

The consultation orchestrator deliberately does not own session state: its
caller tracks how many dialogues have already run and supplies that count.
This module converts that caller-owned spend counter and the configured
per-session cap into a degradation rung.  Keeping this calculation separate
means budget-policy changes cannot accidentally alter debate transitions,
transcript formatting, or worker invocation.

Spend is measured in dialogues rather than rounds, tokens, or dollars.  A
dialogue is the thing the budget promises to constrain; callers that need a
richer cost model can derive an integer spend before calling this module.
The four bands are each one cap wide: below one cap is normal operation,
then reduced rounds, then a cheaper roster and effort, then a complete skip.
The rungs compound: rung 2 retains rung 1's round reduction.  A zero cap
therefore correctly means every dialogue is skipped.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

if __package__:
    from . import routing_config
else:
    import routing_config  # type: ignore[no-redef]

__all__ = [
    "BUDGET_DEGRADATION_MARKER",
    "DEFAULT_SESSION_DIALOGUE_CAP",
    "DegradationLadderState",
    "DegradationRung",
    "resolve_degradation_rung",
]

DegradationRung = Literal[0, 1, 2, 3]

DEFAULT_SESSION_DIALOGUE_CAP = 10
_DEGRADED_ROUND_CAP = 1
_DEGRADED_EFFORT = "low"
_DEFAULT_DEGRADED_ROSTER_MODEL = "Codex 5.6 Terra"
BUDGET_DEGRADATION_MARKER = "BUDGET DEGRADATION"

_DEGRADATION_RUNG_LABELS: dict[DegradationRung, str] = {
    1: "reduce rounds",
    2: "cheapen roster: model + effort",
    3: "skip the dialogue entirely",
}

_CONFIG_PATH = routing_config.ROUTING_CONFIG_PATH


def _load_dialogue_budget_config(config_path: Path) -> int:
    """Read the session dialogue cap from ``config_path`` via
    :mod:`routing_config` (ticket 42).

    The value comes from ``dialogue_budget.session_dialogue_cap`` and falls
    back to :data:`DEFAULT_SESSION_DIALOGUE_CAP` when that section or key is
    absent.  The file itself is intentionally not protected by ``try``/``except``:
    the checked-in default exists, so a missing or malformed injected path is a
    caller error that must remain visible rather than being silently ignored.
    """
    return routing_config.load_routing_config(config_path).dialogue_budget.session_dialogue_cap


def _load_degraded_roster_model(config_path: Path) -> str:
    """Read rung 2's single, cheaper model from ``light_doer.name`` via
    :mod:`routing_config` (ticket 42).

    Rung 2 must cheapen both effort and roster.  Reusing the existing
    ``light_doer`` block avoids inventing a second roster resolver whose cost
    preference would conflict with the consultation resolver's independence
    preference.  The value is an ordered ``/``-separated list, so its first
    non-blank alternative is the concrete model passed to a worker.

    Missing role data or an empty first alternative falls back to
    :data:`_DEFAULT_DEGRADED_ROSTER_MODEL`.  As with
    :func:`_load_dialogue_budget_config`, missing or malformed files raise so
    configuration mistakes do not become invisible production defaults.
    """
    legacy_role = routing_config.load_routing_config(config_path).legacy_roles.get("light_doer")
    name = legacy_role.name if legacy_role is not None else _DEFAULT_DEGRADED_ROSTER_MODEL
    primary = name.split("/")[0].strip()
    return primary or _DEFAULT_DEGRADED_ROSTER_MODEL


def resolve_degradation_rung(
    session_spend_so_far: int,
    *,
    config_path: Path = _CONFIG_PATH,
) -> DegradationRung:
    """Return the rung applicable to the next dialogue.

    With ``cap = dialogue_budget.session_dialogue_cap`` the boundaries are:

    - ``session_spend_so_far < cap``: rung 0, no degradation;
    - ``cap <= session_spend_so_far < 2 * cap``: rung 1, reduce rounds;
    - ``2 * cap <= session_spend_so_far < 3 * cap``: rung 2, cheapen roster
      and effort; and
    - ``session_spend_so_far >= 3 * cap``: rung 3, skip the dialogue.

    Lower bounds are inclusive, so spending exactly one cap reaches rung 1.
    The function is stateless and total for all integer spend values:
    negative spend is under budget by construction, while a zero cap falls
    through to rung 3 for every spend value.  The configuration is read on
    each call so injected configuration remains observable and testable.
    """
    cap = _load_dialogue_budget_config(config_path)
    if session_spend_so_far < cap:
        return 0
    if session_spend_so_far < 2 * cap:
        return 1
    if session_spend_so_far < 3 * cap:
        return 2
    return 3


@dataclass(frozen=True)
class DegradationLadderState:
    """The resolved, inspectable effects of one degradation rung.

    The coordinator owns applying this policy to its selected roster because
    sensitive dialogues may lower effort without replacing their local-only
    models.  This immutable value object records the resulting effective
    controls without adding mutable session state to the policy module:
    ``max_rounds`` is the effective round budget; ``effort`` and ``model``
    are substitutions when applicable; ``is_degraded_independence`` exposes
    the same-family review hazard; and ``is_skipped`` records rung 3's
    no-worker outcome.
    """

    rung: DegradationRung
    max_rounds: int
    effort: str | None
    model: str | None
    is_degraded_independence: bool
    is_skipped: bool
