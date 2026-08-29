#!/usr/bin/env python3
"""Live model catalog and CLI provider capability audit (ticket 45).

Until this module existed, "which models can we actually call, and at which
reasoning efforts?" was answered by three disagreeing sources: the human
labels in ``routing-config.json``'s ``supported_models``, the
``MODEL_ALIASES``/``CODEX_MODELS``/``AGY_MODELS`` tables in
``production_invoker``, and the prose matrix in ``CLAUDE.md``. None of them
were derived from the installed CLIs, so a routed worker could be handed a
model identifier no provider accepts, or a reasoning effort the provider's
own flag does not recognize. On ``agy`` that is a CLI error at dispatch
time; on ``claude`` it is not even that — the installed binary's ``--effort``
parser warns on stderr for a value it does not recognize and silently
runs at the model's default effort, no throw, no non-zero exit, so a role
configured at an effort ``claude`` rejects does not fail loudly, it silently
runs at a different effort than configured.

This module is the authoritative answer, in three parts:

``PROVIDER_CLI_CONTRACTS``
    The exact argv each installed provider accepts for "use this model at
    this reasoning effort", plus the effort enum that provider's flag
    actually parses. These differ per provider and do **not** match the
    project's own ``low|medium|high|ultra`` vocabulary — see
    ``docs/research/live-model-catalog-audit.md``.

``AUDITED_MODEL_CATALOG``
    Every model identifier the installed providers publish, with its
    supported reasoning efforts, factory default effort, and context window,
    each entry carrying the provenance it was read from. This is a snapshot;
    the probe below is what keeps it honest.

``probe_all`` / ``probe_lm_studio`` / ``probe_cli_provider``
    A fail-soft live probe. LM Studio is asked over HTTP with a 200ms
    deadline (spec 0013's non-blocking launch probe); CLI providers are
    probed by presence on ``PATH`` plus their own list command where one
    exists (`agy models`), falling back to the audited snapshot where none
    does (`claude`, `codex` publish no list command). Nothing here raises on
    an absent provider: an offline LM Studio or an uninstalled CLI is a
    reported status, not an exception.

Every I/O seam — the HTTP opener, ``shutil.which``, ``subprocess.run`` — is
injected, so the probe's parsing and fallback paths are exercised by
``test_probe_models.py`` without a network, a subprocess, or an installed CLI.

Run it directly for a human-readable report::

    python3 skills/worker-routing/probe_models.py            # live status
    python3 skills/worker-routing/probe_models.py --json     # machine payload
    python3 skills/worker-routing/probe_models.py --audit    # config drift
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import IO, Any, Literal, TextIO
from urllib.request import urlopen

if __package__:
    from . import routing_config
else:
    import routing_config  # type: ignore[no-redef]

__all__ = [
    "AUDITED_MODEL_CATALOG",
    "CLI_PROBE_TIMEOUT_SECONDS",
    "DEFAULT_PROBE_TIMEOUT_SECONDS",
    "DISPLAY_LABEL_TO_MODEL_ID",
    "LM_STUDIO_MODELS_ENDPOINT",
    "PROVIDER_CLI_CONTRACTS",
    "PROVIDER_IDS",
    "AuditedModel",
    "CatalogSnapshot",
    "CliContract",
    "DriftFinding",
    "ModelCatalogError",
    "ProbedModel",
    "ProviderProbe",
    "UnknownModelError",
    "UnknownProviderError",
    "UnsupportedEffortError",
    "audit_config_drift",
    "main",
    "probe_all",
    "probe_cli_provider",
    "probe_lm_studio",
    "resolve_model_id",
]

# Spec 0013: the dashboard's launch probe must not stall the page, so the
# LM Studio round trip gets 200ms and an offline server is a status.
DEFAULT_PROBE_TIMEOUT_SECONDS = 0.2
# A CLI list command forks a process and may talk to a remote catalog, so it
# gets a far longer deadline than the local HTTP probe — but still one that
# guarantees the probe returns.
CLI_PROBE_TIMEOUT_SECONDS = 15.0
LM_STUDIO_MODELS_ENDPOINT = "http://127.0.0.1:1234/v1/models"

# Named types rather than bare `str` so mypy rejects a typo'd provider or a
# third `source` value at the point it is written, not at the point a
# dashboard renders it.
ProviderId = Literal["claude_code_cli", "codex_cli", "antigravity_cli", "lm_studio_local"]
ModelSource = Literal["live", "audited"]
DriftKind = Literal["unknown_model", "unsupported_effort", "unmapped_label", "mismatched_provider"]

PROVIDER_IDS: tuple[ProviderId, ...] = (
    "claude_code_cli",
    "codex_cli",
    "antigravity_cli",
    "lm_studio_local",
)

# `codex` publishes no list command, but it caches the model catalog it
# fetched from the API here. Reading it is what keeps this module's Codex
# entries from going stale the way the binary's *embedded* catalog already
# has: the embedded copy still advertises a 372,000-token context window and
# a `gpt-5.2` the live catalog has since dropped.
CODEX_MODELS_CACHE_PATH = Path.home() / ".codex" / "models_cache.json"

# LM Studio serves embedding models from the same `/v1/models` listing as
# chat models. They are not routable workers, so the probe drops them rather
# than offering them as assignable models in the dashboard.
_EMBEDDING_MARKERS = ("embedding", "embed-")


class ModelCatalogError(Exception):
    """Base class for every error this module raises."""


class UnknownModelError(ModelCatalogError):
    """Raised when a label or identifier matches no audited model.

    Fail-closed on purpose: guessing a wire identifier is how an unroutable
    model reaches a provider's ``--model`` flag in the first place.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        super().__init__(f"No audited model matches {label!r}")


class UnknownProviderError(ModelCatalogError):
    """Raised when a provider identifier is outside `PROVIDER_IDS`."""

    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id
        super().__init__(f"Unknown provider {provider_id!r} (expected one of {list(PROVIDER_IDS)})")


