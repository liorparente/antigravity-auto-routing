"""Unit tests for routing_config.py (ticket 42): typed, validated,
immutable centralized configuration for routing-config.json."""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import routing_config
else:
    import routing_config  # type: ignore[no-redef]


class LoadCheckedInConfigTests(unittest.TestCase):
    """Ticket 42 AC: "Implement early schema validation at module load
    time" — the checked-in `routing-config.json` must parse cleanly
    through the strict schema, and every documented section must be
    present and correctly typed."""

    config: routing_config.RoutingConfig

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = routing_config.load_routing_config()

    def test_roles_and_providers_are_typed_and_nonempty(self) -> None:
        self.assertIn("planner", self.config.roles)
        role = self.config.roles["planner"]
        self.assertIsInstance(role, routing_config.RoleConfig)
        self.assertIsInstance(role.capability_requirements, routing_config.CapabilityRequirements)
        self.assertIn("claude_opus_5", self.config.providers)
        provider = self.config.providers["claude_opus_5"]
        self.assertIsInstance(provider, routing_config.ProviderConfig)
        self.assertEqual(provider.adapter, "claude_code_cli")

    def test_legacy_roles_are_parsed(self) -> None:
        self.assertIn("light_doer", self.config.legacy_roles)
        legacy = self.config.legacy_roles["light_doer"]
        self.assertIsInstance(legacy, routing_config.LegacyRoleConfig)
        self.assertIn("codex exec", legacy.patterns)

    def test_council_policy_is_typed(self) -> None:
        policy = self.config.council_policy
        self.assertIsInstance(policy, routing_config.CouncilPolicyConfig)
        self.assertTrue(policy.fast_path_enabled)
        self.assertIsInstance(policy.security_veto, routing_config.SecurityVetoConfig)

    def test_consultation_policy_is_typed(self) -> None:
        policy = self.config.consultation_policy
        self.assertIsInstance(policy, routing_config.ConsultationPolicyConfig)
        self.assertTrue(policy.providers)
        self.assertIsInstance(policy.providers[0], routing_config.ConsultationProviderEntry)
        self.assertIsInstance(policy.weighting, routing_config.ConsultationWeightingConfig)

    def test_critical_dialogue_matches_checked_in_values(self) -> None:
        self.assertEqual(self.config.critical_dialogue.code_review_diff_line_threshold, 300)
        self.assertIn("secret", self.config.critical_dialogue.security_sensitive_path_patterns)

    def test_roster_topology_has_all_roles(self) -> None:
        chains = self.config.roster_topology.role_fallback_chains
        self.assertIn("planner", chains)
        self.assertIn("critic_a", chains)
        self.assertIn("critic_b", chains)

    def test_canary_cadence_matches_checked_in_values(self) -> None:
        self.assertEqual(self.config.canary_cadence.dialogues_per_canary, 20)
        self.assertEqual(self.config.canary_cadence.seconds_between_canaries, 604800)

    def test_dialogue_budget_matches_checked_in_value(self) -> None:
        self.assertEqual(self.config.dialogue_budget.session_dialogue_cap, 10)

    def test_acceptance_gate_matches_checked_in_values(self) -> None:
        self.assertEqual(self.config.acceptance_gate.trials, 5)
        self.assertEqual(self.config.acceptance_gate.score_threshold, 0.8)

    def test_code_extensions_and_safe_commands_are_nonempty(self) -> None:
        self.assertIn("py", self.config.code_extensions)
        self.assertTrue(self.config.safe_commands)

    def test_validate_default_config_validates_the_checked_in_file(self) -> None:
        """Ticket 42 iteration 3: validating the checked-in
        `routing-config.json` is no longer a module-import side effect —
        `validate_default_config()` is the explicit call a caller (this
        test, or a future strict-mode CLI) makes for that same fail-fast
        guarantee on demand."""
        self.assertIsInstance(routing_config.validate_default_config(), routing_config.RoutingConfig)


