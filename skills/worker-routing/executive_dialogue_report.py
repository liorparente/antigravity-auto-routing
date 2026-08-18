"""Pure executive-facing formatting for advisory dialogue outcomes."""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any


def _load_sibling(name: str) -> Any:
    """Load a sibling module when this module was imported directly by path."""
    try:
        return __import__(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_dialogue_degradation = _load_sibling("dialogue_degradation")
_dialogue_contracts = _load_sibling("dialogue_contracts")

if TYPE_CHECKING:
    from dialogue_contracts import Occasion
    from dialogue_degradation import DegradationRung


def format_budget_degradation_alert(
    rung: DegradationRung,
    session_spend_so_far: int,
    cap: int,
    *,
    label: str | None = None,
) -> str | None:
    """Render the operator alert for an active budget-degradation rung."""
    if rung <= 0:
        return None

    resolved_label = label or _dialogue_degradation._DEGRADATION_RUNG_LABELS.get(
        rung, "budget degraded"
    )
    return (
        f"⚠️ [BUDGET DEGRADATION ALERT - Rung {rung}: {resolved_label}]\n"
        f"Session dialogue spend has exceeded cap ({session_spend_so_far}/{cap}).\n"
        "Reduced debate depth active. Operator action required: [CONTINUE | PAUSE].\n"
    )


def render_executive_summary(
    outcome: str,
    occasion: Occasion,
    rounds_used: int,
    max_rounds: int,
    planner_model: str,
    critic_model: str,
    *,
    session_spend: int = 1,
    plan_path: str = "implementation_plan.md",
    error: str | None = None,
) -> tuple[str, str, str]:
    """Return the three stable lines used in an executive dialogue summary."""
    status_line = (
        f"[EXECUTIVE SUMMARY] Status: {outcome.upper()} "
        f"(Rounds: {rounds_used}/{max_rounds}) | Occasion: {occasion}"
    )
    models_line = (
        f"Models: Planner={planner_model} | Critic={critic_model} "
        f"| Spend={session_spend} dialogue(s)"
    )
    if outcome == "consensus":
        outcome_line = (
            f"Outcome: Approved plan; persistence failed ({error})"
            if error
            else f"Outcome: Approved plan stored at {plan_path}"
        )
    elif error:
        outcome_line = f"Outcome: Unresolved ({outcome}) - Error: {error}"
    else:
        outcome_line = f"Outcome: Unresolved ({outcome}) - Review required"
    return status_line, models_line, outcome_line


@dataclass(frozen=True)
class ExecutiveDialogueReport:
    """An immutable executive summary with its optional budget alert."""

    summary_lines: tuple[str, str, str]
    budget_alert: str | None = None

    def render(self) -> str:
        """Render the summary followed by its alert, when one is active."""
        report = "\n".join(self.summary_lines)
        return f"{report}\n{self.budget_alert}" if self.budget_alert else report