class UnsupportedEffortError(ModelCatalogError):
    """Raised when a reasoning effort is outside the ladder that was checked.

    ``ladder`` names which enum was violated — a specific model's
    ``supported_efforts`` when the model is audited under *this* provider,
    the ``_CROSS_PROVIDER_EFFORT_LADDERS`` override when the model is
    audited under a *different* provider that publishes a narrower ladder
    for this exact ``(provider_id, model_id)`` pairing, or the provider's
    whole CLI enum otherwise (unaudited entirely, or audited elsewhere with
    no override on file) — so the message tells a caller which table to go
    fix rather than just repeating the rejected value.
    """

    def __init__(self, provider_id: str, effort: str, accepted: tuple[str, ...], *, ladder: str) -> None:
        self.provider_id = provider_id
        self.effort = effort
        self.accepted = accepted
        self.ladder = ladder
        super().__init__(
            f"{provider_id} does not accept reasoning effort {effort!r} for {ladder} "
            f"(accepted: {list(accepted)})"
        )


# ---------------------------------------------------------------------------
# CLI wire contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CliContract:
    """How one provider is told which model to use and how hard to think.

    The argv templates are the literal tokens the CLI parses, with
    ``{model}``/``{effort}`` placeholders — `codex` takes its effort as a
    TOML config override rather than a flag, and that difference is data
    here rather than an ``if`` branch at each call site.
    """

    provider_id: ProviderId
    binary: str | None
    model_argv_template: tuple[str, ...] | None
    effort_argv_template: tuple[str, ...] | None
    accepted_efforts: tuple[str, ...]
    list_models_argv: tuple[str, ...] | None
    models_cache_path: Path | None
    notes: str

    def format_argv(self, model_id: str, effort: str | None) -> tuple[str, ...]:
        """The argv fragment selecting ``model_id`` at ``effort``.

        Raises `ModelCatalogError` for a provider that has no CLI at all, and
        `UnsupportedEffortError` for an effort the provider's flag does not
        accept. Before this check existed, the routing protocol only ever
        found the first failure by trying to launch a nonexistent binary, and
        the second was worse than silent on `claude`: an unrecognized
        `--effort` value there does not exit non-zero at all — the CLI warns
        on stderr and runs at the model's default effort — so a raised
        subprocess exit code would never have surfaced it. `agy` does exit
        non-zero on an unsupported effort, but relying on that per provider
        is exactly the inconsistency this check removes.

        The effort is checked against ``model_id``'s own ladder in
        `AUDITED_MODEL_CATALOG` when the model is known **and** its audited
        entry belongs to *this* contract's provider — Luna's ladder stops at
        ``max`` while Sol and Terra reach ``ultra``, so the provider-wide
        `accepted_efforts` union alone would wave a Luna+``ultra`` pairing
        through. `AUDITED_MODEL_CATALOG` is keyed by bare model id, not
        ``(provider_id, model_id)`` (see docs/research/live-model-catalog-
        audit.md §3, finding F7), so a model two providers both publish —
        `claude-sonnet-4-6` is audited under `antigravity_cli` but the
        `claude` binary's own catalog also accepts it, with a longer ladder —
        would otherwise have the *other* provider's narrower ladder wrongly
        applied to it. A model unaudited for this provider — either not in
        the catalog at all, or audited only under a different provider's
        contract — falls back to this provider's whole CLI enum, *unless*
        `_CROSS_PROVIDER_EFFORT_LADDERS` names a narrower one for this exact
        ``(provider_id, model_id)`` pair: `claude_code_cli`'s own enum
        contains `xhigh`, but the `claude` binary's `xhigh` availability list
        names "Sonnet 5", not "Sonnet 4.6", so the whole-enum fallback alone
        would wave a `claude-sonnet-4-6` + `xhigh` pairing through as if it
        were real.
        """
        if self.model_argv_template is None:
            raise ModelCatalogError(
                f"{self.provider_id} is not invoked through a CLI; {self.notes}"
            )
        argv = tuple(token.format(model=model_id) for token in self.model_argv_template)
        if effort is None:
            return argv
        audited = AUDITED_MODEL_CATALOG.get(model_id)
        if audited is not None and audited.provider_id == self.provider_id:
            ladder = audited.supported_efforts
            ladder_name = f"model {model_id!r}"
        else:
            override = _CROSS_PROVIDER_EFFORT_LADDERS.get((self.provider_id, model_id))
            if override is not None:
                ladder = override
                ladder_name = f"_CROSS_PROVIDER_EFFORT_LADDERS[{(self.provider_id, model_id)!r}]"
            else:
                ladder = self.accepted_efforts
                ladder_name = f"provider {self.provider_id}"
        if effort not in ladder:
            raise UnsupportedEffortError(self.provider_id, effort, ladder, ladder=ladder_name)
        effort_argv = self.effort_argv_template or ()
        return argv + tuple(token.format(effort=effort) for token in effort_argv)


# Audited 2026-08-25 against claude 2.1.241, codex-cli 0.144.1, agy 1.1.20.
PROVIDER_CLI_CONTRACTS: Mapping[str, CliContract] = MappingProxyType(
    {
        "claude_code_cli": CliContract(
            provider_id="claude_code_cli",
            binary="claude",
            model_argv_template=("--model", "{model}"),
            effort_argv_template=("--effort", "{effort}"),
            # `claude --help`: "Effort level for the current session (low,
            # medium, high, xhigh, max)". Note the absence of `ultra`, which
            # is what the project's own routing vocabulary emits.
            accepted_efforts=("low", "medium", "high", "xhigh", "max"),
            list_models_argv=None,
            models_cache_path=None,
            notes="claude 2.1.241 publishes no list-models subcommand and caches no catalog on disk; per-model effort support is served by the API model catalog at runtime.",
        ),
        "codex_cli": CliContract(
            provider_id="codex_cli",
            binary="codex",
            model_argv_template=("--model", "{model}"),
            effort_argv_template=("-c", 'model_reasoning_effort="{effort}"'),
            accepted_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
            list_models_argv=None,
            models_cache_path=CODEX_MODELS_CACHE_PATH,
            notes="codex-cli 0.144.1 publishes no list-models subcommand, but caches the catalog it fetched at ~/.codex/models_cache.json.",
        ),
        "antigravity_cli": CliContract(
            provider_id="antigravity_cli",
            binary="agy",
            model_argv_template=("--model", "{model}"),
            effort_argv_template=("--effort", "{effort}"),
            # `agy --help`: "Reasoning effort for the current CLI session
            # (low|medium|high)" — three rungs, not the project's four.
            accepted_efforts=("low", "medium", "high"),
            list_models_argv=("models",),
            models_cache_path=None,
            notes="agy 1.1.20 lists models as `<id>\\t<label>`; most of its identifiers already carry the effort as a suffix. `agy models` fetches over the network, so it is skipped on a latency-sensitive launch probe.",
        ),
        "lm_studio_local": CliContract(
            provider_id="lm_studio_local",
            binary=None,
            model_argv_template=None,
            effort_argv_template=None,
            accepted_efforts=(),
            list_models_argv=None,
            models_cache_path=None,
            notes="it is reached over the OpenAI-compatible HTTP API at 127.0.0.1:1234, which exposes no reasoning-effort parameter.",
        ),
    }
)