class ImmutabilityTests(unittest.TestCase):
    def test_dataclasses_are_frozen(self) -> None:
        config = routing_config.get_default_routing_config()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.dialogue_budget.session_dialogue_cap = 99  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.canary_cadence.dialogues_per_canary = 1  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.acceptance_gate.trials = 1  # type: ignore[misc]

    def test_nested_mappings_are_read_only(self) -> None:
        config = routing_config.load_routing_config()
        with self.assertRaises(TypeError):
            config.council_policy.perspective_weights["new"] = 1.0  # type: ignore[index]
        with self.assertRaises(TypeError):
            config.roster_topology.role_fallback_chains["planner"] = ()  # type: ignore[index]
        with self.assertRaises(TypeError):
            config.roles["planner"] = config.roles["planner"]  # type: ignore[index]

    def test_capability_requirements_is_frozen(self) -> None:
        config = routing_config.load_routing_config()
        role = config.roles["planner"]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            role.capability_requirements.reasoning_tier = "low"  # type: ignore[misc]


class ParseRoutingConfigValidationTests(unittest.TestCase):
    """Ticket 42 AC: malformed fields raise `ConfigValidationError` naming
    the exact key path and failure reason."""

    def test_negative_min_context_is_rejected(self) -> None:
        data = {
            "roles": {
                "planner": {
                    "capability_requirements": {
                        "reasoning_tier": "high",
                        "tool_access": "read",
                        "min_context": -1,
                    },
                    "preferred_providers": [],
                }
            }
        }
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config(data)
        self.assertEqual(
            ctx.exception.key_path, "roles.planner.capability_requirements.min_context"
        )

    def test_quorum_threshold_out_of_range_is_rejected(self) -> None:
        data = {
            "council_policy": {
                "fast_path_enabled": True,
                "quorum_threshold": 1.5,
                "perspective_weights": {},
                "security_veto": {
                    "enabled": True,
                    "veto_severities": ["critical"],
                    "security_threshold": 0.5,
                },
                "deadlines_seconds": {},
                "consensus_policy": ["UNANIMOUS"],
            }
        }
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config(data)
        self.assertEqual(ctx.exception.key_path, "council_policy.quorum_threshold")
        self.assertEqual(ctx.exception.received_value, 1.5)

    def test_negative_weight_is_rejected(self) -> None:
        data = {
            "council_policy": {
                "fast_path_enabled": True,
                "quorum_threshold": 0.5,
                "perspective_weights": {"reviewer_architecture": -0.1},
                "security_veto": {
                    "enabled": True,
                    "veto_severities": ["critical"],
                    "security_threshold": 0.5,
                },
                "deadlines_seconds": {},
                "consensus_policy": ["UNANIMOUS"],
            }
        }
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config(data)
        self.assertEqual(ctx.exception.key_path, "council_policy.perspective_weights")

    def test_missing_required_key_when_fallback_disabled_raises(self) -> None:
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config({}, fallback_on_missing=False)
        self.assertEqual(ctx.exception.key_path, "roles")

    def test_non_dict_top_level_is_rejected(self) -> None:
        with self.assertRaises(routing_config.ConfigValidationError):
            routing_config.parse_routing_config([])  # type: ignore[arg-type]

    def test_wrong_type_role_entry_is_rejected(self) -> None:
        with self.assertRaises(routing_config.ConfigValidationError):
            routing_config.parse_routing_config({"roles": {"planner": "not-a-dict"}})

    def test_min_weight_exceeding_max_weight_is_rejected(self) -> None:
        data = {
            "consultation_policy": {
                "providers": [{"id": "a", "model": "m"}],
                "adjudicators": [],
                "deadlines_seconds": {},
                "consensus_policy": ["UNANIMOUS"],
                "weighting": {
                    "initial_weights": {"a": 1.0},
                    "min_weight": 0.9,
                    "max_weight": 0.1,
                    "quorum_threshold": 0.5,
                    "dynamic_weights_path": "x.json",
                },
                "security_veto": {
                    "enabled": True,
                    "veto_severities": ["critical"],
                    "security_threshold": 0.5,
                },
            }
        }
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config(data)
        self.assertEqual(ctx.exception.key_path, "consultation_policy.weighting")

    def test_duplicate_provider_ids_are_rejected(self) -> None:
        data = {
            "consultation_policy": {
                "providers": [
                    {"id": "dup", "model": "m1"},
                    {"id": "dup", "model": "m2"},
                ],
                "adjudicators": [],
                "deadlines_seconds": {},
                "consensus_policy": ["UNANIMOUS"],
                "weighting": {
                    "initial_weights": {"dup": 1.0},
                    "min_weight": 0.05,
                    "max_weight": 0.65,
                    "quorum_threshold": 0.5,
                    "dynamic_weights_path": "x.json",
                },
                "security_veto": {
                    "enabled": True,
                    "veto_severities": ["critical"],
                    "security_threshold": 0.5,
                },
            }
        }
        with self.assertRaises(routing_config.ConfigValidationError):
            routing_config.parse_routing_config(data)


