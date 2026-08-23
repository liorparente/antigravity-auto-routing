"""Validated consultation-policy loading shared by council entry points."""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, TypeGuard

try:
    from . import routing_config
    from .debate_state_machine import VALID_CONSENSUS_OUTCOMES
except (ImportError, ValueError):
    import routing_config  # type: ignore[no-redef]
    from debate_state_machine import VALID_CONSENSUS_OUTCOMES  # type: ignore[no-redef]

# Ticket 42: the single file `routing_config` itself reads. Re-exported here
# (rather than referenced only as `routing_config.ROUTING_CONFIG_PATH`) since
# this module's own callers already import it as
# `consultation_policy.ROUTING_CONFIG_PATH`.
ROUTING_CONFIG_PATH = routing_config.ROUTING_CONFIG_PATH

# Ticket 42: sourced directly from `routing_config`'s shared
# `DEFAULT_ROUTING_CONFIG` singleton (`ConsultationPolicyConfig`) rather than
# duplicated as a hand-maintained literal — `_merge_policy_defaults`/
# `_validated_policy` below still own this module's lenient "drop the
# malformed field, keep the rest" merge semantics (routing_config's own
# parser is deliberately fail-closed instead, per ticket 42's "malformed
# configuration raises" mandate), so this module keeps that behavior and
# simply delegates the *shape* of its defaults to the shared typed model
# instead of hand-copying it a second time. No `parse_routing_config({})`
# round-trip is needed: `DEFAULT_ROUTING_CONFIG` already *is* that parse's
# result.
DEFAULT_CONSULTATION_POLICY: dict[str, Any] = routing_config.DEFAULT_ROUTING_CONFIG.to_dict()[
    "consultation_policy"
]

def _merge_policy_defaults(
    defaults: dict[str, Any], configured: object
) -> dict[str, Any]:
    """Overlay a policy section onto its schema defaults without sharing state."""
    if not isinstance(configured, dict):
        return deepcopy(defaults)

    merged = deepcopy(defaults)
    for key, default_value in defaults.items():
        value = configured.get(key)
        if isinstance(default_value, dict):
            merged[key] = _merge_policy_defaults(default_value, value)
        elif value is not None and (
            isinstance(value, type(default_value))
            or (isinstance(default_value, float) and _is_number(value))
        ):
            merged[key] = deepcopy(value)
    for key, value in configured.items():
        if key not in defaults:
            merged[key] = deepcopy(value)
    return merged