# ---------------------------------------------------------------------------
# Audited catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditedModel:
    """One model identifier a provider genuinely accepts.

    ``model_id`` is the exact string that follows the provider's model flag —
    for `agy` that identifier already encodes the reasoning effort
    (``gemini-3.6-flash-high``), which is why ``supported_efforts`` is a
    single rung for most of its entries.
    """

    model_id: str
    display_label: str
    provider_id: ProviderId
    supported_efforts: tuple[str, ...]
    default_effort: str | None
    context_window: int | None
    local_only: bool
    evidence: str
    aliases: tuple[str, ...] = ()


_CLAUDE_EVIDENCE = (
    "claude 2.1.241's own model catalog, read directly from the installed binary "
    "(~/.local/share/claude/versions/2.1.241): default_effort and context.window are stated "
    "literally per model there — claude-opus-5, claude-sonnet-5, and claude-fable-5 each publish "
    'default_effort:"high" and context.window:1_000_000. The CLI does carry a `?? "high"` fallback '
    "for a model whose catalog entry omits a default, but that fallback never fires for these three"
)
_CODEX_EVIDENCE = "codex-cli 0.144.1 live model catalog cache (~/.codex/models_cache.json)"
_AGY_EVIDENCE = "`agy models` (agy 1.1.20)"
_LM_STUDIO_EVIDENCE = "live `GET /v1/models` on LM Studio, 2026-08-25"

_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _agy_gemini_family(family: str, label: str, efforts: Iterable[str]) -> tuple[AuditedModel, ...]:
    """Expand one Gemini family into the effort-suffixed identifiers `agy`
    actually lists. Gemini 3.1 Pro deliberately has no ``medium`` rung."""
    return tuple(
        AuditedModel(
            model_id=f"{family}-{effort}",
            display_label=f"{label} ({effort.capitalize()})",
            provider_id="antigravity_cli",
            supported_efforts=(effort,),
            default_effort=effort,
            context_window=None,
            local_only=False,
            evidence=_AGY_EVIDENCE,
        )
        for effort in efforts
    )