class FallbackBehaviorTests(unittest.TestCase):
    """Ticket 42 AC: "Provide immutable fallback defaults for all optional
    parameters"."""

    def test_empty_dict_falls_back_to_every_default(self) -> None:
        # `legacy_roles` is excluded from the direct comparison: unlike
        # every other section, it is discovered per top-level key present in
        # the input rather than wholesale-filled from
        # `DEFAULT_ROUTING_CONFIG` when absent, so parsing `{}` (no
        # `light_doer` key at all) yields an empty `legacy_roles`, not
        # `DEFAULT_ROUTING_CONFIG`'s registered `light_doer` default. See
        # `test_partial_light_doer_legacy_role_defaults_missing_name` below
        # for that per-field fallback's own coverage.
        config = routing_config.parse_routing_config({}, fallback_on_missing=True)
        self.assertEqual(
            config, dataclasses.replace(routing_config.DEFAULT_ROUTING_CONFIG, legacy_roles={})
        )

    def test_partial_canary_cadence_defaults_the_other_key(self) -> None:
        config = routing_config.parse_routing_config(
            {"canary_cadence": {"dialogues_per_canary": 3}}
        )
        self.assertEqual(config.canary_cadence.dialogues_per_canary, 3)
        self.assertEqual(
            config.canary_cadence.seconds_between_canaries,
            routing_config.DEFAULT_ROUTING_CONFIG.canary_cadence.seconds_between_canaries,
        )

    def test_partial_critical_dialogue_defaults_the_other_key(self) -> None:
        config = routing_config.parse_routing_config(
            {"critical_dialogue": {"code_review_diff_line_threshold": 5}}
        )
        self.assertEqual(config.critical_dialogue.code_review_diff_line_threshold, 5)
        self.assertEqual(
            config.critical_dialogue.security_sensitive_path_patterns,
            routing_config.DEFAULT_ROUTING_CONFIG.critical_dialogue.security_sensitive_path_patterns,
        )

    def test_partial_dialogue_budget_defaults_the_missing_key(self) -> None:
        config = routing_config.parse_routing_config({"dialogue_budget": {}})
        self.assertEqual(
            config.dialogue_budget.session_dialogue_cap,
            routing_config.DEFAULT_ROUTING_CONFIG.dialogue_budget.session_dialogue_cap,
        )

    def test_partial_acceptance_gate_defaults_the_missing_key(self) -> None:
        config = routing_config.parse_routing_config({"acceptance_gate": {"trials": 3}})
        self.assertEqual(config.acceptance_gate.trials, 3)
        self.assertEqual(
            config.acceptance_gate.score_threshold,
            routing_config.DEFAULT_ROUTING_CONFIG.acceptance_gate.score_threshold,
        )

    def test_partial_light_doer_legacy_role_defaults_missing_name(self) -> None:
        config = routing_config.parse_routing_config({"light_doer": {}})
        default_light_doer = routing_config.DEFAULT_ROUTING_CONFIG.legacy_roles["light_doer"]
        self.assertEqual(config.legacy_roles["light_doer"].name, default_light_doer.name)
        self.assertEqual(config.legacy_roles["light_doer"].patterns, default_light_doer.patterns)

    def test_legacy_role_without_registered_default_still_raises(self) -> None:
        """A role with no registered default (anything but `light_doer`)
        still raises on a missing required field, `fallback_on_missing`
        notwithstanding — only `light_doer` has a fallback to give."""
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.parse_routing_config({"planner_helper": {}})
        self.assertEqual(ctx.exception.key_path, "planner_helper.name")

    def test_missing_roles_and_providers_default_to_empty(self) -> None:
        config = routing_config.parse_routing_config({}, fallback_on_missing=True)
        self.assertEqual(dict(config.roles), {})
        self.assertEqual(dict(config.providers), {})

    def test_get_default_routing_config_returns_the_shared_instance(self) -> None:
        self.assertIs(
            routing_config.get_default_routing_config(), routing_config.DEFAULT_ROUTING_CONFIG
        )


