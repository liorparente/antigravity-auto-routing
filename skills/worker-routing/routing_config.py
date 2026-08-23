"""Strongly-typed centralized configuration for worker-routing (ticket 42).

Every module in this package used to open ``routing-config.json`` and
re-parse it with ad hoc ``dict.get(...)`` chains scattered across five
different files, each with its own silently-diverging notion of what a
missing or malformed section should fall back to. This module is the single
place that JSON is turned into validated, immutable dataclasses:
``load_routing_config``/``parse_routing_config`` are the only functions that
should ever call ``json.load`` on ``routing-config.json`` — every consumer
in this package (``production_invoker``, ``consultation_policy``,
``dialogue_degradation``, ``debate_orchestrator``, ``routing_check``)
delegates to a `RoutingConfig` built here instead.

Validation is fail-closed and per-field: a malformed value raises
``ConfigValidationError`` naming the exact dotted key path and the value
that was rejected, rather than silently substituting a default or letting a
`KeyError`/`TypeError` surface deep inside an unrelated call stack. A
missing *section* is different from a malformed *value* within a present
section: ``fallback_on_missing=True`` (the default used throughout
production) fills an absent section from ``DEFAULT_ROUTING_CONFIG``, which
mirrors byte-for-byte the scattered ``DEFAULT_*`` constants this ticket's
consumers already shipped — so an incomplete config file degrades exactly
as it always did. ``fallback_on_missing=False`` instead raises, for callers
(tests, or a future strict-mode CLI) that want a config file to be
self-contained.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeGuard

__all__ = [
    "DEFAULT_ROUTING_CONFIG",
    "ROUTING_CONFIG_PATH",
    "STRUCTURAL_KEYS",
    "AcceptanceGateConfig",
    "CanaryCadenceConfig",
    "CapabilityRequirements",
    "ConfigError",
    "ConfigFileNotFoundError",
    "ConfigParseError",
    "ConfigValidationError",
    "ConsultationPolicyConfig",
    "ConsultationProviderEntry",
    "ConsultationWeightingConfig",
    "CouncilPolicyConfig",
    "CriticalDialogueConfig",
    "DialogueBudgetConfig",
    "LegacyRoleConfig",
    "ProviderConfig",
    "RoleConfig",
    "RosterTopologyConfig",
    "RoutingConfig",
    "SecurityVetoConfig",
    "get_default_routing_config",
    "load_routing_config",
    "parse_routing_config",
    "validate_default_config",
]

ROUTING_CONFIG_PATH = Path(__file__).resolve().parent / "routing-config.json"

# Top-level keys with a dedicated typed section below. Every other
# dict-valued top-level key (`planner`, `context_specialist`, ...) is a
# legacy `{"name": ..., "patterns": [...]}` role entry — the same
# routing_check.NON_ROLE_CONFIG_KEYS split, restated here as the
# authoritative source both modules should agree with. Public (ticket 42
# iteration 3): routing_check.py reads this directly rather than a
# `routing_check`-local copy, so the two modules cannot silently drift.
STRUCTURAL_KEYS = frozenset(
    {
        "roles",
        "providers",
        "council_policy",
        "consultation_policy",
        "critical_dialogue",
        "roster_topology",
        "canary_cadence",
        "dialogue_budget",
        "acceptance_gate",
        "code_extensions",
        "safe_commands",
        "supported_models",
        "orchestrator",
    }
)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Base class for every error this module raises."""


class ConfigFileNotFoundError(ConfigError):
    """Raised when a configured path does not exist on disk."""

    def __init__(self, path: Path | str) -> None:
        self.path = path
        super().__init__(f"Configuration file not found: {path}")