_AUDITED_MODELS: tuple[AuditedModel, ...] = (
    # --- Claude Code CLI -----------------------------------------------
    AuditedModel(
        model_id="claude-opus-5",
        display_label="Claude Opus 5 (Thinking)",
        provider_id="claude_code_cli",
        supported_efforts=_CLAUDE_EFFORTS,
        default_effort="high",
        context_window=1_000_000,
        local_only=False,
        evidence=_CLAUDE_EVIDENCE,
    ),
    AuditedModel(
        model_id="claude-sonnet-5",
        display_label="Claude Sonnet 5 (Thinking)",
        provider_id="claude_code_cli",
        supported_efforts=_CLAUDE_EFFORTS,
        default_effort="high",
        context_window=1_000_000,
        local_only=False,
        evidence=_CLAUDE_EVIDENCE,
    ),
    AuditedModel(
        model_id="claude-fable-5",
        display_label="Claude Fable 5",
        provider_id="claude_code_cli",
        supported_efforts=_CLAUDE_EFFORTS,
        default_effort="high",
        context_window=1_000_000,
        local_only=False,
        evidence=_CLAUDE_EVIDENCE,
    ),
    AuditedModel(
        model_id="claude-3-7-sonnet",
        display_label="Claude 3.7 Sonnet",
        provider_id="claude_code_cli",
        # A pre-effort model: `claude --model claude-3-7-sonnet` is accepted,
        # but it has no reasoning-effort ladder to select from.
        supported_efforts=(),
        default_effort=None,
        context_window=200_000,
        local_only=False,
        evidence=(
            "accepted model identifier in claude 2.1.241's own model catalog; predates the "
            "reasoning-effort ladder (no default_effort published, matching the None recorded "
            "above). That catalog entry carries no `context` key at all — unlike the models "
            "above whose context.window is stated literally — so 200_000 is not read from the "
            "installed binary; it is Claude 3.7 Sonnet's publicly documented context window, "
            "carried over as inferred rather than confirmed provenance"
        ),
    ),
    # --- Codex CLI ------------------------------------------------------
    AuditedModel(
        model_id="gpt-5.6-sol",
        display_label="Codex 5.6 Sol",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
        default_effort="low",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.6-terra",
        display_label="Codex 5.6 Terra",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh", "max", "ultra"),
        default_effort="medium",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.6-luna",
        display_label="Codex 5.6 Luna",
        provider_id="codex_cli",
        # One rung shorter than Sol and Terra: no `ultra`.
        supported_efforts=("low", "medium", "high", "xhigh", "max"),
        default_effort="medium",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.5",
        display_label="GPT-5.5",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh"),
        default_effort="medium",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.4",
        display_label="GPT-5.4",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh"),
        default_effort="medium",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.4-mini",
        display_label="GPT-5.4 Mini",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh"),
        default_effort="medium",
        context_window=272000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    AuditedModel(
        model_id="gpt-5.3-codex-spark",
        display_label="GPT-5.3 Codex Spark",
        provider_id="codex_cli",
        supported_efforts=("low", "medium", "high", "xhigh"),
        default_effort="high",
        context_window=128000,
        local_only=False,
        evidence=_CODEX_EVIDENCE,
    ),
    # --- Antigravity CLI -------------------------------------------------
    *_agy_gemini_family("gemini-3.7-flash", "Gemini 3.7 Flash", ("high", "medium", "low")),
    *_agy_gemini_family("gemini-3.6-flash", "Gemini 3.6 Flash", ("high", "medium", "low")),
    *_agy_gemini_family("gemini-3.5-flash", "Gemini 3.5 Flash", ("high", "medium", "low")),
    *_agy_gemini_family("gemini-3.1-pro", "Gemini 3.1 Pro", ("high", "low")),
    AuditedModel(
        model_id="gpt-oss-120b-medium",
        display_label="GPT-OSS 120B (Medium)",
        provider_id="antigravity_cli",
        supported_efforts=("medium",),
        default_effort="medium",
        context_window=None,
        local_only=False,
        # Listed by `agy`, not by `codex` — `production_invoker.CODEX_MODELS`
        # classifies it as a Codex model, which is the drift this audit found.
        evidence=_AGY_EVIDENCE,
        aliases=("gpt-oss-120b",),
    ),
    AuditedModel(
        model_id="claude-sonnet-4-6",
        display_label="Claude Sonnet 4.6 (Thinking)",
        provider_id="antigravity_cli",
        supported_efforts=("low", "medium", "high"),
        # `agy models` publishes no per-model default; the session `--effort`
        # flag governs, and the CLI does not document its own default.
        default_effort=None,
        context_window=None,
        local_only=False,
        evidence=_AGY_EVIDENCE,
    ),
    AuditedModel(
        model_id="claude-opus-4-6-thinking",
        display_label="Claude Opus 4.6 (Thinking)",
        provider_id="antigravity_cli",
        supported_efforts=("low", "medium", "high"),
        default_effort=None,
        context_window=None,
        local_only=False,
        evidence=_AGY_EVIDENCE,
    ),
    # --- LM Studio (local) ------------------------------------------------
    # A snapshot of what was loaded when the audit ran. `probe_lm_studio` is
    # the authority at runtime; these entries only give the offline dashboard
    # something truthful to render.
    AuditedModel(
        model_id="qwen3.8-27b-mlx",
        display_label="Qwen3.8 27B MLX (Local)",
        provider_id="lm_studio_local",
        supported_efforts=(),
        default_effort=None,
        context_window=None,
        local_only=True,
        evidence=_LM_STUDIO_EVIDENCE,
        aliases=("Qwen3.8-27B-MLX-6bit",),
    ),
    AuditedModel(
        model_id="gemma-4-e4b-it-mlx",
        display_label="Gemma 4 E4B IT MLX (Local)",
        provider_id="lm_studio_local",
        supported_efforts=(),
        default_effort=None,
        context_window=None,
        local_only=True,
        evidence=_LM_STUDIO_EVIDENCE,
        aliases=("Gemma 4 E4B", "gemma-4-e4b"),
    ),
)

AUDITED_MODEL_CATALOG: Mapping[str, AuditedModel] = MappingProxyType(
    {model.model_id: model for model in _AUDITED_MODELS}
)

# Ticket 45 F7 / live-model-catalog-audit.md §3: a narrow, explicit
# correction for the one case where a model's ladder on an *unaudited*
# provider is known to be narrower than that provider's whole CLI enum.
# `claude-sonnet-4-6` is audited only under `antigravity_cli`, so
# `format_argv`'s fallback branch would otherwise trust `claude_code_cli`'s
# entire `accepted_efforts` union — which includes `xhigh` — even though the
# `claude` binary's own `xhigh` availability list names "Sonnet 5", not
# "Sonnet 4.6". This is a data-level patch, not a re-keying of
# `AUDITED_MODEL_CATALOG` to `(provider_id, model_id)`: that schema decision
# belongs to ticket 46's capability registry.
_CROSS_PROVIDER_EFFORT_LADDERS: Mapping[tuple[ProviderId, str], tuple[str, ...]] = MappingProxyType(
    {("claude_code_cli", "claude-sonnet-4-6"): ("low", "medium", "high", "max")}
)


def _build_label_index(models: Iterable[AuditedModel]) -> Mapping[str, str]:
    """Every accepted spelling → wire identifier, over ``models``.

    Collisions are a programming error rather than a runtime condition:
    two models answering to one label is exactly the ambiguity this table
    exists to remove, so it raises rather than silently letting the later
    model win. ``models`` is a parameter — rather than reading
    `_AUDITED_MODELS` directly — so a test can hand this a deliberately
    colliding pair and observe the raise without needing two real catalog
    entries to disagree.
    """
    index: dict[str, str] = {}
    for model in models:
        for label in (model.model_id, model.display_label, *model.aliases):
            existing = index.get(label)
            if existing is not None and existing != model.model_id:
                raise ModelCatalogError(
                    f"Label {label!r} maps to both {existing!r} and {model.model_id!r}"
                )
            index[label] = model.model_id
    return MappingProxyType(index)


DISPLAY_LABEL_TO_MODEL_ID: Mapping[str, str] = _build_label_index(_AUDITED_MODELS)

_CASEFOLDED_LABEL_INDEX: Mapping[str, str] = MappingProxyType(
    {label.casefold(): model_id for label, model_id in DISPLAY_LABEL_TO_MODEL_ID.items()}
)


def resolve_model_id(label_or_id: str, *, snapshot: CatalogSnapshot | None = None) -> str:
    """Resolve a human label, vendor alias, or wire identifier to the exact
    identifier a provider CLI accepts. Raises `UnknownModelError` rather than
    passing an unrecognized string through to a ``--model`` flag."""
    exact = DISPLAY_LABEL_TO_MODEL_ID.get(label_or_id)
    if exact is not None:
        return exact
    folded = _CASEFOLDED_LABEL_INDEX.get(label_or_id.strip().casefold())
    if folded is not None:
        return folded
    if snapshot is not None:
        live_models = snapshot.models()
        for model in live_models:
            if label_or_id in (model.model_id, model.display_label):
                return model.model_id
        folded_input = label_or_id.strip().casefold()
        for model in live_models:
            if folded_input in (model.model_id.casefold(), model.display_label.casefold()):
                return model.model_id
    raise UnknownModelError(label_or_id)


# ---------------------------------------------------------------------------
# Probe results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbedModel:
    """One model as of a probe: either read live from a provider (``source``
    ``"live"``) or carried over from the audited snapshot (``"audited"``)."""

    model_id: str
    display_label: str
    provider_id: ProviderId
    supported_efforts: tuple[str, ...]
    default_effort: str | None
    context_window: int | None
    local_only: bool
    source: ModelSource

    @classmethod
    def from_audited(cls, model: AuditedModel, *, source: ModelSource = "audited") -> ProbedModel:
        return cls(
            model_id=model.model_id,
            display_label=model.display_label,
            provider_id=model.provider_id,
            supported_efforts=model.supported_efforts,
            default_effort=model.default_effort,
            context_window=model.context_window,
            local_only=model.local_only,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_label": self.display_label,
            "provider_id": self.provider_id,
            "supported_efforts": list(self.supported_efforts),
            "default_effort": self.default_effort,
            "context_window": self.context_window,
            "local_only": self.local_only,
            "source": self.source,
        }


@dataclass(frozen=True)
class ProviderProbe:
    """One provider's live status. ``available`` false is a reported state,
    never an exception: an offline LM Studio and an uninstalled CLI are both
    ordinary conditions on a developer workstation."""

    provider_id: ProviderId
    available: bool
    binary_path: str | None
    endpoint: str | None
    models: tuple[ProbedModel, ...]
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "available": self.available,
            "binary_path": self.binary_path,
            "endpoint": self.endpoint,
            "error": self.error,
            "model_ids": [model.model_id for model in self.models],
        }