class ToDictRoundTripTests(unittest.TestCase):
    def test_checked_in_config_round_trips(self) -> None:
        config = routing_config.load_routing_config()
        as_dict = config.to_dict()
        reparsed = routing_config.parse_routing_config(as_dict)
        self.assertEqual(config, reparsed)

    def test_to_dict_matches_raw_json_shape_for_scalars(self) -> None:
        config = routing_config.load_routing_config()
        as_dict = config.to_dict()
        raw = json.loads(routing_config.ROUTING_CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(as_dict["dialogue_budget"], raw["dialogue_budget"])
        self.assertEqual(as_dict["acceptance_gate"], raw["acceptance_gate"])

    def test_default_config_round_trips(self) -> None:
        default = routing_config.get_default_routing_config()
        reparsed = routing_config.parse_routing_config(default.to_dict())
        self.assertEqual(default, reparsed)


class LoadRoutingConfigFileErrorTests(unittest.TestCase):
    def test_missing_file_raises_config_file_not_found_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does-not-exist.json"
            with self.assertRaises(routing_config.ConfigFileNotFoundError) as ctx:
                routing_config.load_routing_config(missing)
            self.assertEqual(ctx.exception.path, missing)

    def test_invalid_json_raises_config_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "routing-config.json"
            bad.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(routing_config.ConfigParseError):
                routing_config.load_routing_config(bad)

    def test_non_object_json_raises_config_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "routing-config.json"
            bad.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(routing_config.ConfigParseError):
                routing_config.load_routing_config(bad)

    def test_valid_partial_file_loads_with_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-config.json"
            path.write_text(json.dumps({"dialogue_budget": {"session_dialogue_cap": 3}}), encoding="utf-8")
            config = routing_config.load_routing_config(path)
            self.assertEqual(config.dialogue_budget.session_dialogue_cap, 3)
            self.assertEqual(dict(config.roles), {})

    def test_error_hierarchy(self) -> None:
        self.assertTrue(issubclass(routing_config.ConfigFileNotFoundError, routing_config.ConfigError))
        self.assertTrue(issubclass(routing_config.ConfigParseError, routing_config.ConfigError))
        self.assertTrue(issubclass(routing_config.ConfigValidationError, routing_config.ConfigError))
        self.assertTrue(issubclass(routing_config.ConfigError, Exception))


class ValidateDefaultConfigTests(unittest.TestCase):
    """Ticket 42 AC: "Implement early schema validation at module load
    time" fails closed — now via the explicit `validate_default_config()`
    call (iteration 3) rather than a module-import side effect. This pins
    that function against both ways the checked-in file could go bad —
    rather than `importlib.reload`, which would just recompute
    `ROUTING_CONFIG_PATH` from `__file__` again and mask any substitution."""

    def test_invalid_json_at_routing_config_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_path = Path(tmp) / "routing-config.json"
            bad_path.write_text("{not valid json", encoding="utf-8")
            with mock.patch.object(routing_config, "ROUTING_CONFIG_PATH", bad_path):
                with self.assertRaises(routing_config.ConfigParseError):
                    routing_config.validate_default_config()

    def test_missing_file_at_routing_config_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "does-not-exist.json"
            with mock.patch.object(routing_config, "ROUTING_CONFIG_PATH", missing_path):
                with self.assertRaises(routing_config.ConfigFileNotFoundError):
                    routing_config.validate_default_config()


if __name__ == "__main__":
    unittest.main()
