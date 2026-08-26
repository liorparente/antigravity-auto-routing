"""Unit tests for routing_config.py (ticket 42): typed, validated,
immutable centralized configuration for routing-config.json."""
from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import probe_models, routing_config
else:
    import probe_models  # type: ignore[no-redef]
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
            with (
                mock.patch.object(routing_config, "ROUTING_CONFIG_PATH", bad_path),
                self.assertRaises(routing_config.ConfigParseError),
            ):
                routing_config.validate_default_config()

    def test_missing_file_at_routing_config_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "does-not-exist.json"
            with (
                mock.patch.object(routing_config, "ROUTING_CONFIG_PATH", missing_path),
                self.assertRaises(routing_config.ConfigFileNotFoundError),
            ):
                routing_config.validate_default_config()


class ModelCapabilityRegistryTests(unittest.TestCase):
    """Ticket 46 AC: "Implement `MODEL_CAPABILITIES_REGISTRY` containing
    audited model entries" — built from ticket 45's
    `probe_models.AUDITED_MODEL_CATALOG`, keyed by `(provider, model_id)`
    so a model published by more than one provider (F7) gets one entry per
    provider instead of losing all but the last."""

    def test_registry_is_keyed_by_provider_and_model_id(self) -> None:
        registry = routing_config.build_model_capabilities_registry()
        capability = registry[("claude_code_cli", "claude-opus-5")]
        self.assertIsInstance(capability, routing_config.ModelCapability)
        self.assertEqual(capability.provider, "claude_code_cli")
        self.assertEqual(capability.model_id, "claude-opus-5")
        self.assertEqual(capability.supported_efforts, ("low", "medium", "high", "xhigh", "max"))
        self.assertEqual(capability.default_effort, "high")
        self.assertEqual(capability.context, 1_000_000)
        self.assertFalse(capability.local_only)

    def test_registry_is_an_immutable_mapping(self) -> None:
        registry = routing_config.build_model_capabilities_registry()
        with self.assertRaises(TypeError):
            registry[("claude_code_cli", "new-model")] = registry[  # type: ignore[index]
                ("claude_code_cli", "claude-opus-5")
            ]

    def test_f7_cross_provider_model_gets_its_own_distinct_entry(self) -> None:
        """`claude-sonnet-4-6` is audited under `antigravity_cli` but is
        also accepted by the `claude` binary with a *different* ladder
        (`probe_models._CROSS_PROVIDER_EFFORT_LADDERS`) — corrected down
        from that provider's own full CLI enum by dropping `xhigh`, which
        leaves it one rung wider than the native entry, not narrower.
        Before ticket 46,
        `AUDITED_MODEL_CATALOG`'s bare-`model_id` keying meant only one of
        these two providers' ladders could ever be looked up for this
        model id at all; the registry must carry both, distinctly."""
        registry = routing_config.build_model_capabilities_registry()
        native = registry[("antigravity_cli", "claude-sonnet-4-6")]
        overlaid = registry[("claude_code_cli", "claude-sonnet-4-6")]
        self.assertNotEqual(native.supported_efforts, overlaid.supported_efforts)
        self.assertEqual(overlaid.supported_efforts, ("low", "medium", "high", "max"))
        self.assertNotIn("xhigh", overlaid.supported_efforts)
        # The overlay's own primary entry (antigravity_cli claude-sonnet-4-6)
        # is audited with default_effort=None, which is not a member of any
        # ladder, so the overlay's default_effort is None too; its tier is
        # the ceiling of the overlay's own four-rung ladder, "max" — not the
        # native entry's "high" ceiling.
        self.assertIsNone(overlaid.default_effort)
        self.assertEqual(overlaid.tier, "max")
        self.assertEqual(overlaid.provider, "claude_code_cli")
        self.assertEqual(overlaid.model_id, "claude-sonnet-4-6")

    def test_overlay_drops_a_default_effort_outside_its_own_ladder(self) -> None:
        """`claude-sonnet-4-6`'s live `default_effort` is `None`, which
        trivially satisfies "not in the overlay's ladder" and never
        exercises the branch that actually *drops* a present default — see
        `build_model_capabilities_registry`'s
        ``primary.default_effort if primary is not None and
        primary.default_effort in ladder else None`` filter. A synthetic
        primary entry whose real default_effort sits outside a synthetic,
        narrower overlay ladder exercises the drop directly."""
        fake_primary = probe_models.AuditedModel(
            model_id="fixture-model-drop",
            display_label="Fixture Model Drop",
            provider_id="antigravity_cli",
            supported_efforts=("low", "medium", "high"),
            default_effort="high",
            context_window=32_000,
            local_only=False,
            evidence="test fixture — not a real audited model",
        )
        fake_catalog = {"fixture-model-drop": fake_primary}
        fake_ladders = {("claude_code_cli", "fixture-model-drop"): ("low", "medium")}
        with (
            mock.patch.object(probe_models, "AUDITED_MODEL_CATALOG", fake_catalog),
            mock.patch.object(probe_models, "_CROSS_PROVIDER_EFFORT_LADDERS", fake_ladders),
        ):
            registry = routing_config.build_model_capabilities_registry()
        overlaid = registry[("claude_code_cli", "fixture-model-drop")]
        # "high" is the primary's real default_effort, but it is outside
        # the overlay's own ("low", "medium") ladder, so it must be
        # dropped to None rather than carried over unchecked.
        self.assertIsNone(overlaid.default_effort)

    def test_overlay_keeps_a_default_effort_inside_its_own_ladder(self) -> None:
        """Complement of the drop case above: when the primary's real
        `default_effort` *is* a member of the overlay's own ladder, it
        must be carried over, not dropped."""
        fake_primary = probe_models.AuditedModel(
            model_id="fixture-model-keep",
            display_label="Fixture Model Keep",
            provider_id="antigravity_cli",
            supported_efforts=("low", "medium", "high"),
            default_effort="medium",
            context_window=32_000,
            local_only=False,
            evidence="test fixture — not a real audited model",
        )
        fake_catalog = {"fixture-model-keep": fake_primary}
        fake_ladders = {("claude_code_cli", "fixture-model-keep"): ("low", "medium")}
        with (
            mock.patch.object(probe_models, "AUDITED_MODEL_CATALOG", fake_catalog),
            mock.patch.object(probe_models, "_CROSS_PROVIDER_EFFORT_LADDERS", fake_ladders),
        ):
            registry = routing_config.build_model_capabilities_registry()
        overlaid = registry[("claude_code_cli", "fixture-model-keep")]
        self.assertEqual(overlaid.default_effort, "medium")

    def test_a_model_with_no_effort_ladder_gets_tier_none_not_a_crash(self) -> None:
        """`claude-3-7-sonnet` predates reasoning efforts entirely
        (`supported_efforts=()`) — deriving a tier from an empty ladder
        must degrade to an explicit sentinel, not raise."""
        registry = routing_config.build_model_capabilities_registry()
        capability = registry[("claude_code_cli", "claude-3-7-sonnet")]
        self.assertEqual(capability.supported_efforts, ())
        self.assertIsNone(capability.default_effort)
        self.assertEqual(capability.tier, "none")

    def test_tier_is_the_models_own_highest_supported_effort_rung(self) -> None:
        registry = routing_config.build_model_capabilities_registry()
        # gpt-5.6-sol reaches "ultra" per the audit doc; gpt-5.6-luna tops
        # out at "max" and never reaches "ultra".
        self.assertEqual(registry[("codex_cli", "gpt-5.6-sol")].tier, "ultra")
        self.assertEqual(registry[("codex_cli", "gpt-5.6-luna")].tier, "max")

    def test_importing_probe_models_before_routing_config_does_not_raise(self) -> None:
        """Guards against reintroducing the shape a prior review round
        rejected: reading `probe_models.AUDITED_MODEL_CATALOG` at this
        module's own top level (e.g. a bare `MODEL_CAPABILITIES_REGISTRY =
        build_model_capabilities_registry()` statement), rather than
        inside `build_model_capabilities_registry`'s function body.
        `probe_models` already imports `routing_config` eagerly at its own
        top level (Ticket 45); if `routing_config` also read
        `AUDITED_MODEL_CATALOG` at ITS OWN top level, then whenever
        `probe_models` is the module that starts the import chain,
        `probe_models`'s own `import routing_config` would, on its way to
        finishing, run `routing_config`'s body — including that read —
        while `probe_models` itself is still mid-initialization, paused at
        its own import statement, long before `AUDITED_MODEL_CATALOG` is
        defined: `AttributeError`, not a deadlock — Python does not block
        on a circular import, it proceeds with whatever partial module
        state exists and raises the moment something reaches for an
        attribute that isn't there yet. A same-process test cannot
        exercise this: both modules are already fully imported by the
        rest of the suite by the time this test runs, so re-importing
        either is a cached no-op regardless of ordering. A fresh
        subprocess is the only way to genuinely control which module
        starts the chain.

        This test alone does not prove the bare *statement*
        `import probe_models` is safe to hoist to `routing_config`'s own
        top level — only that reading `AUDITED_MODEL_CATALOG` there is
        unsafe in this order. See the companion test below for the hazard
        a hoisted-but-unused import statement creates in the other import
        order.
        """
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import probe_models; "
            "assert probe_models.AUDITED_MODEL_CATALOG; "
            "import routing_config; "
            "assert routing_config.build_model_capabilities_registry()"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(__file__).resolve().parent)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_importing_routing_config_before_probe_models_does_not_raise(self) -> None:
        """Guards the opposite hazard: a bare, *unused* top-level
        `import probe_models` statement in `routing_config.py` (no
        attribute read at import time at all). `probe_models.main`'s own
        `config_loader` parameter default is `routing_config.load_routing_config`
        (`probe_models.py`), evaluated when that `def` statement executes
        as part of loading `probe_models`'s module body. If
        `routing_config` imported `probe_models` at its own top level,
        then whenever `routing_config` is the module that starts the
        chain, that statement would run `probe_models`'s module body
        while `routing_config` itself is still mid-initialization — paused
        at its own `import probe_models` statement, before
        `load_routing_config` is defined — so `probe_models.main`'s
        default-argument evaluation would raise `AttributeError: module
        'routing_config' has no attribute 'load_routing_config'`: the
        failure lands on `routing_config`, not on anything in
        `probe_models`, and it is a plain `AttributeError`, not a
        deadlock.
        """
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "import routing_config; "
            "assert routing_config.build_model_capabilities_registry(); "
            "import probe_models; "
            "assert probe_models.AUDITED_MODEL_CATALOG"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(Path(__file__).resolve().parent)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class EffortCeilingTierTests(unittest.TestCase):
    """`_effort_ceiling_tier` is `ModelCapability.tier`'s sole source,
    mechanically derived from `supported_efforts` so it can never drift
    from it. Exercised directly (rather than only through
    `build_model_capabilities_registry`) so its two distinct edge cases —
    an empty ladder, and a ladder naming an unrecognized rung — are each
    pinned by name."""

    def test_empty_ladder_is_tier_none(self) -> None:
        self.assertEqual(routing_config._effort_ceiling_tier((), source="fixture.source.path"), "none")

    def test_highest_rung_wins_regardless_of_input_order(self) -> None:
        self.assertEqual(routing_config._effort_ceiling_tier(("medium", "low"), source="fixture.source.path"), "medium")
        self.assertEqual(routing_config._effort_ceiling_tier(("max", "low", "high"), source="fixture.source.path"), "max")
        # Highest rung last (and mid-sequence) too — not just first — so a
        # `supported_efforts[0]`-style regression that ignores rank can't
        # slip past this test by coincidence of fixture ordering.
        self.assertEqual(routing_config._effort_ceiling_tier(("low", "medium"), source="fixture.source.path"), "medium")
        self.assertEqual(routing_config._effort_ceiling_tier(("low", "high", "max"), source="fixture.source.path"), "max")

    def test_unrecognized_rung_fails_closed_instead_of_degrading_to_none(self) -> None:
        """A non-empty ladder naming an effort outside `_EFFORT_RANK` is a
        probe_models.py data-entry mistake, not a case to silently rank
        around — see `_effort_ceiling_tier`'s docstring. Before this test,
        such a ladder was silently ranked around: `("turbo",)` returned
        "none", indistinguishable from a model with no effort ladder at
        all, while `("low", "turbo")` returned "low", quietly dropping
        the rung it did not recognize.

        Asserts `ConfigValidationError` specifically, not a bare
        `ValueError`: `max(..., key=_EFFORT_RANK.index)` raises
        `ValueError` incidentally for these same inputs, so asserting
        the builtin would leave this test green with the deliberate
        guard deleted outright — pinning nothing. Also asserts `key_path`
        is exactly the `source` passed in, not a value `_effort_ceiling_tier`
        invents on its own — its two real call sites read two different
        tables (`build_model_capabilities_registry`, below), and a
        hardcoded key_path would silently name the wrong one for whichever
        call site isn't the one it was copied from."""
        for ladder in (("turbo",), ("low", "turbo")):
            with self.assertRaises(routing_config.ConfigValidationError) as ctx:
                routing_config._effort_ceiling_tier(ladder, source="fixture.source.path")
            self.assertEqual(ctx.exception.key_path, "fixture.source.path")
            self.assertIn("turbo", ctx.exception.reason)
            self.assertEqual(ctx.exception.received_value, ladder)

    def test_native_and_overlay_call_sites_each_name_their_own_table_on_failure(self) -> None:
        """`_effort_ceiling_tier` is called from two places in
        `build_model_capabilities_registry` — one reading
        `AUDITED_MODEL_CATALOG`, the other `_CROSS_PROVIDER_EFFORT_LADDERS`
        — and each must report ITS OWN table, not the other one, when its
        own data is what's actually broken. A single hardcoded `source`
        shared between both call sites would pass every existing test
        (neither the native nor the overlay live data contains an
        unrecognized rung) while still sending a maintainer to the wrong
        table for whichever call site doesn't match the hardcoded guess."""
        bad_native = probe_models.AuditedModel(
            model_id="fixture-model-bad-native",
            display_label="Fixture Model Bad Native",
            provider_id="antigravity_cli",
            supported_efforts=("low", "turbo"),
            default_effort=None,
            context_window=None,
            local_only=False,
            evidence="test fixture — not a real audited model",
        )
        with (
            mock.patch.object(
                probe_models, "AUDITED_MODEL_CATALOG", {"fixture-model-bad-native": bad_native}
            ),
            self.assertRaises(routing_config.ConfigValidationError) as ctx,
        ):
            routing_config.build_model_capabilities_registry()
        self.assertEqual(
            ctx.exception.key_path,
            "probe_models.AUDITED_MODEL_CATALOG['fixture-model-bad-native'].supported_efforts",
        )

        bad_ladders = {("claude_code_cli", "claude-sonnet-4-6"): ("low", "turbo")}
        with (
            mock.patch.object(probe_models, "_CROSS_PROVIDER_EFFORT_LADDERS", bad_ladders),
            self.assertRaises(routing_config.ConfigValidationError) as ctx,
        ):
            routing_config.build_model_capabilities_registry()
        self.assertEqual(
            ctx.exception.key_path,
            "probe_models._CROSS_PROVIDER_EFFORT_LADDERS[('claude_code_cli', 'claude-sonnet-4-6')]",
        )