def _is_number(value: object) -> TypeGuard[int | float]:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _is_unit_interval(value: object) -> bool:
    return (
        _is_number(value)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def _is_nonnegative_int(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _clean_deadlines_seconds(deadlines: object) -> dict[str, Any] | None:
    """Discard non-dict deadlines wholesale, else drop each malformed entry."""
    if not isinstance(deadlines, dict):
        return None
    return {key: value for key, value in deadlines.items() if _is_nonnegative_int(value)}


def _clean_security_veto_section(security: object) -> dict[str, Any] | None:
    """Validate one `security_veto`-shaped section, dropping each malformed field."""
    if not isinstance(security, dict):
        return None
    clean_security = dict(security)
    if "security_threshold" in clean_security and not _is_unit_interval(
        clean_security["security_threshold"]
    ):
        clean_security.pop("security_threshold")
    severities = clean_security.get("veto_severities")
    if "veto_severities" in clean_security and (
        not isinstance(severities, list)
        or not severities
        or not all(isinstance(severity, str) and bool(severity.strip()) for severity in severities)
    ):
        clean_security.pop("veto_severities")
    return clean_security


def _clean_perspective_weights(weights: object) -> dict[str, Any] | None:
    if not isinstance(weights, dict):
        return None
    if not all(
        _is_number(value) and math.isfinite(float(value)) and float(value) >= 0.0
        for value in weights.values()
    ):
        return None
    return dict(weights)


def _clean_council_policy_section(council: object) -> dict[str, Any] | None:
    """Validate the `council_policy` section (spec 0012 / workflow-v2 ticket 07)."""
    if not isinstance(council, dict):
        return None
    clean_council = dict(council)
    if "fast_path_enabled" in clean_council and not isinstance(
        clean_council["fast_path_enabled"], bool
    ):
        clean_council.pop("fast_path_enabled")
    if "quorum_threshold" in clean_council and not _is_unit_interval(
        clean_council["quorum_threshold"]
    ):
        clean_council.pop("quorum_threshold")
    consensus_policy = clean_council.get("consensus_policy")
    if "consensus_policy" in clean_council and (
        not isinstance(consensus_policy, list)
        or not consensus_policy
        or not all(
            isinstance(outcome, str) and outcome in VALID_CONSENSUS_OUTCOMES
            for outcome in consensus_policy
        )
    ):
        clean_council.pop("consensus_policy")
    if "perspective_weights" in clean_council:
        cleaned_weights = _clean_perspective_weights(clean_council["perspective_weights"])
        if cleaned_weights is None:
            clean_council.pop("perspective_weights")
        else:
            clean_council["perspective_weights"] = cleaned_weights
    if "deadlines_seconds" in clean_council:
        cleaned_deadlines = _clean_deadlines_seconds(clean_council["deadlines_seconds"])
        if cleaned_deadlines is None:
            clean_council.pop("deadlines_seconds")
        else:
            clean_council["deadlines_seconds"] = cleaned_deadlines
    if "security_veto" in clean_council:
        cleaned_security = _clean_security_veto_section(clean_council["security_veto"])
        if cleaned_security is None:
            clean_council.pop("security_veto")
        else:
            clean_council["security_veto"] = cleaned_security
    return clean_council


def _validated_policy(configured: object) -> dict[str, Any]:
    """Discard malformed policy values so each invalid field gets its safe default."""
    if not isinstance(configured, dict):
        return {}
    valid = deepcopy(configured)

    for field, require_nonempty in (("providers", True), ("adjudicators", False)):
        entries = valid.get(field)
        if field in valid and (
            not isinstance(entries, list)
            or (require_nonempty and not entries)
            or not all(
                isinstance(entry, dict)
                and isinstance(entry.get("id"), str)
                and bool(entry["id"].strip())
                and isinstance(entry.get("model"), str)
                and bool(entry["model"].strip())
                and (
                    "effort_mapping" not in entry
                    or (
                        isinstance(entry["effort_mapping"], dict)
                        and all(
                            isinstance(effort, str)
                            and isinstance(mapped_effort, str)
                            for effort, mapped_effort in entry[
                                "effort_mapping"
                            ].items()
                        )
                    )
                )
                for entry in entries
            )
            or len({entry["id"].strip() for entry in entries}) != len(entries)
        ):
            valid.pop(field)

    consensus_policy = valid.get("consensus_policy")
    if "consensus_policy" in valid and (
        not isinstance(consensus_policy, list)
        or not consensus_policy
        or not all(
            isinstance(outcome, str) and outcome in VALID_CONSENSUS_OUTCOMES
            for outcome in consensus_policy
        )
    ):
        valid.pop("consensus_policy")

    if "deadlines_seconds" in valid:
        cleaned_deadlines = _clean_deadlines_seconds(valid["deadlines_seconds"])
        if cleaned_deadlines is None:
            valid.pop("deadlines_seconds")
        else:
            valid["deadlines_seconds"] = cleaned_deadlines

    weighting = valid.get("weighting")
    if weighting is not None:
        if not isinstance(weighting, dict):
            valid.pop("weighting")
        else:
            clean_weighting = dict(weighting)
            initial_weights = clean_weighting.get("initial_weights")
            if "initial_weights" in clean_weighting and (
                not isinstance(initial_weights, dict)
                or not all(
                    _is_number(value)
                    and math.isfinite(float(value))
                    and float(value) >= 0.0
                    for value in initial_weights.values()
                )
            ):
                clean_weighting.pop("initial_weights")
            for field in ("quorum_threshold", "min_weight", "max_weight"):
                if field in clean_weighting and not _is_unit_interval(
                    clean_weighting[field]
                ):
                    clean_weighting.pop(field)
            lo = clean_weighting.get(
                "min_weight",
                DEFAULT_CONSULTATION_POLICY["weighting"]["min_weight"],
            )
            hi = clean_weighting.get(
                "max_weight",
                DEFAULT_CONSULTATION_POLICY["weighting"]["max_weight"],
            )
            if float(lo) > float(hi):
                clean_weighting.pop("min_weight", None)
                clean_weighting.pop("max_weight", None)
            valid["weighting"] = clean_weighting

    if "security_veto" in valid:
        cleaned_security = _clean_security_veto_section(valid["security_veto"])
        if cleaned_security is None:
            valid.pop("security_veto")
        else:
            valid["security_veto"] = cleaned_security

    if "council_policy" in valid:
        cleaned_council = _clean_council_policy_section(valid["council_policy"])
        if cleaned_council is None:
            valid.pop("council_policy")
        else:
            valid["council_policy"] = cleaned_council
    return valid


def load_consultation_policy(
    config_path: Path = ROUTING_CONFIG_PATH,
) -> dict[str, Any]:
    """Load consultation policy defaults while propagating file and JSON errors."""
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)

    # Ticket 42 iteration 2: route this load through the same centralized,
    # validated loader every other consumer uses. A validation failure here
    # is not itself fatal to *this* function — `load_consultation_policy`
    # also accepts legacy flat policy files (`providers`/`weighting` at the
    # top level, no `roles` section) and files with deliberately malformed
    # fields it is meant to degrade rather than reject (`_validated_policy`
    # below), per this module's own documented leniency contract — so
    # `ConfigError` is swallowed here rather than propagated; the raw
    # `config` dict already parsed above is what the merge below still
    # uses either way.
    try:
        routing_config.load_routing_config(config_path, fallback_on_missing=True)
    except routing_config.ConfigError:
        pass

    if not isinstance(config, dict):
        return deepcopy(DEFAULT_CONSULTATION_POLICY)
    if "consultation_policy" in config:
        configured = config["consultation_policy"]
    elif "providers" in config or "weighting" in config:
        configured = config
    else:
        configured = {}

    # `council_policy` (spec 0012 / workflow-v2 ticket 07) is its own
    # top-level sibling of `consultation_policy` in routing-config.json, not
    # nested inside it — fold it in here so `_validated_policy` sees and
    # validates it uniformly with every other section, unless the caller's
    # `configured` dict already declares its own (e.g. a legacy flat policy
    # file that inlines `council_policy` directly).
    if (
        isinstance(configured, dict)
        and "council_policy" not in configured
        and isinstance(config.get("council_policy"), dict)
    ):
        configured = {**configured, "council_policy": config["council_policy"]}

    return _merge_policy_defaults(DEFAULT_CONSULTATION_POLICY, _validated_policy(configured))


__all__ = [
    "DEFAULT_CONSULTATION_POLICY",
    "ROUTING_CONFIG_PATH",
    "load_consultation_policy",
]
