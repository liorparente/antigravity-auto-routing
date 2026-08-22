#!/usr/bin/env python3
"""Unit tests for the declarative Role/Provider/Council schema added to
routing-config.json by spec 0012 ticket 02 (ADR 0012).

Run with:
    python3 -m unittest skills/worker-routing/test_declarative_schema.py -v
or, from this directory:
    python3 -m unittest test_declarative_schema -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from . import routing_check
else:
    import routing_check  # type: ignore[no-redef]

CONFIG_PATH = Path(__file__).resolve().parent / "routing-config.json"

REQUIRED_ROLES = {
    "planner",
    "builder_heavy",
    "builder_light",
    "reviewer_architecture",
    "reviewer_risk",
    "reviewer_maintainability",
    "reviewer_security",
    "adjudicator",
}

REQUIRED_CAPABILITY_KEYS = {
    "reasoning_tier",
    "tool_access",
    "min_context",
    "local_only",
}

REQUIRED_PERSPECTIVE_WEIGHTS = {
    "reviewer_architecture": 0.30,
    "reviewer_risk": 0.25,
    "reviewer_maintainability": 0.20,
    "reviewer_security": 0.25,
}

LEGACY_TOP_LEVEL_KEYS = {
    "context_specialist",
    "planner",
    "critic",
    "heavy_doer",
    "light_doer",
    "sensitive_doer",
    "qa_auditor",
    "supported_models",
    "critical_dialogue",
    "roster_topology",
    "canary_cadence",
    "dialogue_budget",
    "acceptance_gate",
    "consultation_policy",
    "code_extensions",
    "safe_commands",
}


class ConfigLoadMixin:
    """Loads routing-config.json once per test via the public loader."""

    raw_text: ClassVar[str]
    config: ClassVar[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.raw_text = CONFIG_PATH.read_text(encoding="utf-8")
        cls.config = routing_check.load_config()


class TestJsonSyntaxAndSchemaValidity(ConfigLoadMixin, unittest.TestCase):
    def test_file_exists(self) -> None:
        self.assertTrue(CONFIG_PATH.is_file(), f"missing {CONFIG_PATH}")

    def test_is_valid_json(self) -> None:
        parsed = json.loads(self.raw_text)
        self.assertIsInstance(parsed, dict)

    def test_load_config_matches_json_loads(self) -> None:
        self.assertEqual(self.config, json.loads(self.raw_text))

    def test_top_level_is_object(self) -> None:
        self.assertIsInstance(self.config, dict)


class TestRolesPresence(ConfigLoadMixin, unittest.TestCase):
    def test_roles_key_present(self) -> None:
        self.assertIn("roles", self.config)
        self.assertIsInstance(self.config["roles"], dict)

    def test_all_required_roles_present(self) -> None:
        roles = self.config["roles"]
        missing = REQUIRED_ROLES - roles.keys()
        self.assertFalse(missing, f"missing roles: {sorted(missing)}")

    def test_no_unexpected_extra_roles_silently_shadow_required_ones(self) -> None:
        roles = self.config["roles"]
        for required_role in REQUIRED_ROLES:
            self.assertIn(required_role, roles)
            self.assertIsInstance(roles[required_role], dict)


class TestRoleCapabilityRequirements(ConfigLoadMixin, unittest.TestCase):
    def test_every_role_declares_capability_requirements(self) -> None:
        roles = self.config["roles"]
        for role_name in REQUIRED_ROLES:
            with self.subTest(role=role_name):
                self.assertIn("capability_requirements", roles[role_name])
                caps = roles[role_name]["capability_requirements"]
                self.assertIsInstance(caps, dict)

    def test_capability_requirements_have_all_required_keys(self) -> None:
        roles = self.config["roles"]
        for role_name in REQUIRED_ROLES:
            caps = roles[role_name]["capability_requirements"]
            with self.subTest(role=role_name):
                missing = REQUIRED_CAPABILITY_KEYS - caps.keys()
                self.assertFalse(missing, f"{role_name} missing capability keys: {sorted(missing)}")

    def test_reasoning_tier_is_valid_value(self) -> None:
        roles = self.config["roles"]
        valid_tiers = {"low", "medium", "high", "ultra"}
        for role_name in REQUIRED_ROLES:
            tier = roles[role_name]["capability_requirements"]["reasoning_tier"]
            with self.subTest(role=role_name):
                self.assertIn(tier, valid_tiers)

    def test_tool_access_is_valid_value(self) -> None:
        roles = self.config["roles"]
        valid_tool_access = {"read", "workspace-write", "danger-full-access"}
        for role_name in REQUIRED_ROLES:
            tool_access = roles[role_name]["capability_requirements"]["tool_access"]
            with self.subTest(role=role_name):
                self.assertIn(tool_access, valid_tool_access)

    def test_min_context_is_positive_int(self) -> None:
        roles = self.config["roles"]
        for role_name in REQUIRED_ROLES:
            min_context = roles[role_name]["capability_requirements"]["min_context"]
            with self.subTest(role=role_name):
                self.assertIsInstance(min_context, int)
                self.assertGreater(min_context, 0)

    def test_local_only_is_bool(self) -> None:
        roles = self.config["roles"]
        for role_name in REQUIRED_ROLES:
            local_only = roles[role_name]["capability_requirements"]["local_only"]
            with self.subTest(role=role_name):
                self.assertIsInstance(local_only, bool)

    def test_adjudicator_is_local_only(self) -> None:
        # The adjudicator resolves material council disagreements and, per
        # ADR 0012 Contract 3, must be able to run fail-closed offline.
        caps = self.config["roles"]["adjudicator"]["capability_requirements"]
        self.assertTrue(caps["local_only"])


class TestProvidersMapping(ConfigLoadMixin, unittest.TestCase):
    def test_providers_key_present(self) -> None:
        self.assertIn("providers", self.config)
        self.assertIsInstance(self.config["providers"], dict)

    def test_providers_is_nonempty(self) -> None:
        self.assertGreater(len(self.config["providers"]), 0)

    def test_every_provider_has_adapter_model_and_effort(self) -> None:
        providers = self.config["providers"]
        for provider_id, provider in providers.items():
            with self.subTest(provider=provider_id):
                self.assertIsInstance(provider, dict)
                self.assertIn("adapter", provider)
                self.assertIsInstance(provider["adapter"], str)
                self.assertTrue(provider["adapter"])
                self.assertIn("model", provider)
                self.assertIsInstance(provider["model"], str)
                self.assertTrue(provider["model"])
                self.assertIn("default_reasoning_effort", provider)
                self.assertIn(
                    provider["default_reasoning_effort"],
                    {"low", "medium", "high", "ultra"},
                )

    def test_every_role_preferred_provider_resolves_to_a_declared_provider(self) -> None:
        roles = self.config["roles"]
        providers = self.config["providers"]
        for role_name in REQUIRED_ROLES:
            preferred = roles[role_name].get("preferred_providers", [])
            with self.subTest(role=role_name):
                self.assertTrue(preferred, f"{role_name} declares no preferred_providers")
                for provider_id in preferred:
                    self.assertIn(provider_id, providers)


class TestCouncilPolicy(ConfigLoadMixin, unittest.TestCase):
    def test_council_policy_key_present(self) -> None:
        self.assertIn("council_policy", self.config)
        self.assertIsInstance(self.config["council_policy"], dict)

    def test_fast_path_enabled_true(self) -> None:
        self.assertIs(self.config["council_policy"]["fast_path_enabled"], True)

    def test_quorum_threshold(self) -> None:
        self.assertEqual(self.config["council_policy"]["quorum_threshold"], 0.60)

    def test_perspective_weights_present_and_correct(self) -> None:
        weights = self.config["council_policy"]["perspective_weights"]
        self.assertEqual(weights, REQUIRED_PERSPECTIVE_WEIGHTS)

    def test_perspective_weights_sum_to_one(self) -> None:
        weights = self.config["council_policy"]["perspective_weights"]
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=6)

    def test_security_veto_present(self) -> None:
        veto = self.config["council_policy"]["security_veto"]
        self.assertIsInstance(veto, dict)
        self.assertIn("enabled", veto)
        self.assertIn("veto_severities", veto)
        self.assertIn("security_threshold", veto)

    def test_deadlines_seconds_present(self) -> None:
        deadlines = self.config["council_policy"]["deadlines_seconds"]
        self.assertIsInstance(deadlines, dict)
        self.assertGreater(len(deadlines), 0)
        for value in deadlines.values():
            self.assertIsInstance(value, (int, float))
            self.assertGreater(value, 0)

    def test_consensus_policy_present(self) -> None:
        consensus_policy = self.config["council_policy"]["consensus_policy"]
        self.assertIsInstance(consensus_policy, list)
        self.assertIn("UNANIMOUS", consensus_policy)
        self.assertIn("MATERIAL_DISAGREEMENT", consensus_policy)


class TestBackwardCompatibility(ConfigLoadMixin, unittest.TestCase):
    def test_all_legacy_top_level_keys_still_present(self) -> None:
        missing = LEGACY_TOP_LEVEL_KEYS - self.config.keys()
        self.assertFalse(missing, f"legacy keys dropped: {sorted(missing)}")

    def test_legacy_worker_role_dicts_retain_name_and_patterns(self) -> None:
        legacy_worker_roles = (
            "context_specialist",
            "planner",
            "critic",
            "heavy_doer",
            "light_doer",
            "sensitive_doer",
            "qa_auditor",
        )
        for role_name in legacy_worker_roles:
            role = self.config[role_name]
            with self.subTest(role=role_name):
                self.assertIn("name", role)
                self.assertIn("patterns", role)
                self.assertIsInstance(role["patterns"], list)
                self.assertTrue(role["patterns"])

    def test_supported_models_is_nonempty_list(self) -> None:
        self.assertIsInstance(self.config["supported_models"], list)
        self.assertGreater(len(self.config["supported_models"]), 0)

    def test_consultation_policy_shape_unchanged(self) -> None:
        consultation_policy = self.config["consultation_policy"]
        self.assertIn("providers", consultation_policy)
        self.assertIn("adjudicators", consultation_policy)
        self.assertIn("weighting", consultation_policy)
        self.assertIn("security_veto", consultation_policy)

    def test_code_extensions_and_safe_commands_unchanged(self) -> None:
        self.assertIsInstance(self.config["code_extensions"], list)
        self.assertIsInstance(self.config["safe_commands"], list)
        self.assertGreater(len(self.config["code_extensions"]), 0)
        self.assertGreater(len(self.config["safe_commands"]), 0)


class TestNonRoleConfigKeysCoverage(ConfigLoadMixin, unittest.TestCase):
    """Every non-role top-level key must be listed so load_patterns() never
    treats it as a worker-role dict and mines it for "patterns"."""

    EXPECTED_NON_ROLE_KEYS: ClassVar[set[str]] = {
        "code_extensions",
        "safe_commands",
        "orchestrator",
        "critical_dialogue",
        "roster_topology",
        "consultation_policy",
        "council_policy",
        "roles",
        "providers",
        "supported_models",
        "canary_cadence",
        "dialogue_budget",
        "acceptance_gate",
    }

    def test_non_role_config_keys_matches_expected_set(self) -> None:
        self.assertEqual(routing_check.NON_ROLE_CONFIG_KEYS, self.EXPECTED_NON_ROLE_KEYS)

    def test_new_schema_keys_are_excluded_from_role_iteration(self) -> None:
        for key in ("roles", "providers", "council_policy"):
            with self.subTest(key=key):
                self.assertIn(key, routing_check.NON_ROLE_CONFIG_KEYS)

    def test_every_top_level_key_is_either_legacy_or_declared_non_role(self) -> None:
        unaccounted = set(self.config.keys()) - LEGACY_TOP_LEVEL_KEYS - routing_check.NON_ROLE_CONFIG_KEYS
        self.assertFalse(
            unaccounted,
            f"top-level keys not covered by legacy set or NON_ROLE_CONFIG_KEYS: {sorted(unaccounted)}",
        )


class TestLoadPatternsDoesNotLeakSchemaEntries(ConfigLoadMixin, unittest.TestCase):
    def test_load_patterns_excludes_roles_and_providers_and_council_policy(self) -> None:
        patterns = routing_check.load_patterns(self.config)
        # None of the identifiers that only live inside the new schema
        # containers should ever surface as a matchable worker pattern.
        leaked_identifiers = {
            "planner",
            "builder_heavy",
            "builder_light",
            "reviewer_architecture",
            "reviewer_risk",
            "reviewer_maintainability",
            "reviewer_security",
            "adjudicator",
            "claude_opus_5",
            "claude_fable_5",
            "claude_sonnet_5",
            "codex_sol",
            "codex_terra",
            "codex_luna",
            "gemini_flash_high",
            "gemini_pro",
            "lm_studio_local",
        }
        self.assertFalse(leaked_identifiers & set(patterns))

    def test_load_patterns_still_returns_legacy_patterns(self) -> None:
        patterns = routing_check.load_patterns(self.config)
        self.assertIn("claude-sonnet-5", patterns)
        self.assertIn("codex exec", patterns)
        self.assertIn("127.0.0.1:1234/v1/chat", patterns)

    def test_load_patterns_only_descends_into_dict_valued_legacy_roles(self) -> None:
        # Sanity check on the mechanism itself: every pattern returned must
        # have come from a top-level dict that is not in NON_ROLE_CONFIG_KEYS.
        patterns = set(routing_check.load_patterns(self.config))
        expected: set[str] = set()
        for key, value in self.config.items():
            if key in routing_check.NON_ROLE_CONFIG_KEYS or not isinstance(value, dict):
                continue
            expected.update(value.get("patterns", []))
        self.assertEqual(patterns, expected)


if __name__ == "__main__":
    unittest.main()
