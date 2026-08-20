"""Focused, hermetic tests for the dialogue budget degradation policy."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from . import dialogue_degradation
except (ImportError, ValueError):
    import dialogue_degradation


class DegradationRungTests(unittest.TestCase):
    def test_default_cap_resolves_every_rung_at_its_boundary(self) -> None:
        cap = dialogue_degradation.DEFAULT_SESSION_DIALOGUE_CAP

        self.assertEqual(dialogue_degradation.resolve_degradation_rung(cap - 1), 0)
        self.assertEqual(dialogue_degradation.resolve_degradation_rung(cap), 1)
        self.assertEqual(dialogue_degradation.resolve_degradation_rung(2 * cap), 2)
        self.assertEqual(dialogue_degradation.resolve_degradation_rung(3 * cap), 3)

    def test_custom_cap_resolves_every_rung(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "routing-config.json"
            config_path.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 2}}),
                encoding="utf-8",
            )

            observed = [
                dialogue_degradation.resolve_degradation_rung(
                    spend, config_path=config_path
                )
                for spend in range(7)
            ]

        self.assertEqual(observed, [0, 0, 1, 1, 2, 2, 3])

    def test_negative_spend_is_under_budget_and_zero_cap_skips(self) -> None:
        self.assertEqual(dialogue_degradation.resolve_degradation_rung(-1), 0)

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "routing-config.json"
            config_path.write_text(
                json.dumps({"dialogue_budget": {"session_dialogue_cap": 0}}),
                encoding="utf-8",
            )
            self.assertEqual(
                dialogue_degradation.resolve_degradation_rung(0, config_path=config_path),
                3,
            )
            self.assertEqual(
                dialogue_degradation.resolve_degradation_rung(-1, config_path=config_path),
                0,
            )


class DegradedRosterModelTests(unittest.TestCase):
    def test_reads_primary_model_and_uses_default_for_missing_or_blank_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            populated = Path(tmp) / "populated.json"
            populated.write_text(
                json.dumps({"light_doer": {"name": "Model A / Model B"}}),
                encoding="utf-8",
            )
            missing = Path(tmp) / "missing.json"
            missing.write_text("{}", encoding="utf-8")
            blank = Path(tmp) / "blank.json"
            blank.write_text(
                json.dumps({"light_doer": {"name": " / Model B"}}),
                encoding="utf-8",
            )

            self.assertEqual(
                dialogue_degradation._load_degraded_roster_model(populated), "Model A"
            )
            self.assertEqual(
                dialogue_degradation._load_degraded_roster_model(missing),
                dialogue_degradation._DEFAULT_DEGRADED_ROSTER_MODEL,
            )
            self.assertEqual(
                dialogue_degradation._load_degraded_roster_model(blank),
                dialogue_degradation._DEFAULT_DEGRADED_ROSTER_MODEL,
            )


class DegradationLadderStateTests(unittest.TestCase):
    def test_state_is_an_immutable_record_of_rung_effects(self) -> None:
        state = dialogue_degradation.DegradationLadderState(
            rung=2,
            max_rounds=dialogue_degradation._DEGRADED_ROUND_CAP,
            effort=dialogue_degradation._DEGRADED_EFFORT,
            model="Codex 5.6 Terra",
            is_degraded_independence=True,
            is_skipped=False,
        )

        self.assertEqual(state.rung, 2)
        self.assertEqual(state.max_rounds, 1)
        self.assertEqual(state.effort, "low")
        self.assertTrue(state.is_degraded_independence)
        self.assertFalse(state.is_skipped)
        with self.assertRaises(FrozenInstanceError):
            state.max_rounds = 2


if __name__ == "__main__":
    unittest.main()