class ConfigParseError(ConfigError):
    """Raised when a configuration file is not valid JSON, or its top level
    is not a JSON object."""

    def __init__(self, path: Path | str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to parse configuration file {path}: {reason}")


class ConfigValidationError(ConfigError):
    """Raised when a present value fails schema validation.

    ``key_path`` is the dotted path to the offending field (e.g.
    ``"roles.planner.capability_requirements.min_context"``), so a caller
    debugging a bad config file does not have to guess which of several
    identically-shaped sections is broken.
    """

    def __init__(self, key_path: str, reason: str, received_value: Any = None) -> None:
        self.key_path = key_path
        self.reason = reason
        self.received_value = received_value
        super().__init__(
            f"Invalid configuration at '{key_path}': {reason} "
            f"(received: {received_value!r})"
        )


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRequirements:
    """One role's declared requirements, mirroring routing-config.json's
    ``roles.<role>.capability_requirements`` shape (ADR 0012)."""

    reasoning_tier: str
    tool_access: str
    min_context: int
    local_only: bool = False


@dataclass(frozen=True)
class ProviderConfig:
    """One entry from routing-config.json's ``providers`` map."""

    provider_id: str
    adapter: str
    model: str
    default_reasoning_effort: str


@dataclass(frozen=True)
class RoleConfig:
    """One entry from routing-config.json's ``roles`` map: what a role
    requires and which providers, in order, can satisfy it."""

    role_id: str
    capability_requirements: CapabilityRequirements
    preferred_providers: tuple[str, ...]


@dataclass(frozen=True)
class LegacyRoleConfig:
    """One legacy top-level role entry (e.g. ``planner``, ``light_doer``):
    a human-readable model label plus the CLI-invocation patterns
    ``routing_check`` matches against a command line."""

    name: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class SecurityVetoConfig:
    enabled: bool
    veto_severities: tuple[str, ...]
    security_threshold: float


@dataclass(frozen=True)
class CouncilPolicyConfig:
    fast_path_enabled: bool
    quorum_threshold: float
    perspective_weights: Mapping[str, float]
    security_veto: SecurityVetoConfig
    deadlines_seconds: Mapping[str, int]
    consensus_policy: tuple[str, ...]


@dataclass(frozen=True)
class ConsultationProviderEntry:
    id: str
    model: str
    effort_mapping: Mapping[str, str]


@dataclass(frozen=True)
class ConsultationWeightingConfig:
    initial_weights: Mapping[str, float]
    min_weight: float
    max_weight: float
    quorum_threshold: float
    dynamic_weights_path: str


@dataclass(frozen=True)
class ConsultationPolicyConfig:
    providers: tuple[ConsultationProviderEntry, ...]
    adjudicators: tuple[ConsultationProviderEntry, ...]
    deadlines_seconds: Mapping[str, int]
    consensus_policy: tuple[str, ...]
    weighting: ConsultationWeightingConfig
    security_veto: SecurityVetoConfig
    council_policy: CouncilPolicyConfig | None = None


@dataclass(frozen=True)
class CriticalDialogueConfig:
    code_review_diff_line_threshold: int
    security_sensitive_path_patterns: tuple[str, ...]


@dataclass(frozen=True)
class RosterTopologyConfig:
    role_fallback_chains: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CanaryCadenceConfig:
    dialogues_per_canary: int
    seconds_between_canaries: int | float


@dataclass(frozen=True)
class DialogueBudgetConfig:
    session_dialogue_cap: int


@dataclass(frozen=True)
class AcceptanceGateConfig:
    trials: int
    score_threshold: float


@dataclass(frozen=True)
class RoutingConfig:
    """Root container: every section of ``routing-config.json``, typed."""

    roles: Mapping[str, RoleConfig]
    providers: Mapping[str, ProviderConfig]
    legacy_roles: Mapping[str, LegacyRoleConfig]
    council_policy: CouncilPolicyConfig
    consultation_policy: ConsultationPolicyConfig
    critical_dialogue: CriticalDialogueConfig
    roster_topology: RosterTopologyConfig
    canary_cadence: CanaryCadenceConfig
    dialogue_budget: DialogueBudgetConfig
    acceptance_gate: AcceptanceGateConfig
    code_extensions: tuple[str, ...]
    safe_commands: tuple[str, ...]
    supported_models: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Reconstruct the plain-JSON shape this instance was parsed from.

        Round-trips through :func:`parse_routing_config`: legacy roles are
        re-flattened back to top-level keys, exactly where
        :func:`parse_routing_config` found them. Composed from
        `_SECTION_CONVERTERS`, one converter per typed section, so a
        section's shape is defined in exactly one place.
        """
        result: dict[str, Any] = {
            key: converter(self) for key, converter in _SECTION_CONVERTERS.items()
        }
        for role_name, legacy_role in self.legacy_roles.items():
            result[role_name] = _legacy_role_to_dict(legacy_role)
        return result


def _roles_to_dict(roles: Mapping[str, RoleConfig]) -> dict[str, Any]:
    return {
        role_id: {
            "capability_requirements": {
                "reasoning_tier": role.capability_requirements.reasoning_tier,
                "tool_access": role.capability_requirements.tool_access,
                "min_context": role.capability_requirements.min_context,
                "local_only": role.capability_requirements.local_only,
            },
            "preferred_providers": list(role.preferred_providers),
        }
        for role_id, role in roles.items()
    }


def _providers_to_dict(providers: Mapping[str, ProviderConfig]) -> dict[str, Any]:
    return {
        provider_id: {
            "adapter": provider.adapter,
            "model": provider.model,
            "default_reasoning_effort": provider.default_reasoning_effort,
        }
        for provider_id, provider in providers.items()
    }


def _legacy_role_to_dict(legacy_role: LegacyRoleConfig) -> dict[str, Any]:
    return {"name": legacy_role.name, "patterns": list(legacy_role.patterns)}


def _critical_dialogue_to_dict(critical_dialogue: CriticalDialogueConfig) -> dict[str, Any]:
    return {
        "code_review_diff_line_threshold": critical_dialogue.code_review_diff_line_threshold,
        "security_sensitive_path_patterns": list(critical_dialogue.security_sensitive_path_patterns),
    }


def _roster_topology_to_dict(roster_topology: RosterTopologyConfig) -> dict[str, Any]:
    return {
        "role_fallback_chains": {
            role: list(chain) for role, chain in roster_topology.role_fallback_chains.items()
        }
    }


def _canary_cadence_to_dict(canary_cadence: CanaryCadenceConfig) -> dict[str, Any]:
    return {
        "dialogues_per_canary": canary_cadence.dialogues_per_canary,
        "seconds_between_canaries": canary_cadence.seconds_between_canaries,
    }


def _dialogue_budget_to_dict(dialogue_budget: DialogueBudgetConfig) -> dict[str, Any]:
    return {"session_dialogue_cap": dialogue_budget.session_dialogue_cap}


def _acceptance_gate_to_dict(acceptance_gate: AcceptanceGateConfig) -> dict[str, Any]:
    return {"trials": acceptance_gate.trials, "score_threshold": acceptance_gate.score_threshold}


def _council_policy_to_dict(policy: CouncilPolicyConfig) -> dict[str, Any]:
    return {
        "fast_path_enabled": policy.fast_path_enabled,
        "quorum_threshold": policy.quorum_threshold,
        "perspective_weights": dict(policy.perspective_weights),
        "security_veto": _security_veto_to_dict(policy.security_veto),
        "deadlines_seconds": dict(policy.deadlines_seconds),
        "consensus_policy": list(policy.consensus_policy),
    }


def _security_veto_to_dict(veto: SecurityVetoConfig) -> dict[str, Any]:
    return {
        "enabled": veto.enabled,
        "veto_severities": list(veto.veto_severities),
        "security_threshold": veto.security_threshold,
    }


def _consultation_provider_entry_to_dict(entry: ConsultationProviderEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "model": entry.model,
        "effort_mapping": dict(entry.effort_mapping),
    }


def _consultation_policy_to_dict(policy: ConsultationPolicyConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "providers": [_consultation_provider_entry_to_dict(p) for p in policy.providers],
        "adjudicators": [_consultation_provider_entry_to_dict(p) for p in policy.adjudicators],
        "deadlines_seconds": dict(policy.deadlines_seconds),
        "consensus_policy": list(policy.consensus_policy),
        "weighting": {
            "initial_weights": dict(policy.weighting.initial_weights),
            "min_weight": policy.weighting.min_weight,
            "max_weight": policy.weighting.max_weight,
            "quorum_threshold": policy.weighting.quorum_threshold,
            "dynamic_weights_path": policy.weighting.dynamic_weights_path,
        },
        "security_veto": _security_veto_to_dict(policy.security_veto),
    }
    if policy.council_policy is not None:
        result["council_policy"] = _council_policy_to_dict(policy.council_policy)
    return result


# Single dispatch table `RoutingConfig.to_dict()` reads from, so a section's
# shape is defined once instead of duplicated as a hand-written if/elif chain.
_SECTION_CONVERTERS: dict[str, Any] = {
    "roles": lambda config: _roles_to_dict(config.roles),
    "providers": lambda config: _providers_to_dict(config.providers),
    "council_policy": lambda config: _council_policy_to_dict(config.council_policy),
    "consultation_policy": lambda config: _consultation_policy_to_dict(config.consultation_policy),
    "critical_dialogue": lambda config: _critical_dialogue_to_dict(config.critical_dialogue),
    "roster_topology": lambda config: _roster_topology_to_dict(config.roster_topology),
    "canary_cadence": lambda config: _canary_cadence_to_dict(config.canary_cadence),
    "dialogue_budget": lambda config: _dialogue_budget_to_dict(config.dialogue_budget),
    "acceptance_gate": lambda config: _acceptance_gate_to_dict(config.acceptance_gate),
    "code_extensions": lambda config: list(config.code_extensions),
    "safe_commands": lambda config: list(config.safe_commands),
    "supported_models": lambda config: list(config.supported_models),
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_dict(value: Any, key_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigValidationError(key_path, "must be an object", value)
    return value


def _require_list(value: Any, key_path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigValidationError(key_path, "must be a list", value)
    return value


def _require_str(data: dict[str, Any], key: str, key_path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigValidationError(f"{key_path}.{key}", "must be a non-empty string", value)
    return value


def _require_bool(data: dict[str, Any], key: str, key_path: str, *, default: bool | None = None) -> bool:
    if key not in data:
        if default is not None:
            return default
        raise ConfigValidationError(f"{key_path}.{key}", "is required", None)
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigValidationError(f"{key_path}.{key}", "must be a bool", value)
    return value


def _is_number(value: Any) -> TypeGuard[int | float]:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _require_nonneg_int(data: dict[str, Any], key: str, key_path: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigValidationError(f"{key_path}.{key}", "must be a non-negative integer", value)
    return value


def _require_number(data: dict[str, Any], key: str, key_path: str) -> float:
    value = data.get(key)
    if not _is_number(value):
        raise ConfigValidationError(f"{key_path}.{key}", "must be a finite number", value)
    return float(value)


def _require_unit_interval(data: dict[str, Any], key: str, key_path: str) -> float:
    value = _require_number(data, key, key_path)
    if not (0.0 <= value <= 1.0):
        raise ConfigValidationError(f"{key_path}.{key}", "must be between 0 and 1 inclusive", value)
    return value


def _require_str_tuple(data: dict[str, Any], key: str, key_path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigValidationError(f"{key_path}.{key}", "must be a list of strings", value)
    if not allow_empty and not value:
        raise ConfigValidationError(f"{key_path}.{key}", "must be a non-empty list of strings", value)
    return tuple(value)


def _require_str_mapping(data: dict[str, Any], key: str, key_path: str) -> dict[str, str]:
    value = data.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ConfigValidationError(f"{key_path}.{key}", "must be an object of string to string", value)
    return dict(value)


def _require_number_mapping(data: dict[str, Any], key: str, key_path: str) -> dict[str, float]:
    value = data.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and _is_number(v) and float(v) >= 0.0 for k, v in value.items()
    ):
        raise ConfigValidationError(
            f"{key_path}.{key}", "must be an object of string to non-negative number", value
        )
    return {k: float(v) for k, v in value.items()}


def _parse_optional_top_level_str_list(
    data: dict[str, Any], key: str, default: tuple[str, ...], *, fallback_on_missing: bool
) -> tuple[str, ...]:
    """One of the three flat top-level list-of-strings sections
    (`code_extensions`, `safe_commands`, `supported_models`): a validated
    tuple of strings when `key` is present, `default` when absent and
    `fallback_on_missing` is set, else a `ConfigValidationError` naming the
    missing section — the same `fallback_on_missing` contract every other
    top-level section in `parse_routing_config` already honors. The three
    call sites differ only in which field of `DEFAULT_ROUTING_CONFIG`
    supplies `default`.
    """
    value = data.get(key)
    if value is None:
        if fallback_on_missing:
            return default
        raise ConfigValidationError(key, "required section is missing", None)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigValidationError(key, "must be a list of strings", value)
    return tuple(value)


def _require_int_mapping(data: dict[str, Any], key: str, key_path: str) -> dict[str, int]:
    value = data.get(key, {})
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and not isinstance(v, bool) and isinstance(v, int) and v >= 0
        for k, v in value.items()
    ):
        raise ConfigValidationError(
            f"{key_path}.{key}", "must be an object of string to non-negative integer", value
        )
    return {k: int(v) for k, v in value.items()}


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------


def _parse_capability_requirements(data: Any, key_path: str) -> CapabilityRequirements:
    data = _require_dict(data, key_path)
    return CapabilityRequirements(
        reasoning_tier=_require_str(data, "reasoning_tier", key_path),
        tool_access=_require_str(data, "tool_access", key_path),
        min_context=_require_nonneg_int(data, "min_context", key_path),
        local_only=_require_bool(data, "local_only", key_path, default=False),
    )


def _parse_role_config(role_id: str, data: Any, key_path: str) -> RoleConfig:
    data = _require_dict(data, key_path)
    capability_requirements = _parse_capability_requirements(
        data.get("capability_requirements"), f"{key_path}.capability_requirements"
    )
    preferred_providers = _require_str_tuple(data, "preferred_providers", key_path)
    return RoleConfig(
        role_id=role_id,
        capability_requirements=capability_requirements,
        preferred_providers=preferred_providers,
    )


def _parse_provider_config(provider_id: str, data: Any, key_path: str) -> ProviderConfig:
    data = _require_dict(data, key_path)
    return ProviderConfig(
        provider_id=provider_id,
        adapter=_require_str(data, "adapter", key_path),
        model=_require_str(data, "model", key_path),
        default_reasoning_effort=_require_str(data, "default_reasoning_effort", key_path),
    )


def _parse_legacy_role_config(
    role_name: str, data: Any, key_path: str, *, fallback_on_missing: bool
) -> LegacyRoleConfig:
    """Parse one legacy top-level role entry, with per-field fallback.

    When `fallback_on_missing` is set and `role_name` has a registered
    default in `DEFAULT_ROUTING_CONFIG.legacy_roles` (currently only
    `light_doer` — see `_DEFAULT_LEGACY_ROLES`), a present-but-incomplete
    entry defaults whichever of `name`/`patterns` it omits from that role's
    default instead of raising — mirroring `_parse_critical_dialogue`'s
    independently-optional-keys contract. A role with no registered default,
    or `fallback_on_missing=False`, still raises `ConfigValidationError` for
    a missing/malformed field exactly as before.
    """
    data = _require_dict(data, key_path)
    default = DEFAULT_ROUTING_CONFIG.legacy_roles.get(role_name) if fallback_on_missing else None
    name = (
        default.name
        if default is not None and "name" not in data
        else _require_str(data, "name", key_path)
    )
    patterns = (
        default.patterns
        if default is not None and "patterns" not in data
        else _require_str_tuple(data, "patterns", key_path)
    )
    return LegacyRoleConfig(name=name, patterns=patterns)


def _parse_id_keyed_section(
    data: dict[str, Any],
    key: str,
    parser: Callable[[str, Any, str], Any],
    default: Mapping[str, Any],
    *,
    fallback_on_missing: bool,
) -> dict[str, Any]:
    """Parse a top-level `{id: {...}}` section (`roles`, `providers`):
    missing follows `fallback_on_missing` exactly like every other section
    (see `parse_routing_config`'s own `_section` helper), present parses
    each entry through `parser` keyed by its own id-scoped path.
    """
    section_data = data.get(key)
    if section_data is None:
        if fallback_on_missing:
            return dict(default)
        raise ConfigValidationError(key, "required section is missing", None)
    section_data = _require_dict(section_data, key)
    return {
        item_id: parser(item_id, item_data, f"{key}.{item_id}")
        for item_id, item_data in section_data.items()
    }


def _parse_security_veto(data: Any, key_path: str) -> SecurityVetoConfig:
    data = _require_dict(data, key_path)
    return SecurityVetoConfig(
        enabled=_require_bool(data, "enabled", key_path),
        veto_severities=_require_str_tuple(data, "veto_severities", key_path, allow_empty=False),
        security_threshold=_require_unit_interval(data, "security_threshold", key_path),
    )


def _parse_council_policy(data: Any, key_path: str) -> CouncilPolicyConfig:
    data = _require_dict(data, key_path)
    return CouncilPolicyConfig(
        fast_path_enabled=_require_bool(data, "fast_path_enabled", key_path),
        quorum_threshold=_require_unit_interval(data, "quorum_threshold", key_path),
        perspective_weights=MappingProxyType(
            _require_number_mapping(data, "perspective_weights", key_path)
        ),
        security_veto=_parse_security_veto(data.get("security_veto"), f"{key_path}.security_veto"),
        deadlines_seconds=MappingProxyType(_require_int_mapping(data, "deadlines_seconds", key_path)),
        consensus_policy=_require_str_tuple(data, "consensus_policy", key_path, allow_empty=False),
    )


def _parse_consultation_provider_entry(data: Any, key_path: str) -> ConsultationProviderEntry:
    data = _require_dict(data, key_path)
    return ConsultationProviderEntry(
        id=_require_str(data, "id", key_path),
        model=_require_str(data, "model", key_path),
        effort_mapping=MappingProxyType(_require_str_mapping(data, "effort_mapping", key_path)),
    )


def _parse_consultation_provider_list(
    data: Any, key_path: str, *, allow_empty: bool
) -> tuple[ConsultationProviderEntry, ...]:
    entries = _require_list(data, key_path)
    if not allow_empty and not entries:
        raise ConfigValidationError(key_path, "must be a non-empty list", data)
    parsed = tuple(
        _parse_consultation_provider_entry(entry, f"{key_path}[{index}]")
        for index, entry in enumerate(entries)
    )
    ids = [entry.id for entry in parsed]
    if len(set(ids)) != len(ids):
        raise ConfigValidationError(key_path, "entry ids must be unique", ids)
    return parsed


def _parse_consultation_weighting(data: Any, key_path: str) -> ConsultationWeightingConfig:
    data = _require_dict(data, key_path)
    min_weight = _require_unit_interval(data, "min_weight", key_path)
    max_weight = _require_unit_interval(data, "max_weight", key_path)
    if min_weight > max_weight:
        raise ConfigValidationError(
            key_path, "min_weight must not exceed max_weight", (min_weight, max_weight)
        )
    return ConsultationWeightingConfig(
        initial_weights=MappingProxyType(_require_number_mapping(data, "initial_weights", key_path)),
        min_weight=min_weight,
        max_weight=max_weight,
        quorum_threshold=_require_unit_interval(data, "quorum_threshold", key_path),
        dynamic_weights_path=_require_str(data, "dynamic_weights_path", key_path),
    )


def _parse_consultation_policy(data: Any, key_path: str) -> ConsultationPolicyConfig:
    data = _require_dict(data, key_path)
    council_policy_data = data.get("council_policy")
    return ConsultationPolicyConfig(
        providers=_parse_consultation_provider_list(
            data.get("providers", []), f"{key_path}.providers", allow_empty=False
        ),
        adjudicators=_parse_consultation_provider_list(
            data.get("adjudicators", []), f"{key_path}.adjudicators", allow_empty=True
        ),
        deadlines_seconds=MappingProxyType(_require_int_mapping(data, "deadlines_seconds", key_path)),
        consensus_policy=_require_str_tuple(data, "consensus_policy", key_path, allow_empty=False),
        weighting=_parse_consultation_weighting(data.get("weighting"), f"{key_path}.weighting"),
        security_veto=_parse_security_veto(data.get("security_veto"), f"{key_path}.security_veto"),
        council_policy=(
            _parse_council_policy(council_policy_data, f"{key_path}.council_policy")
            if council_policy_data is not None
            else None
        ),
    )


def _parse_critical_dialogue(data: Any, key_path: str) -> CriticalDialogueConfig:
    """Parse the `critical_dialogue` section with per-field fallback.

    Unlike most sections, each of this section's two keys is independently
    optional — mirroring `debate_orchestrator._load_code_review_risk_config`'s
    pre-ticket-42 `section.get(key, DEFAULT_*)` contract, which several of
    that module's own tests exercise with a config file specifying only one
    of the two keys. A *present* key with the wrong type/value still raises.
    """
    data = _require_dict(data, key_path)
    default = DEFAULT_ROUTING_CONFIG.critical_dialogue
    threshold = (
        _require_nonneg_int(data, "code_review_diff_line_threshold", key_path)
        if "code_review_diff_line_threshold" in data
        else default.code_review_diff_line_threshold
    )
    patterns = (
        _require_str_tuple(data, "security_sensitive_path_patterns", key_path)
        if "security_sensitive_path_patterns" in data
        else default.security_sensitive_path_patterns
    )
    return CriticalDialogueConfig(
        code_review_diff_line_threshold=threshold,
        security_sensitive_path_patterns=patterns,
    )


def _parse_roster_topology(data: Any, key_path: str) -> RosterTopologyConfig:
    data = _require_dict(data, key_path)
    chains_data = _require_dict(data.get("role_fallback_chains", {}), f"{key_path}.role_fallback_chains")
    chains: dict[str, tuple[str, ...]] = {}
    for role, chain in chains_data.items():
        chain_key_path = f"{key_path}.role_fallback_chains.{role}"
        if not isinstance(chain, list) or not all(isinstance(item, str) for item in chain):
            raise ConfigValidationError(chain_key_path, "must be a list of strings", chain)
        chains[role] = tuple(chain)
    return RosterTopologyConfig(role_fallback_chains=MappingProxyType(chains))


def _parse_canary_cadence(data: Any, key_path: str) -> CanaryCadenceConfig:
    """Parse the `canary_cadence` section with per-field fallback — same
    independently-optional-keys contract as `_parse_critical_dialogue`,
    mirroring `debate_orchestrator._load_canary_cadence_config`'s
    pre-ticket-42 behavior and its tests covering a config file that sets
    only one of the two keys.
    """
    data = _require_dict(data, key_path)
    default = DEFAULT_ROUTING_CONFIG.canary_cadence
    dialogues_per_canary = (
        _require_nonneg_int(data, "dialogues_per_canary", key_path)
        if "dialogues_per_canary" in data
        else default.dialogues_per_canary
    )
    if "seconds_between_canaries" in data:
        seconds_between_canaries = _require_number(data, "seconds_between_canaries", key_path)
        if seconds_between_canaries < 0:
            raise ConfigValidationError(
                f"{key_path}.seconds_between_canaries", "must be non-negative", seconds_between_canaries
            )
    else:
        seconds_between_canaries = default.seconds_between_canaries
    return CanaryCadenceConfig(
        dialogues_per_canary=dialogues_per_canary,
        seconds_between_canaries=seconds_between_canaries,
    )


def _parse_dialogue_budget(data: Any, key_path: str) -> DialogueBudgetConfig:
    """Parse the `dialogue_budget` section with per-key fallback — same
    independently-optional-keys contract as `_parse_critical_dialogue`.
    """
    data = _require_dict(data, key_path)
    default = DEFAULT_ROUTING_CONFIG.dialogue_budget
    session_dialogue_cap = (
        _require_nonneg_int(data, "session_dialogue_cap", key_path)
        if "session_dialogue_cap" in data
        else default.session_dialogue_cap
    )
    return DialogueBudgetConfig(session_dialogue_cap=session_dialogue_cap)


def _parse_acceptance_gate(data: Any, key_path: str) -> AcceptanceGateConfig:
    """Parse the `acceptance_gate` section with per-key fallback — same
    independently-optional-keys contract as `_parse_critical_dialogue`.
    """
    data = _require_dict(data, key_path)
    default = DEFAULT_ROUTING_CONFIG.acceptance_gate
    if "trials" in data:
        trials = _require_nonneg_int(data, "trials", key_path)
        if trials < 1:
            raise ConfigValidationError(f"{key_path}.trials", "must be at least 1", trials)
    else:
        trials = default.trials
    score_threshold = (
        _require_unit_interval(data, "score_threshold", key_path)
        if "score_threshold" in data
        else default.score_threshold
    )
    return AcceptanceGateConfig(trials=trials, score_threshold=score_threshold)


# ---------------------------------------------------------------------------
# Immutable fallback defaults
#
# Mirrors, field for field, the `DEFAULT_*` constants that
# production_invoker.py, consultation_policy.py, dialogue_degradation.py,
# and debate_orchestrator.py each shipped before this ticket — so a config
# file missing any of these sections degrades exactly as it always did.
# ---------------------------------------------------------------------------

_DEFAULT_SECURITY_VETO = SecurityVetoConfig(
    enabled=True,
    veto_severities=("critical", "high"),
    security_threshold=0.80,
)

_DEFAULT_COUNCIL_POLICY = CouncilPolicyConfig(
    fast_path_enabled=True,
    quorum_threshold=0.60,
    perspective_weights=MappingProxyType(
        {
            "reviewer_architecture": 0.30,
            "reviewer_risk": 0.25,
            "reviewer_maintainability": 0.20,
            "reviewer_security": 0.25,
        }
    ),
    security_veto=_DEFAULT_SECURITY_VETO,
    deadlines_seconds=MappingProxyType({"round_1": 45, "round_2": 60, "round_3": 60, "adjudicator": 60}),
    consensus_policy=(
        "UNANIMOUS",
        "QUALIFIED",
        "MATERIAL_DISAGREEMENT",
        "INCOMPLETE",
        "UNRESOLVED",
    ),
)

_DEFAULT_CONSULTATION_POLICY = ConsultationPolicyConfig(
    providers=(
        ConsultationProviderEntry(id="claude", model="claude-opus-5", effort_mapping=MappingProxyType({"high": "high"})),
        ConsultationProviderEntry(id="codex", model="gpt-5.6-sol", effort_mapping=MappingProxyType({"high": "high"})),
        ConsultationProviderEntry(id="gemini", model="gemini-3.1-pro", effort_mapping=MappingProxyType({"high": "high"})),
    ),
    adjudicators=(
        ConsultationProviderEntry(id="lm-studio", model="qwen3-coder-30b", effort_mapping=MappingProxyType({"high": "high"})),
    ),
    deadlines_seconds=MappingProxyType({"round_1": 120, "round_2": 60, "round_3": 60}),
    consensus_policy=(
        "UNANIMOUS",
        "QUALIFIED",
        "MATERIAL_DISAGREEMENT",
        "INCOMPLETE",
        "UNRESOLVED",
    ),
    weighting=ConsultationWeightingConfig(
        initial_weights=MappingProxyType({"claude": 0.40, "codex": 0.40, "gemini": 0.20}),
        min_weight=0.05,
        max_weight=0.65,
        quorum_threshold=0.60,
        dynamic_weights_path=".ralph/council_weights.json",
    ),
    security_veto=_DEFAULT_SECURITY_VETO,
    council_policy=_DEFAULT_COUNCIL_POLICY,
)

_DEFAULT_CRITICAL_DIALOGUE = CriticalDialogueConfig(
    code_review_diff_line_threshold=300,
    security_sensitive_path_patterns=(
        "auth",
        "credential",
        "secret",
        ".env",
        "password",
        "token",
        "/keys/",
        "security",
        "iam",
        "acl",
        "permission",
    ),
)

_DEFAULT_ROSTER_TOPOLOGY = RosterTopologyConfig(
    role_fallback_chains=MappingProxyType(
        {
            "planner": (
                "Claude Opus 5 (Thinking)",
                "Claude Fable 5",
                "Codex 5.6 Sol",
                "Gemini 3.6 Flash (High)",
                "Gemma 4 E4B",
            ),
            "critic_a": (
                "Codex 5.6 Sol",
                "GPT-OSS 120B (Medium)",
                "Gemini 3.6 Flash (High)",
                "Claude Opus 5 (Thinking)",
                "Qwen3.8-27B-MLX-6bit",
            ),
            "critic_b": (
                "Gemini 3.6 Flash",
                "Gemini 3.1 Pro (High)",
                "Codex 5.6 Sol",
                "Claude Opus 5 (Thinking)",
                "Gemma 4 E4B",
            ),
        }
    )
)

_DEFAULT_CANARY_CADENCE = CanaryCadenceConfig(
    dialogues_per_canary=20,
    seconds_between_canaries=7 * 24 * 60 * 60,
)

_DEFAULT_DIALOGUE_BUDGET = DialogueBudgetConfig(session_dialogue_cap=10)

_DEFAULT_ACCEPTANCE_GATE = AcceptanceGateConfig(trials=5, score_threshold=0.8)

_DEFAULT_CODE_EXTENSIONS: tuple[str, ...] = ("ts", "tsx", "css", "js", "jsx")

# `light_doer`'s registered fallback: dialogue_degradation.py's
# `_load_degraded_roster_model` reads `light_doer.name` for rung 2's cheaper
# roster model, and needs a default for a config whose `light_doer` block is
# present but incomplete. Mirrors dialogue_degradation.py's own pre-ticket-42
# `_DEFAULT_DEGRADED_ROSTER_MODEL` literal rather than importing it — that
# module already imports this one, so importing the other way would cycle.
# No other legacy role is registered here: every other role's validation
# failure on a present-but-incomplete entry is a genuine caller mistake for
# that role's own consumers, not a partial-config case anything degrades
# gracefully from.
_DEFAULT_LEGACY_ROLES: dict[str, LegacyRoleConfig] = {
    "light_doer": LegacyRoleConfig(name="Codex 5.6 Terra", patterns=()),
}

DEFAULT_ROUTING_CONFIG = RoutingConfig(
    roles=MappingProxyType({}),
    providers=MappingProxyType({}),
    legacy_roles=MappingProxyType(_DEFAULT_LEGACY_ROLES),
    council_policy=_DEFAULT_COUNCIL_POLICY,
    consultation_policy=_DEFAULT_CONSULTATION_POLICY,
    critical_dialogue=_DEFAULT_CRITICAL_DIALOGUE,
    roster_topology=_DEFAULT_ROSTER_TOPOLOGY,
    canary_cadence=_DEFAULT_CANARY_CADENCE,
    dialogue_budget=_DEFAULT_DIALOGUE_BUDGET,
    acceptance_gate=_DEFAULT_ACCEPTANCE_GATE,
    code_extensions=_DEFAULT_CODE_EXTENSIONS,
    safe_commands=(),
    supported_models=(),
)


def get_default_routing_config() -> RoutingConfig:
    """The immutable, complete default `RoutingConfig` — never read from
    disk. Every optional section here matches the scattered ``DEFAULT_*``
    constants this ticket's five consumer modules shipped before
    centralization, so behavior is unchanged for a config file missing any
    of them."""
    return DEFAULT_ROUTING_CONFIG


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def parse_routing_config(
    data: dict[str, Any], *, fallback_on_missing: bool = True
) -> RoutingConfig:
    """Validate and construct a `RoutingConfig` from an already-parsed JSON
    object.

    Each top-level section is independent: a malformed value inside a
    *present* section always raises `ConfigValidationError`, regardless of
    `fallback_on_missing`. An *absent* section instead follows
    `fallback_on_missing`: `True` (the default) substitutes the matching
    piece of `DEFAULT_ROUTING_CONFIG`; `False` raises `ConfigValidationError`
    naming the missing section.
    """
    if not isinstance(data, dict):
        raise ConfigValidationError("$", "top-level configuration must be an object", data)

    def _section(key: str, parser: Any, default: Any) -> Any:
        if key not in data:
            if fallback_on_missing:
                return default
            raise ConfigValidationError(key, "required section is missing", None)
        return parser(data[key], key)

    roles: dict[str, RoleConfig] = _parse_id_keyed_section(
        data, "roles", _parse_role_config, DEFAULT_ROUTING_CONFIG.roles,
        fallback_on_missing=fallback_on_missing,
    )
    providers: dict[str, ProviderConfig] = _parse_id_keyed_section(
        data, "providers", _parse_provider_config, DEFAULT_ROUTING_CONFIG.providers,
        fallback_on_missing=fallback_on_missing,
    )

    legacy_roles: dict[str, LegacyRoleConfig] = {}
    for key, value in data.items():
        if key in STRUCTURAL_KEYS or not isinstance(value, dict):
            continue
        legacy_roles[key] = _parse_legacy_role_config(
            key, value, key, fallback_on_missing=fallback_on_missing
        )

    council_policy = _section("council_policy", _parse_council_policy, DEFAULT_ROUTING_CONFIG.council_policy)
    consultation_policy = _section(
        "consultation_policy", _parse_consultation_policy, DEFAULT_ROUTING_CONFIG.consultation_policy
    )
    critical_dialogue = _section(
        "critical_dialogue", _parse_critical_dialogue, DEFAULT_ROUTING_CONFIG.critical_dialogue
    )
    roster_topology = _section(
        "roster_topology", _parse_roster_topology, DEFAULT_ROUTING_CONFIG.roster_topology
    )
    canary_cadence = _section(
        "canary_cadence", _parse_canary_cadence, DEFAULT_ROUTING_CONFIG.canary_cadence
    )
    dialogue_budget = _section(
        "dialogue_budget", _parse_dialogue_budget, DEFAULT_ROUTING_CONFIG.dialogue_budget
    )
    acceptance_gate = _section(
        "acceptance_gate", _parse_acceptance_gate, DEFAULT_ROUTING_CONFIG.acceptance_gate
    )

    code_extensions = _parse_optional_top_level_str_list(
        data, "code_extensions", DEFAULT_ROUTING_CONFIG.code_extensions, fallback_on_missing=fallback_on_missing
    )
    safe_commands = _parse_optional_top_level_str_list(
        data, "safe_commands", DEFAULT_ROUTING_CONFIG.safe_commands, fallback_on_missing=fallback_on_missing
    )
    supported_models = _parse_optional_top_level_str_list(
        data, "supported_models", DEFAULT_ROUTING_CONFIG.supported_models, fallback_on_missing=fallback_on_missing
    )

    return RoutingConfig(
        roles=MappingProxyType(roles),
        providers=MappingProxyType(providers),
        legacy_roles=MappingProxyType(legacy_roles),
        council_policy=council_policy,
        consultation_policy=consultation_policy,
        critical_dialogue=critical_dialogue,
        roster_topology=roster_topology,
        canary_cadence=canary_cadence,
        dialogue_budget=dialogue_budget,
        acceptance_gate=acceptance_gate,
        code_extensions=code_extensions,
        safe_commands=safe_commands,
        supported_models=supported_models,
    )


def load_routing_config(
    config_path: Path | str | None = None, *, fallback_on_missing: bool = True
) -> RoutingConfig:
    """Read and validate `routing-config.json` (or `config_path`) from disk.

    Raises `ConfigFileNotFoundError` when the path does not exist and
    `ConfigParseError` when it exists but is not a valid JSON object. Schema
    validation of its contents is delegated to `parse_routing_config`.
    """
    path = Path(config_path) if config_path is not None else ROUTING_CONFIG_PATH
    if not path.exists():
        raise ConfigFileNotFoundError(path)
    try:
        with open(path, "r", encoding="utf-8") as stream:
            raw = json.load(stream)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(path, str(exc)) from exc
    if not isinstance(raw, dict):
        raise ConfigParseError(path, "top-level JSON value must be an object")
    return parse_routing_config(raw, fallback_on_missing=fallback_on_missing)


def validate_default_config() -> RoutingConfig:
    """Validate the checked-in `routing-config.json` against this module's
    schema, on demand.

    Ticket 42 iteration 3: this validation used to run automatically as a
    bare statement at module import time, so merely importing this module —
    to reach a type, or `DEFAULT_ROUTING_CONFIG` — paid for a disk read and
    full parse whether or not the caller needed one. A caller that wants
    that fail-fast guarantee (a test, or a future strict-mode CLI) now asks
    for it explicitly by calling this function.
    """
    return load_routing_config(ROUTING_CONFIG_PATH, fallback_on_missing=True)
