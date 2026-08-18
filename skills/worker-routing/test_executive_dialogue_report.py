"""Hermetic tests for executive dialogue reporting helpers."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any


def _load_module(name: str) -> Any:
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("dialogue_degradation")
executive_dialogue_report = _load_module("executive_dialogue_report")


class BudgetDegradationAlertTests(unittest.TestCase):
    def test_rung_zero_has_no_alert(self) -> None:
        self.assertIsNone(
            executive_dialogue_report.format_budget_degradation_alert(0, 10, 10)
        )

    def test_each_active_rung_uses_its_policy_label(self) -> None:
        for rung, label in (
            (1, "reduce rounds"),
            (2, "cheapen roster: model + effort"),
            (3, "skip the dialogue entirely"),
        ):
            with self.subTest(rung=rung):
                self.assertEqual(
                    executive_dialogue_report.format_budget_degradation_alert(
                        rung, 10, 10
                    ),
                    f"⚠️ [BUDGET DEGRADATION ALERT - Rung {rung}: {label}]\n"
                    "Session dialogue spend has exceeded cap (10/10).\n"
                    "Reduced debate depth active. Operator action required: "
                    "[CONTINUE | PAUSE].\n",
                )


class ExecutiveSummaryTests(unittest.TestCase):
    def test_summary_has_three_exact_lines_for_each_outcome(self) -> None:
        cases = (
            ("consensus", None, "Outcome: Approved plan stored at implementation_plan.md"),
            ("stalemate", None, "Outcome: Unresolved (stalemate) - Review required"),
            (
                "sensitivity_halt",
                None,
                "Outcome: Unresolved (sensitivity_halt) - Review required",
            ),
            ("error", "worker unavailable", "Outcome: Unresolved (error) - Error: worker unavailable"),
        )
        for outcome, error, expected_outcome_line in cases:
            with self.subTest(outcome=outcome):
                lines = executive_dialogue_report.render_executive_summary(
                    outcome,
                    "plan-review",
                    1,
                    3,
                    "Planner",
                    "Critic",
                    error=error,
                )
                self.assertEqual(len(lines), 3)
                self.assertEqual(
                    lines[0],
                    f"[EXECUTIVE SUMMARY] Status: {outcome.upper()} "
                    "(Rounds: 1/3) | Occasion: plan-review",
                )
                self.assertEqual(
                    lines[1], "Models: Planner=Planner | Critic=Critic | Spend=1 dialogue(s)"
                )
                self.assertEqual(lines[2], expected_outcome_line)

    def test_report_rendering_appends_the_alert_after_summary(self) -> None:
        lines = executive_dialogue_report.render_executive_summary(
            "consensus", "ambiguity", 1, 1, "Planner", "Critic"
        )

    def test_consensus_with_persistence_error_does_not_claim_artifact_was_written(self) -> None:
        lines = executive_dialogue_report.render_executive_summary(
            "consensus", "ambiguity", 1, 1, "Planner", "Critic", error="disk full"
        )

        self.assertEqual(
            lines[2], "Outcome: Approved plan; persistence failed (disk full)"
        )
        alert = executive_dialogue_report.format_budget_degradation_alert(1, 10, 10)
        self.assertEqual(
            executive_dialogue_report.ExecutiveDialogueReport(lines, alert).render(),
            "\n".join(lines) + "\n" + alert,
        )

    def test_standalone_import_loads_its_siblings(self) -> None:
        for name in ("dialogue_degradation", "dialogue_contracts", "executive_dialogue_report"):
            sys.modules.pop(name, None)

        standalone = _load_module("executive_dialogue_report")

        self.assertIsNotNone(standalone._dialogue_degradation)
        self.assertIsNotNone(standalone._dialogue_contracts)


if __name__ == "__main__":
    unittest.main()