class RoleMatrixViewDataTests(unittest.TestCase):
    """Ticket 46 AC: "Expose `get_role_matrix_view_data(config)` returning
    validated role records with model defaults" and "unit tests verifying
    schema validation and fail-closed contracts"."""

    def test_checked_in_config_resolves_every_role_without_raising(self) -> None:
        config = routing_config.load_routing_config()
        matrix = routing_config.get_role_matrix_view_data(config)
        self.assertEqual(set(matrix.keys()), set(config.roles.keys()))
        planner = matrix["planner"]
        self.assertIsInstance(planner, routing_config.RoleMatrixEntry)
        self.assertEqual(len(planner.bindings), len(config.roles["planner"].preferred_providers))
        self.assertIsInstance(planner.bindings[0], routing_config.RoleModelBinding)

    def test_unknown_preferred_provider_reference_fails_closed(self) -> None:
        """A role naming a `preferred_providers` id absent from
        `config.providers` is a structural authoring error — distinct from
        a provider/model pair simply missing from the audited capability
        registry, which the checked-in config currently has three known
        instances of (the `unknown_model` kind among ticket 45's six
        `--audit` drift findings) and which this view must render, not
        raise on."""
        config = routing_config.parse_routing_config(
            {
                "roles": {
                    "planner": {
                        "capability_requirements": {
                            "reasoning_tier": "high",
                            "tool_access": "read",
                            "min_context": 0,
                        },
                        "preferred_providers": ["does_not_exist"],
                    }
                },
                "providers": {},
            }
        )
        with self.assertRaises(routing_config.ConfigValidationError) as ctx:
            routing_config.get_role_matrix_view_data(config)
        self.assertEqual(ctx.exception.key_path, "roles.planner.preferred_providers")

    def test_model_missing_from_capability_registry_is_reported_not_raised(self) -> None:
        """Known live-catalog drift (a configured model the audited
        registry does not recognize for that provider) must surface as
        `capability=None` on the binding, not an exception — ticket 45
        deliberately audited `routing-config.json`'s drift without
        rewiring it, and this view is what a future ticket uses to let an
        operator see and fix that drift, so it cannot itself refuse to
        render a drifted role."""
        config = routing_config.parse_routing_config(
            {
                "roles": {
                    "planner": {
                        "capability_requirements": {
                            "reasoning_tier": "high",
                            "tool_access": "read",
                            "min_context": 0,
                        },
                        "preferred_providers": ["ghost"],
                    }
                },
                "providers": {
                    "ghost": {
                        "adapter": "claude_code_cli",
                        "model": "model-that-does-not-exist",
                        "default_reasoning_effort": "high",
                    }
                },
            }
        )
        matrix = routing_config.get_role_matrix_view_data(config)
        binding = matrix["planner"].bindings[0]
        self.assertIsNone(binding.capability)
        self.assertEqual(binding.model_id, "model-that-does-not-exist")

    def test_capabilities_parameter_overrides_the_live_registry(self) -> None:
        """Dependency injection (mirroring `RoleResolver`'s own
        `availability_checker` parameter and `probe_models.main`'s
        `config_loader` parameter) so a test can hand this a small,
        deliberate fixture instead of the real, live-probe-shaped
        registry."""
        config = routing_config.parse_routing_config(
            {
                "roles": {
                    "planner": {
                        "capability_requirements": {
                            "reasoning_tier": "high",
                            "tool_access": "read",
                            "min_context": 0,
                        },
                        "preferred_providers": ["fixture_provider"],
                    }
                },
                "providers": {
                    "fixture_provider": {
                        "adapter": "claude_code_cli",
                        "model": "fixture-model",
                        "default_reasoning_effort": "medium",
                    }
                },
            }
        )
        fixture_capability = routing_config.ModelCapability(
            provider="claude_code_cli",
            model_id="fixture-model",
            supported_efforts=("low", "medium"),
            default_effort="low",
            tier="medium",
            context=8_000,
            local_only=False,
        )
        matrix = routing_config.get_role_matrix_view_data(
            config, capabilities={("claude_code_cli", "fixture-model"): fixture_capability}
        )
        self.assertIs(matrix["planner"].bindings[0].capability, fixture_capability)

    def test_role_matrix_entries_and_bindings_are_frozen(self) -> None:
        config = routing_config.load_routing_config()
        matrix = routing_config.get_role_matrix_view_data(config)
        entry = matrix["planner"]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.role_id = "other"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.bindings[0].reasoning_effort = "low"  # type: ignore[misc]

    def test_view_data_mapping_itself_is_immutable(self) -> None:
        """The individual entries are frozen dataclasses (tested above),
        but the top-level `role_id -> RoleMatrixEntry` mapping
        `get_role_matrix_view_data` returns must itself reject mutation
        too — mirroring `build_model_capabilities_registry`'s own
        `test_registry_is_an_immutable_mapping`."""
        config = routing_config.load_routing_config()
        matrix = routing_config.get_role_matrix_view_data(config)
        with self.assertRaises(TypeError):
            matrix["extra_role"] = matrix["planner"]  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
