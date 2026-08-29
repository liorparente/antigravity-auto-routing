"""Coverage for the atomic worker-routing profile switcher."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import switch_profile


class SwitchProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.profiles = self.root / "profiles"
        self.profiles.mkdir()
        self.target = self.root / "routing-config.json"
        baseline = Path(__file__).resolve().parent / "profiles" / "01_baseline_cloud_default.json"
        self.valid = self.profiles / baseline.name
        self.valid.write_bytes(baseline.read_bytes())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_listing_and_alias_resolution(self) -> None:
        alternate = self.profiles / "02_hybrid_local_60usd.json"
        alternate.write_bytes(self.valid.read_bytes())
        self.assertEqual(
            switch_profile.resolve_profile("hybrid_local_60usd", self.profiles), alternate
        )
        self.assertEqual(switch_profile.resolve_profile("01", self.profiles), self.valid)
        self.assertEqual([path.name for path in switch_profile.available_profiles(self.profiles)], [
            "01_baseline_cloud_default.json", "02_hybrid_local_60usd.json"
        ])

    def test_validation_failure_does_not_overwrite_active_config(self) -> None:
        bad = self.profiles / "02_bad.json"
        bad.write_text('{"roles": "bad"}', encoding="utf-8")
        self.target.write_text('{"preserve": true}\n', encoding="utf-8")
        with self.assertRaises(switch_profile.routing_config.ConfigValidationError):
            switch_profile.switch_profile(bad, target=self.target)
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"preserve": true}\n')

    def test_switch_is_atomic_and_tags_active_profile(self) -> None:
        with mock.patch.object(switch_profile.os, "replace", wraps=switch_profile.os.replace) as replace:
            active = switch_profile.switch_profile(self.valid, target=self.target)
        self.assertEqual(active, "baseline_cloud_default")
        replace.assert_called_once()
        temporary, target = replace.call_args.args
        self.assertEqual(Path(target), self.target)
        self.assertEqual(Path(temporary).parent, self.target.parent)
        self.assertEqual(switch_profile.current_profile(self.target), active)

    def test_dry_run_and_roundtrip_consistency(self) -> None:
        switch_profile.switch_profile(self.valid, target=self.target, dry_run=True)
        self.assertFalse(self.target.exists())
        switch_profile.switch_profile(self.valid, target=self.target)
        expected = switch_profile.routing_config.load_routing_config(self.valid)
        actual = switch_profile.routing_config.load_routing_config(self.target)
        self.assertEqual(actual, expected)

    def test_cli_reports_validation_error(self) -> None:
        bad = self.profiles / "02_bad.json"
        bad.write_text('{"providers": "bad"}', encoding="utf-8")
        output = io.StringIO()
        with mock.patch.object(switch_profile, "PROFILE_DIR", self.profiles):
            result = switch_profile.main(["--switch", "02_bad"], stdout=output)
        self.assertEqual(result, 1)
        self.assertIn("switch failed:", output.getvalue())


if __name__ == "__main__":
    unittest.main()