@dataclass(frozen=True)
class CatalogSnapshot:
    """Every provider's status plus the merged model list from one probe.

    Shaped by `to_dict` into the capability payload spec 0013's dashboard
    reads — but it is a plain value object, not an HTTP concern.
    """

    providers: tuple[ProviderProbe, ...]

    def models(self) -> tuple[ProbedModel, ...]:
        """Every probed model, deduplicated by identifier with a live entry
        always beating an audited one for the same model."""
        merged: dict[str, ProbedModel] = {}
        for probe in self.providers:
            for model in probe.models:
                existing = merged.get(model.model_id)
                if existing is None or (existing.source != "live" and model.source == "live"):
                    merged[model.model_id] = model
        return tuple(merged.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": [probe.to_dict() for probe in self.providers],
            "models": {model.model_id: model.to_dict() for model in self.models()},
        }


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------

HttpOpener = Callable[[str, float], IO[bytes]]
BinaryLocator = Callable[[str], str | None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
CacheReader = Callable[[Path], str]


def _default_cache_reader(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _default_opener(url: str, timeout: float) -> IO[bytes]:
    return urlopen(url, timeout=timeout)


def probe_lm_studio(
    *,
    endpoint: str = LM_STUDIO_MODELS_ENDPOINT,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    opener: HttpOpener = _default_opener,
) -> ProviderProbe:
    """Ask the local LM Studio server which models are loaded.

    Fails soft in every direction — unreachable server, non-JSON body, a
    running server with nothing loaded — because this runs on the dashboard's
    launch path where a stalled or absent local server must not block the page.
    """
    try:
        with opener(endpoint, timeout) as response:
            body = response.read()
    except OSError as error:
        return _lm_studio_failure(endpoint, f"LM Studio unreachable at {endpoint}: {error}")
    try:
        payload = json.loads(body)
    except ValueError as error:
        return _lm_studio_failure(endpoint, f"invalid JSON from {endpoint}: {error}")
    if not isinstance(payload, dict):
        return _lm_studio_failure(endpoint, f"invalid JSON from {endpoint}: expected an object")

    models: list[ProbedModel] = []
    for entry in payload.get("data") or ():
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str) or not model_id or _is_embedding_model(model_id):
            continue
        models.append(_local_probed_model(model_id))

    if not models:
        return _lm_studio_failure(endpoint, "LM Studio is reachable but has no chat model loaded")
    return ProviderProbe(
        provider_id="lm_studio_local",
        available=True,
        binary_path=None,
        endpoint=endpoint,
        models=tuple(models),
        error=None,
    )


def _lm_studio_failure(endpoint: str, error: str) -> ProviderProbe:
    return ProviderProbe(
        provider_id="lm_studio_local",
        available=False,
        binary_path=None,
        endpoint=endpoint,
        models=(),
        error=error,
    )


def _is_embedding_model(model_id: str) -> bool:
    folded = model_id.casefold()
    return any(marker in folded for marker in _EMBEDDING_MARKERS)


def _local_probed_model(model_id: str) -> ProbedModel:
    audited = AUDITED_MODEL_CATALOG.get(model_id)
    if audited is not None:
        return ProbedModel.from_audited(audited, source="live")
    # A model loaded since the audit: reported as-is rather than hidden, with
    # no effort ladder because the OpenAI-compatible API exposes none.
    return ProbedModel(
        model_id=model_id,
        display_label=model_id,
        provider_id="lm_studio_local",
        supported_efforts=(),
        default_effort=None,
        context_window=None,
        local_only=True,
        source="live",
    )


def _audited_models_for(provider_id: str) -> tuple[ProbedModel, ...]:
    return tuple(
        ProbedModel.from_audited(model)
        for model in _AUDITED_MODELS
        if model.provider_id == provider_id
    )


@dataclass(frozen=True)
class _CliProbeContext:
    """The ``(contract, binary_path, audited)`` trio every degrade-to-the-
    snapshot path in `probe_cli_provider` needs to build a `ProviderProbe`.

    Bundled so it travels as one argument through `_cli_fallback` and
    `_probe_cached_catalog` instead of three positional ones that must stay
    in the same order at every call site — `probe_cli_provider` builds it
    once, as soon as the binary is confirmed present.
    """

    contract: CliContract
    binary_path: str
    audited: tuple[ProbedModel, ...]


def probe_cli_provider(
    provider_id: str,
    *,
    which: BinaryLocator = shutil.which,
    runner: CommandRunner = subprocess.run,
    cache_reader: CacheReader = _default_cache_reader,
    timeout: float = CLI_PROBE_TIMEOUT_SECONDS,
    list_models: bool = True,
) -> ProviderProbe:
    """Probe one CLI provider: is its binary on ``PATH``, and what does it publish?

    Three live sources, in descending order of directness: a list command
    (`agy models`), an on-disk catalog cache the CLI maintains
    (`~/.codex/models_cache.json`), and — where the provider offers neither
    (`claude`) — the audited snapshot. Every failure along the way degrades to
    the snapshot with the reason recorded in ``error``: an expired login or a
    deleted cache should not erase a provider from the dashboard.

    ``list_models=False`` skips the list command only. Spec 0013 wants the
    dashboard's launch probe non-blocking, and `agy models` fetches over the
    network — the cache and snapshot paths stay local either way.
    """
    contract = PROVIDER_CLI_CONTRACTS.get(provider_id)
    if contract is None:
        raise UnknownProviderError(provider_id)
    if contract.binary is None:
        raise ModelCatalogError(
            f"{provider_id} has no CLI to probe; {contract.notes} Use probe_lm_studio() instead."
        )

    binary_path = which(contract.binary)
    if binary_path is None:
        return ProviderProbe(
            provider_id=contract.provider_id,
            available=False,
            binary_path=None,
            endpoint=None,
            models=(),
            error=f"{contract.binary} is not installed (not on PATH)",
        )

    audited = _audited_models_for(contract.provider_id)
    context = _CliProbeContext(contract=contract, binary_path=binary_path, audited=audited)
    if contract.list_models_argv is None or not list_models:
        if contract.models_cache_path is None:
            return _cli_fallback(context, None)
        return _probe_cached_catalog(context, cache_reader)

    argv = [contract.binary, *contract.list_models_argv]
    try:
        completed = runner(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return _cli_fallback(context, f"`{' '.join(argv)}` timed out after {timeout}s")
    except OSError as error:
        return _cli_fallback(context, f"`{' '.join(argv)}` failed: {error}")

    if completed.returncode != 0:
        reason = (completed.stderr or "").strip() or f"exit status {completed.returncode}"
        return _cli_fallback(context, reason)

    listed = _parse_listed_models(completed.stdout or "", contract)
    if not listed:
        return _cli_fallback(context, f"`{' '.join(argv)}` listed no models")
    return ProviderProbe(
        provider_id=contract.provider_id,
        available=True,
        binary_path=binary_path,
        endpoint=None,
        models=listed,
        error=None,
    )


def _cli_fallback(context: _CliProbeContext, error: str | None) -> ProviderProbe:
    """Degrade to the audited snapshot for `context.contract`'s provider.

    ``error=None`` is the deliberate-skip case (``list_models=False`` with no
    on-disk cache to fall back to further) — the audited models are exactly
    as callable then as they are after a failed live probe, so it is the same
    shape with nothing to report.
    """
    return ProviderProbe(
        provider_id=context.contract.provider_id,
        available=True,
        binary_path=context.binary_path,
        endpoint=None,
        models=context.audited,
        error=error,
    )


def _probe_cached_catalog(context: _CliProbeContext, cache_reader: CacheReader) -> ProviderProbe:
    """Read a provider's on-disk catalog cache.

    This is what stops the Codex half of the audit from rotting: the copy
    embedded in the binary lags the account's real entitlements, so a
    hand-transcribed snapshot of it is wrong the day a model ships. An
    unreadable or malformed cache degrades to the snapshot rather than
    emptying the provider.

    ``cache_path`` is read from ``context.contract.models_cache_path`` rather
    than threaded through as its own parameter — the only caller reaches this
    function after already confirming that path is not `None`, so a second
    parameter carrying the same fact would just re-split the clump
    `_CliProbeContext` exists to close. The `is None` check below exists to
    narrow the type for mypy, not because it is reachable at runtime.
    """
    contract = context.contract
    cache_path = contract.models_cache_path
    if cache_path is None:
        return _cli_fallback(context, None)
    try:
        raw = cache_reader(cache_path)
    except OSError as error:
        return _cli_fallback(context, f"{cache_path} is unreadable: {error}")
    try:
        payload = json.loads(raw)
    except ValueError as error:
        return _cli_fallback(context, f"{cache_path} is not valid JSON: {error}")
    if not isinstance(payload, dict):
        return _cli_fallback(context, f"{cache_path} is not a JSON object")

    models = tuple(
        _cached_entry_to_model(entry, contract)
        for entry in payload.get("models") or ()
        # `visibility: "hide"` marks internal models (`codex-auto-review`)
        # that are real but not user-selectable, so they stay out of a
        # dashboard whose whole purpose is assigning models to roles.
        if isinstance(entry, dict) and entry.get("visibility") == "list" and entry.get("slug")
    )
    if not models:
        return _cli_fallback(context, f"{cache_path} lists no selectable models")
    return ProviderProbe(
        provider_id=contract.provider_id,
        available=True,
        binary_path=context.binary_path,
        endpoint=None,
        models=models,
        error=None,
    )


def _cached_entry_to_model(entry: Mapping[str, Any], contract: CliContract) -> ProbedModel:
    slug = str(entry["slug"])
    levels = tuple(
        str(level["effort"])
        for level in entry.get("supported_reasoning_levels") or ()
        if isinstance(level, dict) and level.get("effort")
    )
    default = entry.get("default_reasoning_level")
    context = entry.get("context_window")
    audited = AUDITED_MODEL_CATALOG.get(slug)
    # The audited label wins for a model this audit already knows — codex's
    # own `display_name` ("GPT-5.6-Sol") disagrees with the spelling
    # `routing-config.json` and `DISPLAY_LABEL_TO_MODEL_ID` use ("Codex 5.6
    # Sol"), and `_build_label_index` exists precisely so one model has one
    # name. The provider's label is the fallback for a model the audit does
    # not list at all.
    label = audited.display_label if audited is not None else (entry.get("display_name") or slug)
    return ProbedModel(
        model_id=slug,
        display_label=str(label),
        provider_id=contract.provider_id,
        supported_efforts=levels or contract.accepted_efforts,
        default_effort=str(default) if default else None,
        context_window=context if isinstance(context, int) else None,
        local_only=False,
        source="live",
    )


def _parse_listed_models(stdout: str, contract: CliContract) -> tuple[ProbedModel, ...]:
    """Parse a provider's ``<id>\\t<label>`` listing.

    Lines without a tab are progress chatter (`agy` prints "Fetching
    available models...") rather than models, so they are skipped instead of
    being parsed into an identifier nobody can call.
    """
    models: list[ProbedModel] = []
    for line in stdout.splitlines():
        if "\t" not in line:
            continue
        raw_id, _, raw_label = line.partition("\t")
        model_id, label = raw_id.strip(), raw_label.strip()
        if not model_id:
            continue
        audited = AUDITED_MODEL_CATALOG.get(model_id)
        if audited is not None:
            # The audited label wins over `agy`'s own listing: it is the
            # spelling `routing-config.json` and `DISPLAY_LABEL_TO_MODEL_ID`
            # already use, and `_build_label_index` exists precisely so one
            # model answers to one name rather than to whatever a given
            # provider happens to print this run.
            models.append(ProbedModel.from_audited(audited, source="live"))
            continue
        supported, default = _infer_efforts_from_model_id(model_id, contract)
        models.append(
            ProbedModel(
                model_id=model_id,
                display_label=label or model_id,
                provider_id=contract.provider_id,
                supported_efforts=supported,
                default_effort=default,
                context_window=None,
                local_only=False,
                source="live",
            )
        )
    return tuple(models)


def _infer_efforts_from_model_id(
    model_id: str, contract: CliContract
) -> tuple[tuple[str, ...], str | None]:
    """Recover the effort ladder of a model released after this audit.

    `agy` bakes the effort into the identifier (``gemini-3.7-flash-high``), so
    a suffix match is a fact about the id rather than a guess. Without one,
    fall back to the provider's whole enum and publish no default.
    """
    for effort in contract.accepted_efforts:
        if model_id.endswith(f"-{effort}"):
            return (effort,), effort
    return contract.accepted_efforts, None


def probe_all(
    *,
    endpoint: str = LM_STUDIO_MODELS_ENDPOINT,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    opener: HttpOpener = _default_opener,
    which: BinaryLocator = shutil.which,
    runner: CommandRunner = subprocess.run,
    cache_reader: CacheReader = _default_cache_reader,
    cli_timeout: float = CLI_PROBE_TIMEOUT_SECONDS,
    list_models: bool = True,
) -> CatalogSnapshot:
    """Probe every provider, in `PROVIDER_IDS` order.

    Pass ``list_models=False`` for spec 0013's launch probe: it keeps every
    provider local, at the cost of `agy`'s live listing.
    """
    probes: list[ProviderProbe] = []
    for provider_id in PROVIDER_IDS:
        if provider_id == "lm_studio_local":
            probes.append(probe_lm_studio(endpoint=endpoint, timeout=timeout, opener=opener))
        else:
            probes.append(
                probe_cli_provider(
                    provider_id,
                    which=which,
                    runner=runner,
                    cache_reader=cache_reader,
                    timeout=cli_timeout,
                    list_models=list_models,
                )
            )
    return CatalogSnapshot(providers=tuple(probes))


# ---------------------------------------------------------------------------
# Config drift audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftFinding:
    """One disagreement between the checked-in routing config and what the
    installed providers actually accept."""

    kind: DriftKind
    subject: str
    detail: str


def audit_config_drift(
    config: routing_config.RoutingConfig, snapshot: CatalogSnapshot | None = None
) -> tuple[DriftFinding, ...]:
    """Compare a routing config against the audited catalog.

    Four kinds of drift are reported: ``unknown_model`` (a configured
    provider names an identifier no installed CLI publishes),
    ``mismatched_provider`` (a model belongs to a different provider adapter),
    ``unsupported_effort`` (its default effort is outside that model's ladder),
    and ``unmapped_label`` (a label that resolves to no wire identifier — both
    in `supported_models` and in `roster_topology.role_fallback_chains`, since
    a fallback chain entry reaches a provider's ``--model`` flag exactly the
    way a `supported_models` entry does). Findings are the audit's regression
    guard — a config edit that reintroduces a stale identifier fails the test
    that pins them.
    """
    active_catalogs = _active_model_catalogs(snapshot)

    findings: list[DriftFinding] = []
    for provider_id, provider in config.providers.items():
        subject = f"providers.{provider_id}"
        try:
            model_id = resolve_model_id(provider.model, snapshot=snapshot)
        except UnknownModelError:
            findings.append(
                DriftFinding(
                    kind="unknown_model",
                    subject=subject,
                    detail=f"model {provider.model!r} is not published by any installed provider",
                )
            )
            continue
        # `provider.adapter` is a bare `str` read from routing-config.json, not
        # a verified `ProviderId` — a typo'd adapter (see
        # `test_an_unknown_provider_adapter_does_not_crash_the_audit`) must
        # still flow through this comparison rather than being coerced into
        # the Literal type it may not actually belong to.
        adapter = provider.adapter
        adapter_catalog = active_catalogs.get(adapter) if adapter in PROVIDER_IDS else None
        model_entry = adapter_catalog.get(model_id) if adapter_catalog is not None else None
        if model_entry is None:
            other_provider_id = _other_publishing_provider(model_id, adapter, active_catalogs)
            if other_provider_id is None:
                findings.append(
                    DriftFinding(
                        kind="unknown_model",
                        subject=subject,
                        detail=f"model {provider.model!r} is not published by any installed provider",
                    )
                )
            else:
                findings.append(
                    DriftFinding(
                        kind="mismatched_provider",
                        subject=subject,
                        detail=(
                            f"model {model_id!r} belongs to provider {other_provider_id!r}, "
                            f"not {adapter!r}"
                        ),
                    )
                )
            continue
        effort = provider.default_reasoning_effort
        if effort not in model_entry.supported_efforts:
            supported = list(model_entry.supported_efforts) or "none (this model has no effort ladder)"
            findings.append(
                DriftFinding(
                    kind="unsupported_effort",
                    subject=subject,
                    detail=f"default_reasoning_effort {effort!r} is unsupported by {model_id} (supported: {supported})",
                )
            )
    def label_drift_findings(labels: Iterable[str], subject_prefix: str) -> None:
        """Shared by both label loops below (`supported_models` and each
        `role_fallback_chains` entry): a label is `unmapped_label` drift
        either because it resolves to no wire identifier at all, or because
        the identifier it resolves to is not in any active catalog. Both
        call sites report the same `prefix[label]` subject shape, so only
        the prefix differs between them.
        """
        for label in labels:
            subject = f"{subject_prefix}[{label}]"
            try:
                model_id = resolve_model_id(label, snapshot=snapshot)
            except UnknownModelError:
                findings.append(
                    DriftFinding(
                        kind="unmapped_label",
                        subject=subject,
                        detail=f"label {label!r} maps to no wire identifier in the audited catalog",
                    )
                )
                continue
            if not any(model_id in catalog for catalog in active_catalogs.values()):
                findings.append(
                    DriftFinding(
                        kind="unmapped_label",
                        subject=subject,
                        detail=f"label {label!r} maps to no active wire identifier",
                    )
                )

    label_drift_findings(config.supported_models, "supported_models")
    for role, chain in config.roster_topology.role_fallback_chains.items():
        label_drift_findings(chain, f"roster_topology.{role}")
    deduplicated = tuple(dict.fromkeys(findings))
    return tuple(sorted(deduplicated, key=lambda finding: (finding.kind, finding.subject)))


def _active_model_catalogs(
    snapshot: CatalogSnapshot | None,
) -> dict[ProviderId, dict[str, AuditedModel | ProbedModel]]:
    """Build each adapter's current catalog without retaining stale models.

    A probe with at least one live model is authoritative for that provider:
    its listing replaces, rather than extends, the audited snapshot. Providers
    without such a listing retain their audited fallback. Explicit cross-
    provider ladders are published under their target adapter too, since they
    describe real model/provider pairings absent from the one-key audited map.
    """
    live_probes = (
        {
            probe.provider_id: probe
            for probe in snapshot.providers
            if any(model.source == "live" for model in probe.models)
        }
        if snapshot is not None
        else {}
    )
    catalogs: dict[ProviderId, dict[str, AuditedModel | ProbedModel]] = {}
    for provider_id in PROVIDER_IDS:
        probe = live_probes.get(provider_id)
        if probe is None:
            models: Iterable[AuditedModel | ProbedModel] = (
                model for model in _AUDITED_MODELS if model.provider_id == provider_id
            )
        else:
            models = probe.models
        catalogs[provider_id] = {model.model_id: model for model in models}

    for (provider_id, model_id), ladder in _CROSS_PROVIDER_EFFORT_LADDERS.items():
        if provider_id in live_probes:
            continue
        audited = AUDITED_MODEL_CATALOG.get(model_id)
        if audited is None:
            continue
        catalogs[provider_id][model_id] = ProbedModel(
            model_id=model_id,
            display_label=audited.display_label,
            provider_id=provider_id,
            supported_efforts=ladder,
            default_effort=audited.default_effort if audited.default_effort in ladder else None,
            context_window=audited.context_window,
            local_only=audited.local_only,
            source="audited",
        )
    return catalogs


def _other_publishing_provider(
    model_id: str,
    provider_id: str,
    active_catalogs: Mapping[ProviderId, Mapping[str, AuditedModel | ProbedModel]],
) -> ProviderId | None:
    """Find another adapter publishing ``model_id`` in the active catalogs.

    ``provider_id`` is the adapter string named in the config being audited —
    only ever used here for a ``!=`` exclusion, never as a lookup key — so it
    is typed as the bare, unverified ``str`` it actually is. A typo'd adapter
    (outside `PROVIDER_IDS`) legitimately reaches this function and must
    still be excluded by value, not rejected as a type mismatch.
    """
    for other_provider_id in PROVIDER_IDS:
        if other_provider_id != provider_id and model_id in active_catalogs[other_provider_id]:
            return other_provider_id
    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _render_text_report(snapshot: CatalogSnapshot) -> str:
    lines = ["Provider status", "---------------"]
    for probe in snapshot.providers:
        status = "online" if probe.available else "offline"
        location = probe.binary_path or probe.endpoint or "-"
        lines.append(f"  {probe.provider_id:<18} {status:<8} {location}")
        if probe.error:
            lines.append(f"      note: {probe.error}")
    lines.extend(("", "Models", "------"))
    for model in sorted(snapshot.models(), key=lambda entry: (entry.provider_id, entry.model_id)):
        efforts = ",".join(model.supported_efforts) or "-"
        default = model.default_effort or "-"
        lines.append(
            f"  [{model.source:<7}] {model.model_id:<28} {model.provider_id:<18} "
            f"efforts={efforts:<32} default={default}"
        )
    return "\n".join(lines)


def _render_drift_report(findings: tuple[DriftFinding, ...]) -> str:
    if not findings:
        return "Config drift\n------------\n  none — routing-config.json matches the audited catalog."
    lines = ["Config drift", "------------"]
    lines.extend(f"  {finding.kind:<20} {finding.subject}\n      {finding.detail}" for finding in findings)
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    opener: HttpOpener = _default_opener,
    which: BinaryLocator = shutil.which,
    runner: CommandRunner = subprocess.run,
    cache_reader: CacheReader = _default_cache_reader,
    config_loader: Callable[[], routing_config.RoutingConfig] = routing_config.load_routing_config,
) -> int:
    """Print a live provider/model report. Exits non-zero only for ``--audit``
    with findings, so it can gate CI on routing-config drift."""
    parser = argparse.ArgumentParser(description="Probe live model availability across CLI providers.")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable capability payload")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="also report routing-config.json drift; exit 1 when any is found",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        help=f"LM Studio probe timeout in seconds (default {DEFAULT_PROBE_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--cli-timeout",
        type=float,
        default=CLI_PROBE_TIMEOUT_SECONDS,
        help=f"CLI provider list-command timeout in seconds (default {CLI_PROBE_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help=(
            "skip the network-backed `agy models` listing (spec 0013's non-blocking launch probe); "
            "every provider stays local, at the cost of agy's live listing"
        ),
    )
    args = parser.parse_args(argv)
    stream = stdout if stdout is not None else sys.stdout

    snapshot = probe_all(
        timeout=args.timeout,
        opener=opener,
        which=which,
        runner=runner,
        cache_reader=cache_reader,
        cli_timeout=args.cli_timeout,
        list_models=not args.fast,
    )
    findings = audit_config_drift(config_loader(), snapshot=snapshot) if args.audit else ()

    if args.json:
        payload = snapshot.to_dict()
        if args.audit:
            payload["drift"] = [
                {"kind": finding.kind, "subject": finding.subject, "detail": finding.detail}
                for finding in findings
            ]
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        return 1 if findings else 0

    stream.write(_render_text_report(snapshot))
    stream.write("\n")
    if not args.audit:
        return 0

    stream.write("\n")
    stream.write(_render_drift_report(findings))
    stream.write("\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
