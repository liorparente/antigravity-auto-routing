"""Production orchestration for CriticalDialogue Planner/Critic debates."""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import stat
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from debate_state_machine import (
        ConsensusTable,
        CriticResponse,
        DebateRoundRecord,
        DebateSessionState,
        DebateState,
        RoundTurnResult,
        SecurityVeto,
        SecurityVetoHandler,
    )
    from debate_transport import (
        DebateTransport,
        RecurringFailureNotifier,
    )
    from dialogue_contracts import (
        AdvisoryOutcome,
        AdvisoryResolutionOption,
        AdvisoryRoundVerdict,
        AdvisoryStalemateReport,
        CriticVerdict,
        Occasion,
        VerdictContractResult,
    )
    from dialogue_degradation import (
        DegradationLadderState,
        DegradationRung,
    )
    from executive_dialogue_report import (
        ExecutiveDialogueReport,
    )
    from prompt_assembler import MissionCopy
    from sensitivity_redactor import TaskIdentity


if __package__:
    from . import consultation_policy as _consultation_policy
    from . import debate_state_machine as _debate_state_machine
    from . import debate_transport as _debate_transport
    from . import dialogue_contracts as _dialogue_contracts
    from . import dialogue_degradation as _dialogue_degradation
    from . import dialogue_transcript as _dialogue_transcript
    from . import executive_dialogue_report as _executive_dialogue_report
    from . import learned_state as _learned_state
    from . import learning_journal as _learning_journal
    from . import learning_outcomes as _learning_outcomes
    from . import production_invoker as _production_invoker
    from . import prompt_assembler as _prompt_assembler
    from . import routing_config
    from . import sensitivity_redactor as _sensitivity_redactor
else:
    import consultation_policy as _consultation_policy  # type: ignore[no-redef]
    import debate_state_machine as _debate_state_machine  # type: ignore[no-redef]
    import debate_transport as _debate_transport  # type: ignore[no-redef]
    import dialogue_contracts as _dialogue_contracts  # type: ignore[no-redef]
    import dialogue_degradation as _dialogue_degradation  # type: ignore[no-redef]
    import dialogue_transcript as _dialogue_transcript  # type: ignore[no-redef]
    import executive_dialogue_report as _executive_dialogue_report  # type: ignore[no-redef]
    import learned_state as _learned_state  # type: ignore[no-redef]
    import learning_journal as _learning_journal  # type: ignore[no-redef]
    import learning_outcomes as _learning_outcomes  # type: ignore[no-redef]
    import production_invoker as _production_invoker  # type: ignore[no-redef]
    import prompt_assembler as _prompt_assembler  # type: ignore[no-redef]
    import routing_config  # type: ignore[no-redef]
    import sensitivity_redactor as _sensitivity_redactor  # type: ignore[no-redef]

# `_learning_journal` and `_learning_outcomes` are imported to ensure sibling modules
# are loaded into `sys.modules` for dynamic resolution across dialogue transcript callers,
# so a static analyzer sees no direct attribute access on these two bindings here.
_ = (_learning_journal, _learning_outcomes)


def _current_production_invoker() -> Any:
    if __package__:
        package_invoker = sys.modules.get(f"{__package__}.production_invoker")
        if package_invoker is not None:
            return package_invoker
    return sys.modules.get("production_invoker", _production_invoker)

if not TYPE_CHECKING:
    AdvisoryOutcome = _dialogue_contracts.AdvisoryOutcome
    Occasion = _dialogue_contracts.Occasion
    AdvisoryRoundVerdict = _dialogue_contracts.AdvisoryRoundVerdict
    AdvisoryResolutionOption = _dialogue_contracts.AdvisoryResolutionOption
    AdvisoryStalemateReport = _dialogue_contracts.AdvisoryStalemateReport
    CriticVerdict = _dialogue_contracts.CriticVerdict
    VerdictContractResult = _dialogue_contracts.VerdictContractResult
    MissionCopy = _prompt_assembler.MissionCopy
    TaskIdentity = _sensitivity_redactor.TaskIdentity
    DegradationLadderState = _dialogue_degradation.DegradationLadderState
    DegradationRung = _dialogue_degradation.DegradationRung
    ExecutiveDialogueReport = _executive_dialogue_report.ExecutiveDialogueReport
    CriticResponse = _debate_state_machine.CriticResponse
    SecurityVeto = _debate_state_machine.SecurityVeto
    SecurityVetoHandler = _debate_state_machine.SecurityVetoHandler
    ConsensusTable = _debate_state_machine.ConsensusTable
    DebateRoundRecord = _debate_state_machine.DebateRoundRecord
    DebateSessionState = _debate_state_machine.DebateSessionState
    DebateState = _debate_state_machine.DebateState
    RoundTurnResult = _debate_state_machine.RoundTurnResult
    DebateTransport = _debate_transport.DebateTransport
    RecurringFailureNotifier = _debate_transport.RecurringFailureNotifier

CRITIC_VERDICT_APPROVE = _dialogue_contracts.CRITIC_VERDICT_APPROVE
CRITIC_VERDICT_REVISE = _dialogue_contracts.CRITIC_VERDICT_REVISE
extract_quotes = _dialogue_contracts.extract_quotes
extract_objections = _dialogue_contracts.extract_objections
verify_quotes = _dialogue_contracts.verify_quotes
parse_verdict_contract = _dialogue_contracts.parse_verdict_contract
_count_engagement_units = _dialogue_contracts._count_engagement_units
_is_tolerant_revise = _dialogue_contracts._is_tolerant_revise
_parse_critic_verdict = _dialogue_contracts._parse_critic_verdict

MISSION_COPY = _prompt_assembler.MISSION_COPY
build_planner_prompt = _prompt_assembler.build_planner_prompt
build_critic_prompt = _prompt_assembler.build_critic_prompt
build_canary_prompt = _prompt_assembler.build_canary_prompt
build_adjudicator_prompt = _prompt_assembler.build_adjudicator_prompt
build_stalemate_prompt = _prompt_assembler.build_stalemate_prompt
combine_panel_critic_feedback = _prompt_assembler.combine_panel_critic_feedback
extract_scoped_memory = _prompt_assembler.extract_scoped_memory
get_scoped_memory = _learned_state.get_scoped_memory
_MISSION_COPY = _prompt_assembler._MISSION_COPY
_MissionCopy = _prompt_assembler._MissionCopy

scan_sensitivity_markers = _sensitivity_redactor.scan_sensitivity_markers
derive_safe_task_identity = _sensitivity_redactor.derive_safe_task_identity
detect_sensitivity_marker = _sensitivity_redactor.detect_sensitivity_marker
SENSITIVITY_MARKERS = _sensitivity_redactor.SENSITIVITY_MARKERS

# Pure state-machine API, re-exported here for the historic orchestration API.
PANEL_TOPOLOGY_OCCASIONS = _debate_state_machine.PANEL_TOPOLOGY_OCCASIONS
is_panel_topology = _debate_state_machine.is_panel_topology
build_stalemate_report = _debate_state_machine.build_stalemate_report
evaluate_round_verdicts = _debate_state_machine.evaluate_round_verdicts
advance_debate_state = _debate_state_machine.advance_debate_state
ROUTING_CONFIG_PATH = _consultation_policy.ROUTING_CONFIG_PATH
DEFAULT_CONSULTATION_POLICY = _consultation_policy.DEFAULT_CONSULTATION_POLICY
load_consultation_policy = _consultation_policy.load_consultation_policy
_load_consultation_policy = load_consultation_policy
evaluate_quorum = _debate_state_machine.evaluate_quorum
_PANEL_TOPOLOGY_OCCASIONS = PANEL_TOPOLOGY_OCCASIONS
_is_panel_topology = is_panel_topology
_build_stalemate_report = build_stalemate_report


def _resolve_task_id(
    task_description: str, task_id: str | None, outcome: str
) -> str:
    """Resolve task identity through the sensitivity-safe pure helper."""
    return derive_safe_task_identity(task_description, task_id, outcome=outcome).task_id

BUDGET_DEGRADATION_MARKER = _dialogue_degradation.BUDGET_DEGRADATION_MARKER
DEFAULT_SESSION_DIALOGUE_CAP = _dialogue_degradation.DEFAULT_SESSION_DIALOGUE_CAP
_DEFAULT_DEGRADED_ROSTER_MODEL = _dialogue_degradation._DEFAULT_DEGRADED_ROSTER_MODEL
_DEGRADED_EFFORT = _dialogue_degradation._DEGRADED_EFFORT
_DEGRADED_ROUND_CAP = _dialogue_degradation._DEGRADED_ROUND_CAP
_DEGRADATION_RUNG_LABELS = _dialogue_degradation._DEGRADATION_RUNG_LABELS
_load_degraded_roster_model = _dialogue_degradation._load_degraded_roster_model
_load_dialogue_budget_config = _dialogue_degradation._load_dialogue_budget_config
resolve_degradation_rung = _dialogue_degradation.resolve_degradation_rung

format_budget_degradation_alert = _executive_dialogue_report.format_budget_degradation_alert
render_executive_summary = _executive_dialogue_report.render_executive_summary

ConsultationTranscript = _dialogue_transcript.ConsultationTranscript
DEGRADED_INDEPENDENCE_MARKER = _dialogue_transcript.DEGRADED_INDEPENDENCE_MARKER
CANARY_MARKER = _dialogue_transcript.CANARY_MARKER
_atomic_text_write = _dialogue_transcript._atomic_text_write
AdvisoryTelemetryRecord = _dialogue_transcript.AdvisoryTelemetryRecord
_default_task_id = _dialogue_transcript._default_task_id
_render_consultation_transcript = _dialogue_transcript._render_consultation_transcript
_render_sensitivity_halt_transcript = _dialogue_transcript._render_sensitivity_halt_transcript
render_consultation_transcript = _dialogue_transcript.render_consultation_transcript
render_sensitivity_halt_transcript = _dialogue_transcript.render_sensitivity_halt_transcript
format_transcript_markdown = _dialogue_transcript.format_transcript_markdown
_write_transcript = _dialogue_transcript._write_transcript
_append_jsonl_locked = _dialogue_transcript._append_jsonl_locked
_reduce_dialogue_round = _dialogue_transcript._reduce_dialogue_round
_build_telemetry_record = _dialogue_transcript._build_telemetry_record
_write_telemetry_record = _dialogue_transcript._write_telemetry_record
_write_dialogue_quality_record = _dialogue_transcript._write_dialogue_quality_record
_write_plan_outcome_record = _dialogue_transcript._write_plan_outcome_record

MAX_DEBATE_ROUNDS = 3

# Mirrors `agent_council.ESCALATION_FAILURE_THRESHOLD` rather than importing
# it — same reasoning as `SENSITIVITY_MARKERS` below (importing
# `agent_council` would pull `urllib.request`/`asyncio` into this module,
# and both files are loaded by path, not as a package). This is the
# protocol's 2-failure escalation rule: `agent_council.escalate_routing_effort`
# treats `attempts >= ESCALATION_FAILURE_THRESHOLD` as "escalate", and
# `needs_post_mortem_consultation` below fires its own escalation trigger at
# the same threshold, because it exists specifically to track that same
# protocol rule. `test_escalation_failure_threshold_matches_agent_council_constant`
# in `test_routing.py` keeps the two values from silently drifting apart.
ESCALATION_FAILURE_THRESHOLD = 2

WORKER_MODE_TOKEN = "[WORKER-MODE: NESTED-EXEC]"
LEGACY_WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"

InvokeWorker = Callable[[str, str, str], str]


class ReviewerAdapterProtocol(Protocol):
    provider_id: str

    async def review(
        self, envelope: str, round_spec: int, deadline: int
    ) -> dict[str, Any]: ...


# Discriminates how a consultation ended. `consensus_reached` on the result
# stays consistent with this: True only when outcome == "consensus". The
# next four are all "no consensus", distinguished for the caller because a
# stalemate, a malformed verdict, an unreachable worker, and a sensitivity
# halt each demand a different human response. "sensitivity_halt" is kept
# distinct from "stalemate" and "worker_error" rather than folded into
# either: it is a pre-flight refusal on the task text, so no worker was ever
# contacted — it is neither a disagreement (stalemate) nor a failure to
# reach one (worker_error). "canary" (spec 0003 ticket 08) is a sixth,
# orthogonal case, not a fifth flavor of "no consensus": a canary run never
# attempts to review a real mission artifact at all — there is no Planner
# proposal to agree or disagree about — so grouping it with the other four
# would misstate what happened. See `CanaryFixture` and `is_canary_dialogue`
# below for the full mechanism. "budget_skipped" (spec 0003 ticket 09) is a
# seventh, again orthogonal case: like "sensitivity_halt", no Planner or
# Critic is ever contacted — but for an unrelated reason (the session's
# dialogue budget is fully exhausted, not that the task text itself is
# unsafe), and unlike "sensitivity_halt" nothing about `task_description` is
# redacted, because there is nothing sensitive here to redact. It exists so
# a caller whose session ran out of budget still receives a real
# `AdvisoryDebateResult` — transcript and telemetry record both written,
# per this module's "every outcome gets both artifacts" invariant — rather
# than silently getting "no dialogue happened" with no trace. See
# `resolve_degradation_rung` below for the pure decision this outcome is
# reported for.

# Spec 0004 ticket 25 (fix pass 2): the occasions whose artifact under debate
# is actually a plan. "ambiguity" and "plan-review" both debate a Planner's
# architecture, so a consensus reached under either one is honestly describable
# as "the plan was accepted". "code-review" debates a diff and "post-mortem"
# debates a lesson — neither produces a plan at all, so there is no plan for a
# consensus under those two to have accepted. This is the one place that fact
# is declared: a fifth occasion added later must be added here, deliberately,
# rather than silently inheriting a plan verdict it may not have earned.
_PLAN_PRODUCING_OCCASIONS: tuple[Occasion, ...] = ("ambiguity", "plan-review")

# Mirrors `agent_council.SENSITIVE_PATTERNS` rather than importing it:
# importing `agent_council` would pull `urllib.request`, `asyncio`, and
# `fcntl` into a module whose docstring promises no HTTP client and full
# offline exercisability, and these files are loaded by path rather than as
# a package, so the import would need a `sys.path` hack. `test_routing.py`
# already loads both modules and asserts this tuple is a superset of
# `agent_council.SENSITIVE_PATTERNS`, so the duplication cannot silently
# drift apart. Same precedent this module already set for `MAX_DEBATE_ROUNDS`.
SENSITIVITY_MARKERS = _sensitivity_redactor.SENSITIVITY_MARKERS

# Spec 0003 (CriticalDialogue) ticket 03: the code-review occasion's risk
# signals — an oversized diff or a security-sensitive changed path — must be
# "read from config, not hardcoded literals in the Python source" per the
# ticket's acceptance criteria. `_CONFIG_PATH` is this module's own
# `routing-config.json`, mirroring `routing_check.CONFIG_PATH`'s
# `SCRIPT_DIR / "routing-config.json"` pattern; it is not imported from
# `routing_check` for the same reason `SENSITIVITY_MARKERS` above duplicates
# rather than imports `agent_council.SENSITIVE_PATTERNS` — these files are
# loaded by path, not as a package. `needs_code_review_consultation` accepts
# `config_path` as a keyword argument specifically so a caller (a test, or a
# future ticket) can point it at a different file and observe the trigger's
# answer change, which is the only way to prove a value is genuinely read
# from config rather than merely referenced by key.
#
# The two `DEFAULT_*` constants below are a fallback for a config file that
# is missing the `critical_dialogue` section (or one of its two keys) —
# mirroring `routing_check.load_code_extensions`'s identical
# `config.get("code_extensions", DEFAULT_CODE_EXTENSIONS)` pattern — never
# the value actually used when `routing-config.json` supplies its own
# `critical_dialogue` section, which it does as of this ticket. Sourced from
# `routing_config`'s typed default (ticket 42) rather than duplicated as a
# hand-maintained literal.
_CONFIG_PATH = routing_config.ROUTING_CONFIG_PATH
DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD = (
    routing_config.DEFAULT_ROUTING_CONFIG.critical_dialogue.code_review_diff_line_threshold
)
DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    routing_config.DEFAULT_ROUTING_CONFIG.critical_dialogue.security_sensitive_path_patterns
)


def resolve_hmac_secret(workspace_root: str | Path) -> bytes:
    """Resolve the council signing secret, failing closed when none is usable."""
    for variable in ("AGY_CALIBRATION_SECRET", "COUNCIL_REVIEW_SECRET"):
        value = os.environ.get(variable, "").strip()
        if value:
            return value.encode("utf-8")

    key_path = Path(workspace_root) / ".ralph" / "cache" / "calibration.key"
    try:
        key_stat = key_path.stat()
    except FileNotFoundError:
        key_stat = None
    except OSError as exc:
        raise RuntimeError(f"Council HMAC secret could not be read at {key_path}: {exc}") from exc
    if key_stat is not None and stat.S_ISREG(key_stat.st_mode):
        try:
            secret = key_path.read_bytes().strip()
        except OSError as exc:
            raise RuntimeError(f"Council HMAC secret could not be read at {key_path}: {exc}") from exc
        if secret:
            return secret

    raise RuntimeError(
        "Council HMAC secret resolution failed: AGY_CALIBRATION_SECRET and "
        "COUNCIL_REVIEW_SECRET are unset, and no non-empty workspace key was "
        "found at .ralph/cache/calibration.key."
    )


def _manifest_json_value(value: Any) -> Any:
    """Detach immutable state-machine values into JSON-serialisable values."""
    if isinstance(value, Mapping):
        return {str(key): _manifest_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_manifest_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_manifest_json_value(item) for item in value)
    return value


def write_council_manifest(
    status: str,
    run_id: str,
    workspace_root: str | Path,
    security_veto: SecurityVeto | None = None,
    events: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Atomically write an HMAC-signed CouncilPanel manifest and return its path."""
    sanitized_run_id = re.sub(r"[^a-zA-Z0-9_-]", "_", run_id)
    manifest_path = (
        Path(workspace_root) / ".ralph" / f"council-manifest-{sanitized_run_id}.json"
    )
    manifest: dict[str, Any] = {
        "metadata": {"status": status, "run_id": run_id},
        "events": _manifest_json_value(list(events or ())),
    }
    if security_veto is not None:
        manifest["security_veto"] = {
            "provider": security_veto.provider,
            "finding": _manifest_json_value(security_veto.finding),
        }

    canonical_json = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    manifest["council_hmac"] = hmac.new(
        resolve_hmac_secret(workspace_root), canonical_json, hashlib.sha256
    ).hexdigest()
    _atomic_text_write(
        manifest_path,
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    return str(manifest_path)


class PrivacyMode:
    AUTO = "auto"
    LOCAL_ONLY = "local-only"


@dataclass
class ReviewRequest:
    objective: str
    workspace_root: str
    subject: str = ""
    privacy_mode: str = PrivacyMode.AUTO
    # Spec 0012 / workflow-v2 ticket 07: opt into the 4-perspective Council
    # panel (`ReviewCouncil._resolve_perspective_adapters`) and its 1-shot
    # fast-path/security-veto/stalemate-escalation flow, instead of the
    # legacy 3-provider, hash-ratified round protocol. Defaults to `False`
    # so every existing caller keeps its current behavior unchanged.
    by_perspective: bool = False

    def __post_init__(self) -> None:
        if not self.workspace_root:
            raise ValueError("workspace_root is required")
        if self.privacy_mode not in (PrivacyMode.AUTO, PrivacyMode.LOCAL_ONLY):
            raise ValueError("Invalid privacy mode")


@dataclass
class ReviewOutcome:
    status: str
    run_id: str
    report_path: str | None = None
    manifest_path: str | None = None
    unresolved_blockers: int = 0
    source_changed: bool = False
    # Populated when a Council perspective review round 1 fails to reach
    # quorum (or fast path is disabled) and stalemate escalation to the
    # Adjudicator/HITL is performed (ticket 07, ADR 0012 1-shot fast path).
    stalemate_report: AdvisoryStalemateReport | None = None


def _load_provider_adapters() -> Any:
    """Load the council-review adapters only when a council run needs them."""
    module_name = "provider_adapters"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = (
        Path(__file__).resolve().parents[1]
        / "council-review"
        / "scripts"
        / f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class ReviewCouncil:
    def __init__(self, policy_path: str | Path = ROUTING_CONFIG_PATH) -> None:
        resolved_policy_path = Path(policy_path)
        if not resolved_policy_path.exists() and ROUTING_CONFIG_PATH.exists():
            resolved_policy_path = ROUTING_CONFIG_PATH
        self.policy = load_consultation_policy(resolved_policy_path)

    _resolve_secret = staticmethod(resolve_hmac_secret)

    def _load_weights(self, workspace_root: str) -> dict[str, float]:
        weighting = self.policy.get("weighting", {})
        weights = dict(weighting.get("initial_weights", {}))
        # Ticket 07: perspective weights live alongside provider weights in
        # the same dict — `ConsensusTable._identity` reads a vote's
        # `perspective` field ahead of its `provider`/`critic_id`, so the two
        # key spaces (`"claude"`/`"codex"`/`"gemini"` vs.
        # `"reviewer_architecture"`/etc.) never collide.
        weights.update(self.policy.get("council_policy", {}).get("perspective_weights", {}))
        lo = weighting.get("min_weight", 0.05)
        hi = weighting.get("max_weight", 0.65)
        dynamic_path = weighting.get("dynamic_weights_path")

        if not dynamic_path:
            return weights

        full_path = os.path.join(workspace_root or ".", dynamic_path)
        if not os.path.isfile(full_path):
            return weights

        try:
            with open(full_path, "r", encoding="utf-8") as stream:
                dynamic = json.load(stream)
            if isinstance(dynamic, dict):
                for provider, value in dynamic.items():
                    if provider not in weights:
                        continue
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    weights[provider] = max(lo, min(hi, float(value)))
        except (json.JSONDecodeError, OSError):
            pass

        return weights

    def _resolve_adapters(
        self, request: ReviewRequest
    ) -> list[ReviewerAdapterProtocol]:
        build_adapter = _load_provider_adapters().build_adapter
        if request.privacy_mode == PrivacyMode.LOCAL_ONLY:
            adapters = [build_adapter(config) for config in self.policy.get("adjudicators", [])]
            if not adapters:
                raise RuntimeError(
                    "local-only review requested but no local adjudicator is configured "
                    "— failing closed rather than egressing data to cloud."
                )
            return adapters
        if request.by_perspective:
            return self._resolve_perspective_adapters(request)
        providers = self.policy.get("providers", [])
        return [build_adapter(config) for config in providers]

    def _resolve_perspective_adapters(self, request: ReviewRequest) -> list[ReviewerAdapterProtocol]:
        """Resolve the 4 Council perspective reviewers (ticket 07) via
        `RoleResolver`, one `PerspectiveReviewerAdapter` per
        `ReviewerPerspective` in `dialogue_contracts.REVIEWER_PERSPECTIVES`,
        each seeded with `request.objective` as its `task_description` so a
        reviewer's prompt describes the actual mission under review rather
        than `PerspectiveReviewerAdapter`'s generic "Proposal review"
        default."""
        module = _load_provider_adapters()
        resolver = _current_production_invoker().get_default_role_resolver()
        adapters: list[ReviewerAdapterProtocol] = []
        for perspective in _dialogue_contracts.REVIEWER_PERSPECTIVES:
            resolved = resolver.resolve_role(perspective)
            adapters.append(
                module.PerspectiveReviewerAdapter(
                    perspective,
                    model=resolved.model,
                    effort=resolved.reasoning_effort,
                    task_description=request.objective,
                )
            )
        return adapters

    def _hash_source(self, path: str) -> str:
        if not path or not os.path.exists(path):
            return ""
        hasher = hashlib.sha256()
        if os.path.isfile(path):
            with open(path, "rb") as stream:
                hasher.update(stream.read())
        else:
            for root, _, files in sorted(os.walk(path)):
                for name in sorted(files):
                    source_path = os.path.join(root, name)
                    relative_path = os.path.relpath(source_path, path)
                    hasher.update(f"{relative_path}\0".encode())
                    with open(source_path, "rb") as stream:
                        hasher.update(stream.read())
        return hasher.hexdigest()

    async def _execute_round(
        self,
        adapters: Sequence[ReviewerAdapterProtocol],
        envelope: str,
        round_num: int,
        deadline: int,
    ) -> list[dict[str, Any]]:
        tasks = [adapter.review(envelope, round_num, deadline) for adapter in adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_results: list[dict[str, Any]] = []
        for adapter, result in zip(adapters, results):
            if isinstance(result, Exception):
                valid_results.append(
                    {
                        "provider": getattr(adapter, "provider_id", "unknown"),
                        "vote": "abstain",
                        "confidence": 0.0,
                        "error": str(result),
                    }
                )
            elif isinstance(result, dict):
                valid_results.append(result)
            else:
                valid_results.append(
                    {
                        "provider": getattr(adapter, "provider_id", "unknown"),
                        "vote": "abstain",
                        "confidence": 0.0,
                        "error": "Invalid adapter response payload",
                    }
                )
        return valid_results

    _write_manifest = staticmethod(write_council_manifest)

    def _check_veto_and_halt(
        self,
        votes: Sequence[dict[str, Any]],
        veto_handler: SecurityVetoHandler,
        run_id: str,
        workspace_root: str,
    ) -> ReviewOutcome | None:
        veto = veto_handler.check(votes)
        if veto is None:
            return None
        manifest_path = self._write_manifest(
            "SECURITY_HALT", run_id, workspace_root, veto
        )
        return ReviewOutcome(
            status="SECURITY_HALT",
            run_id=run_id,
            unresolved_blockers=1,
            manifest_path=manifest_path,
        )

    async def review(
        self,
        request: ReviewRequest,
        custom_adapters: Sequence[ReviewerAdapterProtocol] | None = None,
    ) -> ReviewOutcome:
        if request.by_perspective:
            return await self._review_by_perspective(request, custom_adapters)

        initial_hash = self._hash_source(request.subject)
        weights = self._load_weights(request.workspace_root)
        run_id = f"run-{uuid.uuid4().hex[:8]}"

        weighting_policy = self.policy.get("weighting", {})
        security_policy = self.policy.get("security_veto", {})
        veto_handler = SecurityVetoHandler(
            veto_severities=security_policy.get("veto_severities", ["critical", "high"]),
            security_threshold=security_policy.get("security_threshold", 0.80),
            enabled=security_policy.get("enabled", True),
        )
        table = ConsensusTable(
            self.policy.get("consensus_policy", []),
            weights=weights,
            quorum_threshold=weighting_policy.get("quorum_threshold", 0.60),
        )
        adapters = custom_adapters if custom_adapters is not None else self._resolve_adapters(request)

        deadline_r1 = self.policy.get("deadlines_seconds", {}).get("round_1", 120)
        round1_votes = await self._execute_round(adapters, request.objective, 1, deadline_r1)
        veto_outcome = self._check_veto_and_halt(
            round1_votes, veto_handler, run_id, request.workspace_root
        )
        if veto_outcome is not None:
            return veto_outcome

        candidate_hash = hashlib.sha256(request.objective.encode("utf-8")).hexdigest()
        deadline_r2 = self.policy.get("deadlines_seconds", {}).get("round_2", 60)
        round2_votes = await self._execute_round(adapters, candidate_hash, 2, deadline_r2)
        veto_outcome = self._check_veto_and_halt(
            round2_votes, veto_handler, run_id, request.workspace_root
        )
        if veto_outcome is not None:
            return veto_outcome

        consensus = table.evaluate(round2_votes, expected_hash=candidate_hash)
        if consensus in ("MATERIAL_DISAGREEMENT", "UNRESOLVED"):
            deadline_r3 = self.policy.get("deadlines_seconds", {}).get("round_3", 60)
            round3_votes = await self._execute_round(adapters, candidate_hash, 3, deadline_r3)
            veto_outcome = self._check_veto_and_halt(
                round3_votes, veto_handler, run_id, request.workspace_root
            )
            if veto_outcome is not None:
                return veto_outcome

            consensus = table.evaluate(round3_votes, expected_hash=candidate_hash)
            if consensus == "MATERIAL_DISAGREEMENT":
                consensus = "UNRESOLVED"

        final_hash = self._hash_source(request.subject)
        source_changed = bool(request.subject) and initial_hash != final_hash
        if source_changed:
            consensus = "UNRESOLVED"

        manifest_path = self._write_manifest(consensus, run_id, request.workspace_root)
        return ReviewOutcome(
            status=consensus,
            run_id=run_id,
            source_changed=source_changed,
            manifest_path=manifest_path,
        )

    async def _review_by_perspective(
        self,
        request: ReviewRequest,
        custom_adapters: Sequence[ReviewerAdapterProtocol] | None,
    ) -> ReviewOutcome:
        """The 4-perspective Council review flow (spec 0012 / workflow-v2
        ticket 07, ADR 0012's 1-shot fast path): round 1 runs all 4
        perspective reviewers concurrently (`_execute_round` already gathers
        every adapter's `review()` call via `asyncio.gather`); a unilateral
        security veto halts immediately. Round 1 is the panel's only round —
        it either reaches weighted quorum and finalizes there, or it does
        not and the review escalates immediately to the configured
        Adjudicator (or HITL when none is configured) via
        `_route_perspective_stalemate`, rather than re-polling the same
        panel for a second or third round.
        """
        initial_hash = self._hash_source(request.subject)
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        council_policy = self.policy.get("council_policy", {})

        security_policy = council_policy.get("security_veto", self.policy.get("security_veto", {}))
        veto_handler = SecurityVetoHandler(
            veto_severities=security_policy.get("veto_severities", ["critical", "high"]),
            security_threshold=security_policy.get("security_threshold", 0.80),
            enabled=security_policy.get("enabled", True),
        )
        quorum_threshold = council_policy.get(
            "quorum_threshold", self.policy.get("weighting", {}).get("quorum_threshold", 0.60)
        )
        table = ConsensusTable(
            council_policy.get("consensus_policy", self.policy.get("consensus_policy", [])),
            weights=self._load_weights(request.workspace_root),
            quorum_threshold=quorum_threshold,
        )
        deadlines = council_policy.get("deadlines_seconds", {})

        adapters = custom_adapters if custom_adapters is not None else self._resolve_adapters(request)

        candidate_hash = hashlib.sha256(request.objective.encode("utf-8")).hexdigest()

        deadline = deadlines.get("round_1", 45)
        votes = await self._execute_round(adapters, request.objective, 1, deadline)
        veto_outcome = self._check_veto_and_halt(
            votes, veto_handler, run_id, request.workspace_root
        )
        if veto_outcome is not None:
            return veto_outcome

        fast_path_enabled = council_policy.get("fast_path_enabled", True)
        if fast_path_enabled:
            outcome = table.evaluate(votes, expected_hash=candidate_hash)
            if outcome in ("UNANIMOUS", "QUALIFIED"):
                return self._finalize_perspective_outcome(outcome, run_id, request, initial_hash)

        return await self._route_perspective_stalemate(votes, run_id, request, initial_hash)

    def _finalize_perspective_outcome(
        self,
        status: str,
        run_id: str,
        request: ReviewRequest,
        initial_hash: str,
        events: list[dict[str, Any]] | None = None,
        stalemate_report: AdvisoryStalemateReport | None = None,
    ) -> ReviewOutcome:
        final_hash = self._hash_source(request.subject)
        source_changed = bool(request.subject) and initial_hash != final_hash
        final_status = "UNRESOLVED" if source_changed else status
        manifest_path = self._write_manifest(
            final_status, run_id, request.workspace_root, events=events
        )
        return ReviewOutcome(
            status=final_status,
            run_id=run_id,
            source_changed=source_changed,
            unresolved_blockers=1 if source_changed or final_status == "UNRESOLVED" else 0,
            manifest_path=manifest_path,
            stalemate_report=stalemate_report,
        )

    @staticmethod
    def _build_perspective_stalemate_report(
        request: ReviewRequest, votes: Sequence[dict[str, Any]]
    ) -> AdvisoryStalemateReport:
        """Summarize a final round's per-perspective votes into the
        `AdvisoryStalemateReport` shape via `build_stalemate_report`'s
        `perspective_positions` parameter (ticket 07's addition)."""
        positions: dict[str, str] = {}
        for vote in votes:
            perspective = str(vote.get("perspective") or vote.get("provider") or "unknown")
            verdict = str(vote.get("vote") or vote.get("verdict") or "unparseable")
            findings = vote.get("findings") or []
            first_claim = ""
            if findings:
                first_finding = findings[0]
                first_claim = (
                    first_finding.get("claim", "")
                    if isinstance(first_finding, dict)
                    else str(getattr(first_finding, "claim", ""))
                )
            summary = f"verdict: {verdict}" + (f"\nfinding: {first_claim}" if first_claim else "")
            positions[perspective] = summary
        return build_stalemate_report(request.objective, perspective_positions=positions)

    async def _route_perspective_stalemate(
        self,
        votes: Sequence[dict[str, Any]],
        run_id: str,
        request: ReviewRequest,
        initial_hash: str,
    ) -> ReviewOutcome:
        """Panel failed to reach weighted quorum in 1-shot execution (or fast
        path disabled): escalate to the configured Adjudicator, or fail
        closed to HITL when none is configured, carrying the summarized
        AdvisoryStalemateReport either way (ticket 07 acceptance criterion)."""
        stalemate_report = self._build_perspective_stalemate_report(request, votes)
        adjudicators = self.policy.get("adjudicators", [])

        if adjudicators:
            # Fail closed on any adjudicator trouble — an unavailable adapter
            # id, an offline endpoint, a raised exception, or a malformed
            # payload all fall through to the UNRESOLVED/HITL path below
            # rather than being read as an implicit approval. Only an
            # adjudicator that actively returns APPROVE resolves a stalemate.
            try:
                build_adapter = _load_provider_adapters().build_adapter
                adjudicator_adapter = build_adapter(adjudicators[0])
                adjudicator_prompt = build_adjudicator_prompt(
                    request.objective,
                    stalemate_report.planner_position,
                    stalemate_report.critic_position,
                )
                deadlines = self.policy.get("council_policy", {}).get("deadlines_seconds", {})
                deadline = deadlines.get("adjudicator", deadlines.get("round_3", 60))
                result = await adjudicator_adapter.review(adjudicator_prompt, 4, deadline)
            except Exception:  # noqa: BLE001
                result = None
                adjudicator_adapter = None
            resolved_vote = (
                str(result.get("vote", "")).strip().casefold() if isinstance(result, dict) else ""
            )
            if resolved_vote in ("approve", "approved", "unanimous"):
                return self._finalize_perspective_outcome(
                    "QUALIFIED",
                    run_id,
                    request,
                    initial_hash,
                    events=[
                        {
                            "type": "adjudicator_resolved",
                            "adjudicator": getattr(adjudicator_adapter, "provider_id", "unknown"),
                        }
                    ],
                    stalemate_report=stalemate_report,
                )

        return self._finalize_perspective_outcome(
            "UNRESOLVED",
            run_id,
            request,
            initial_hash,
            events=[{"type": "stalemate_report", **dataclasses.asdict(stalemate_report)}],
            stalemate_report=stalemate_report,
        )


def _critic_response_from_payload(
    critic_id: str,
    raw_response: str,
    model_name: str | None = None,
) -> CriticResponse:
    """Build the state-machine vote used by veto and panel manifest policy."""
    payload = _current_production_invoker().extract_review_payload(
        raw_response,
        default_candidate_hash=None,
    )
    findings = payload.get("findings", ())
    if not isinstance(findings, (list, tuple)):
        findings = ()
    identity_source = model_name if model_name is not None else critic_id
    model_family = classify_model_family(identity_source)
    provider_id = "codex" if model_family == "codex-gpt" else model_family
    if model_name is None and provider_id not in {"claude", "codex", "gemini"}:
        provider_id = critic_id
    return CriticResponse(
        critic_id=provider_id,
        response=raw_response,
        verdict=str(payload.get("vote", "abstain")),
        confidence=payload.get("confidence"),
        candidate_hash=payload.get("candidate_hash"),
        findings=tuple(finding for finding in findings if isinstance(finding, dict)),
    )

# Spec 0003 (CriticalDialogue) ticket 07: "Family, not model, is the
# independence unit" (spec's Implementation Decisions paragraph of the same
# name). A family is a provider lineage — the Claude family, the Codex/GPT
# family, the Gemini family, and each local model lineage counts as its own
# family — not an individual model name. `classify_model_family` is the pure
# function every later piece of this ticket's infrastructure is built on:
# the roster resolver below calls it to decide whether two roles' assigned
# models are independent, and a caller wiring telemetry (ticket 10) or a
# canary fixture (ticket 08) can call it directly on any model name without
# reaching into this module's other machinery.
#
# The four cloud lineages are recognized by a small, fixed vocabulary of
# provider-name substrings, not by an exhaustive model list pulled from
# `routing-config.json`'s `supported_models` — deliberately, per this
# ticket's own brief ("don't hardcode an exhaustive model list ... but use
# your judgment"). Provider lineage is a property of the name itself: every
# Claude model's name contains "claude", and that stays true for models this
# file has never seen; `supported_models` is merely today's roster snapshot
# and would need an edit for every new tier that ships, which would make
# family classification silently stale the moment a new model landed in
# config but not in this list. "codex" and "gpt" both resolve to one
# "codex-gpt" family — spelled out explicitly in this ticket's own
# instructions — because `routing-config.json`'s `critic` role block already
# lists "Codex 5.6 Sol" and "GPT-OSS 120B (Medium)" as interchangeable
# alternatives within one role, i.e. this repo's own config already treats
# them as the same lineage for routing purposes.
#
# Anything matching none of the four substrings is treated as its own local
# lineage — the spec's "each local model lineage counts as its own family" —
# derived from the model's own name rather than looked up in a table this
# module would otherwise need to keep in sync with every local model ever
# loaded into LM Studio. The leading run of alphabetic characters in the
# name, case-folded, is that lineage's identifier: "Gemma 4 E4B" and a
# future "Gemma 2 9B" both become "gemma" (two checkpoints of one base model
# are one lineage), while "Qwen3-Coder-Next" becomes "qwen" — a genuinely
# different local model gets a genuinely different family. This is a
# heuristic, not a registry lookup, on purpose: it needs no maintenance as
# new local checkpoints are loaded, which a hardcoded local-model list would.
_CLOUD_FAMILY_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("claude", "claude"),
    ("gemini", "gemini"),
    ("codex", "codex-gpt"),
    ("gpt", "codex-gpt"),
)

_LOCAL_LINEAGE_PATTERN = re.compile(r"[a-zA-Z]+")


def classify_model_family(model_name: str) -> str:
    """Return the provider-lineage family `model_name` belongs to.

    Pure and total: every input string maps to some family string, and this
    never raises. See the module comment above `_CLOUD_FAMILY_SUBSTRINGS`
    for the exact rule and why the cloud vocabulary is a small fixed set
    rather than a model list, and why a local model's family is derived
    from its own name rather than looked up. Matching is case-insensitive
    and substring-based (`"claude" in lowered`), same style
    `_detect_sensitivity_marker` already uses for `SENSITIVITY_MARKERS`.

    The local-lineage fallback uses `_LOCAL_LINEAGE_PATTERN.match`, which
    Python anchors at index 0, not `.search` — deliberately: this function's
    own contract is "the LEADING run of alphabetic characters", and `.match`
    is what actually enforces that anchor. `.search` would instead find the
    first alphabetic run anywhere in the string, which for a digit-led local
    name (e.g. a hypothetical "70B-Instruct" or "4B-Mixtral") would pick out
    whatever short fragment happens to occur first ("b", "instruct") — an
    unstable, coincidental identifier two genuinely unrelated local models
    could easily collide into, silently defeating the independence guarantee
    `resolve_roster` exists to enforce. A `model_name` with no leading
    alphabetic run at all (digit-led, or empty) instead falls through to the
    final fallback below: the full lowercased, stripped name is used as its
    own family identifier, or `"unknown"` if that is also empty. That
    fallback can never falsely equate two different digit-led names (each
    gets its own family, keyed off its own full text), and never leaves a
    "different family" check silently satisfied by an empty-string family
    either.
    """
    lowered = model_name.lower()
    for substring, family in _CLOUD_FAMILY_SUBSTRINGS:
        if substring in lowered:
            return family
    match = _LOCAL_LINEAGE_PATTERN.match(model_name)
    if match:
        return match.group(0).lower()
    return lowered.strip() or "unknown"


def is_local_family(family: str) -> bool:
    """True when `family` is a local model lineage, not one of the cloud
    families `_CLOUD_FAMILY_SUBSTRINGS` recognizes.

    Spec 0003 (CriticalDialogue) ticket 11: the sensitive-task path needs
    "is this family local" as its own predicate, distinct from
    `classify_model_family`'s "which family is this". `resolve_roster`'s
    `is_family_reachable` seam (ticket 07) is already exactly the callable
    shape `(family) -> bool` a sensitive task needs to compose local-only
    reachability with — `lambda family: is_local_family(family) and
    reachability_check(family)`, wired in at `run_advisory_consultation_debate`'s
    roster-resolution block — so this function's whole job is to be that one
    missing half of the composition, nothing more.

    Deliberately derived from `_CLOUD_FAMILY_SUBSTRINGS` — the set of family
    names those pairs actually produce — rather than a second, hand-written
    list of cloud family names. The cloud vocabulary already lives in
    exactly one place in this module; a second list of the same four names
    would only ever be a duplicate that could silently drift the next time a
    cloud family is added, renamed, or split. `family` is expected to
    already be `classify_model_family`'s output, which is always lowercase
    (see that function's own docstring for why), so this performs no
    case-folding of its own — every real caller (`resolve_roster`'s
    resolution loop) already hands this a lowercase family string.
    """
    cloud_families = {cloud_family for _substring, cloud_family in _CLOUD_FAMILY_SUBSTRINGS}
    return family not in cloud_families


# Spec 0003 (CriticalDialogue) ticket 07: the two topologies a roster can be
# resolved for. "critic_a" doubles as pair mode's sole critic role — the
# same "`critic_position` means Critic A in panel mode" convention
# `AdvisoryStalemateReport`'s docstring already established (pair mode
# simply never resolves a `critic_b`), rather than inventing a third,
# pair-only role name that would need its own fallback chain in config for
# no semantic reason: a pair's one Critic and a panel's Critic A play
# exactly the same role.
RosterTopology = Literal["pair", "panel"]
RosterRole = Literal["planner", "critic_a", "critic_b"]

_PAIR_ROSTER_ROLES: tuple[RosterRole, ...] = ("planner", "critic_a")
_PANEL_ROSTER_ROLES: tuple[RosterRole, ...] = ("planner", "critic_a", "critic_b")

# The independence unit's own callable seam: `(family) -> bool`, injected by
# the caller exactly like `InvokeWorker` above — this is what lets
# `resolve_roster` be exercised offline with a scripted fake instead of a
# real reachability probe (a curl to LM Studio, a provider health check),
# per this ticket's own instruction and the precedent every other seam in
# this module already sets.
IsFamilyReachable = Callable[[str], bool]


class RosterResolutionError(RuntimeError):
    """Raised when `resolve_roster` finds no reachable family for a role at all.

    Distinct from degraded independence: degradation means a family had to
    be reused across roles, and `resolve_roster` still returns a complete,
    usable result for that case — see `RosterResolution.degraded_independence`.
    This error means a role's entire fallback chain, every family it names,
    reported unreachable, so no assignment exists for that role at all. A
    caller integrating this into `run_advisory_consultation_debate` treats
    it the same as any other pre-flight failure to reach a worker: fail
    closed, never fabricate a roster to paper over it.
    """


# Spec 0003 (CriticalDialogue) ticket 07: each role's ordered fallback chain
# of candidate models, config-driven (see `_load_roster_fallback_chains`)
# rather than hardcoded here — mirroring the `critical_dialogue` section
# ticket 03 added to `routing-config.json`, and for the identical reason: a
# config read is the only way a test (or a future operator) can point this
# module at a different roster and observe the resolver's answer change,
# which is the only way to prove a value is genuinely read from config
# rather than merely referenced by key. These `DEFAULT_*` chains are the
# fallback for a config file missing the `roster_topology` section (or one
# of its role keys) — mirroring `DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD`'s
# identical role above — never what production actually uses, since
# `routing-config.json` supplies its own section as of this ticket.
#
# Every chain's first entry is exactly the parameter default
# `run_advisory_consultation_debate` already shipped before this ticket
# existed (`planner_model="Claude Opus 5 (Thinking)"`,
# `critic_a_model="Codex 5.6 Sol"`, `critic_b_model="Gemini 3.6 Flash"`), so
# a fully-reachable environment resolves to exactly the roster those
# hardcoded defaults already produced — this ticket adds a resolution
# *path*, it does not change what "everything is up" already looked like.
# Each chain's later entries walk through the protocol's documented
# fallback ordering (`protocol.md` section 3.5, "Complex/Planning: Claude
# Opus 5 (Thinking) -> Claude Fable/Opus 4.8 -> codex Sol") before ending on
# a local model, so "local families qualify" (the spec's own phrase) is a
# real, reachable last resort in every chain, not merely a claim. Sourced
# from `routing_config`'s typed default (ticket 42) rather than duplicated
# as a hand-maintained literal.
DEFAULT_ROSTER_FALLBACK_CHAINS: dict[str, tuple[str, ...]] = dict(
    routing_config.DEFAULT_ROUTING_CONFIG.roster_topology.role_fallback_chains
)


def _load_roster_fallback_chains(config_path: Path) -> dict[str, tuple[str, ...]]:
    """Read each role's fallback chain from `config_path`'s `roster_topology`
    section (via `routing_config`, ticket 42), falling back to
    `DEFAULT_ROSTER_FALLBACK_CHAINS` per role (or wholesale when the section
    itself is absent) — same pattern `_load_code_review_risk_config` above
    uses for `critical_dialogue`, including its no-try/except contract: a
    missing or malformed `config_path` raises whatever
    `routing_config.load_routing_config` raises rather than being silently
    swallowed, because production always calls this with the default
    `_CONFIG_PATH`, which is checked into the repo.
    """
    configured_chains = routing_config.load_routing_config(config_path).roster_topology.role_fallback_chains
    resolved: dict[str, tuple[str, ...]] = {}
    for role, default_chain in DEFAULT_ROSTER_FALLBACK_CHAINS.items():
        resolved[role] = tuple(configured_chains.get(role, default_chain))
    return resolved


@dataclass(frozen=True)
class RosterAssignment:
    """One role's resolved model, and the family `classify_model_family`
    placed it in."""

    role: RosterRole
    model: str
    family: str


@dataclass(frozen=True)
class RosterResolution:
    """The result of resolving one topology's roster against live reachability.

    `degraded_independence` is this ticket's own exposed signal: True
    whenever any two roles in `assignments` ended up sharing a family,
    because `resolve_roster` could not find enough distinct reachable
    families to give every role its own. See `resolve_roster`'s docstring
    for the exact "single family remains" reading this field implements —
    this dataclass is deliberately where a caller (including ticket 10's
    telemetry-extension work) reads whether degradation occurred, per this
    ticket's own instruction to expose it here even where wiring it further
    would overstep this ticket's scope.
    """

    topology: RosterTopology
    assignments: tuple[RosterAssignment, ...]
    degraded_independence: bool

    def model_for(self, role: RosterRole) -> str:
        """The resolved model for `role`.

        Raises `KeyError` if `role` was never part of this resolution's
        topology (e.g. asking a pair-mode result for `"critic_b"`) —
        deliberately loud rather than returning an empty string, since that
        is always a call-site bug, not a legitimate "no such role" state.
        """
        for assignment in self.assignments:
            if assignment.role == role:
                return assignment.model
        raise KeyError(f"role {role!r} is not part of this {self.topology} roster")


def resolve_roster(
    topology: RosterTopology,
    *,
    is_family_reachable: IsFamilyReachable,
    config_path: Path = _CONFIG_PATH,
) -> RosterResolution:
    """Assign a model to each role `topology` requires, preferring a
    distinct family per role and degrading to family reuse only when the
    reachable families genuinely run out.

    Roles are resolved in a fixed order — `planner`, then `critic_a`, then
    (panel only) `critic_b` — each walking its own config-driven fallback
    chain (`_load_roster_fallback_chains`) and classifying every candidate
    with `classify_model_family`. For each role, two passes:

    1. Prefer the first candidate whose family both `is_family_reachable`
       and is not already claimed by an earlier role in this resolution.
       This is "substitute the next family in the fallback chain" (the
       ticket's own phrase): a role whose *preferred* family is unreachable
       does not immediately degrade, it just keeps walking its own chain,
       exactly the same as any other unreachable candidate.
    2. Only if no candidate offers a fresh, reachable family does resolution
       fall back to the first candidate that is merely reachable (even
       though its family is already used elsewhere in this roster) — and
       only this second pass sets `degraded_independence`.

    A role whose chain offers no reachable family at all, in either pass,
    raises `RosterResolutionError` — resolution never fabricates an
    assignment nobody can actually reach.

    **Reading "a single family remains" (spec 0003's "Degraded
    independence, never silence" paragraph).** The spec's literal words —
    "Only when a single family remains does the dialogue run same-family" —
    admit two readings: (a) exactly one family is reachable across the
    entire environment, or (b) resolution has been forced to reuse a family
    already claimed within *this* roster, however many families remain
    reachable elsewhere. This function implements reading (b), for two
    reasons. First, the spec's own next clause is about causation, not a
    global census: "and then it carries an explicit degraded-independence
    marker" — the marker exists to flag *this dialogue's* weakened
    independence, and a dialogue's independence is exactly as weakened by
    "2 roles, 1 shared family, while a third unrelated family sits
    reachable but unused by this topology" as by "literally 1 family total
    reachable" — reading (a) would leave the former case silently
    unflagged, which contradicts the spec's own repeated "Never silently"
    refrain (Problem Statement, Implementation Decisions, and User Story 14
    all restate it). Second, reading (b) is strictly the more conservative
    of the two — it flags a superset of what reading (a) would — so it
    never under-reports a compromised roster, only ever over-reports
    relative to the narrower literal reading, which is the safe direction
    to err in for an audit signal. Concretely: a panel needing 3 families
    with only 2 reachable is degraded under this reading (one family
    serves two roles) even though "a single family" was never literally
    true — this is the ticket's own suggested resolution for that exact
    ambiguity, spelled out here rather than left implicit.

    Raises `ValueError` for a `topology` that is not `"pair"` or `"panel"`
    — a call-site programming error, not a roster outcome.
    """
    if topology == "pair":
        roles: tuple[RosterRole, ...] = _PAIR_ROSTER_ROLES
    elif topology == "panel":
        roles = _PANEL_ROSTER_ROLES
    else:
        raise ValueError(f"unknown roster topology {topology!r}; expected 'pair' or 'panel'")

    chains = _load_roster_fallback_chains(config_path)
    used_families: set[str] = set()
    assignments: list[RosterAssignment] = []
    degraded = False

    for role in roles:
        chain = chains[role]
        chosen: tuple[str, str] | None = None

        for model in chain:
            family = classify_model_family(model)
            if is_family_reachable(family) and family not in used_families:
                chosen = (model, family)
                break

        if chosen is None:
            for model in chain:
                family = classify_model_family(model)
                if is_family_reachable(family):
                    chosen = (model, family)
                    degraded = True
                    break

        if chosen is None:
            raise RosterResolutionError(
                f"no reachable family for role {role!r}: every family in its "
                f"fallback chain {list(chain)!r} reported unreachable"
            )

        assignments.append(RosterAssignment(role=role, model=chosen[0], family=chosen[1]))
        used_families.add(chosen[1])

    return RosterResolution(
        topology=topology,
        assignments=tuple(assignments),
        degraded_independence=degraded,
    )


# Spec 0003 (CriticalDialogue) ticket 07: the literal substring
# `_render_consultation_transcript` writes into a degraded-independence
# transcript, and what a test greps for. Named and exported so a caller
# never needs to hand-copy the marker text to check for it — see the
# acceptance criterion that a test asserting on transcript content must
# find this, not just the structured record.

# Spec 0003 (CriticalDialogue) ticket 04: named once and reused by both
# `run_advisory_consultation_debate` and the crash-recovery path inside
# `dispatch_post_mortem_consultation`'s thread target, rather than left as
# two independently-typed literals. Before ticket 04 there was only ever one
# writer of these two paths, so one inline literal each was fine; a second
# writer (the crash-recovery path, which must reach the exact same files a
# normal run would have, "exactly as if it had blocked") makes the
# duplication a real drift risk instead of a hypothetical one — a future
# edit to one inline literal and not the other would make the crash path
# write to a location nothing ever reads.
_TRANSCRIPT_RELATIVE_PATH = Path(".scratch") / "planning_debate.md"
_TELEMETRY_RELATIVE_PATH = Path(".ralph") / "routing_telemetry.jsonl"


# Spec 0003 (CriticalDialogue) ticket 08: seeded-flaw canaries. On a
# schedule (about one dialogue in twenty, or weekly, whichever comes first —
# config, never a hardcoded literal, per the ticket), a Critic invocation is
# given a plan from this fixture library instead of a real mission
# artifact, to measure whether the Critic still catches a known, documented
# defect rather than rubber-stamping it. See `run_advisory_consultation_debate`'s
# `is_canary`/`canary_fixture` parameters for how a canary round actually
# runs, and `is_canary_dialogue` below for the pure cadence predicate that
# decides *whether* one should — two separate concerns, deliberately: this
# module still contacts no scheduler and holds no session state (see the
# module docstring's "no model or network dependency" promise for
# `resolve_roster`'s identical `reachability_check` seam, which this mirrors).

# Named exactly like this module's other reused Literal unions
# (`AdvisoryOutcome`, `CriticVerdict`, `Occasion`, `RosterTopology`,
# `RosterRole`), all factored into a top-level alias rather than spelled out
# inline at each use site — `AdvisoryDebateResult.canary_result`,
# `AdvisoryTelemetryRecord.canary_result`, the `_result` closure's
# `canary_result` parameter, and `canary_verdict_result`'s own annotation
# all reference this one alias instead of repeating the literal.
CanaryResult = Literal["miss", "catch"]


@dataclass(frozen=True)
class CanaryFixture:
    """One documented, seeded-flaw plan the canary mechanism shows a Critic
    instead of a real mission artifact.

    `id` is a short, stable slug — never derived from `plan_text` — so a
    transcript or telemetry record can name which fixture produced a given
    canary result without re-embedding the whole fixture text every time.
    `flaw_summary` documents, in prose, the specific defect `plan_text`
    seeds: this is what makes a fixture a *canary* and not just an
    arbitrary plan — a genuinely engaged Critic reading `plan_text` on its
    merits should find and object to exactly the defect `flaw_summary`
    names. `plan_text` is the artifact itself, shown to the Critic verbatim
    in place of a Planner-generated plan, and is also what
    `_parse_critic_verdict` verifies quotes against for that round — a
    fixture plan is reviewed no differently than a real one; only its
    origin (this library, not a Planner invocation) differs.
    """

    id: str
    flaw_summary: str
    plan_text: str


# The library ships two documented fixtures rather than one, so it reads as
# a genuine library (per the ticket's own wording) rather than a single
# hardcoded canary — but the acceptance criterion only requires "at least
# one", and nothing here builds a rotation or selection scheme across them:
# absent an explicit `canary_fixture` argument, `run_advisory_consultation_debate`
# always uses `CANARY_FIXTURES[0]` (see that function's docstring). Adding a
# selection strategy across multiple fixtures (round-robin, random, keyed by
# dialogue count) is left for whichever future ticket actually needs
# fixture variety to be observable in production; today's tests exercise
# the mechanism against a known, fixed fixture plus the seam
# (`canary_fixture`) that lets a caller pick any other library entry, or an
# entirely custom one, explicitly.
CANARY_FIXTURES: tuple[CanaryFixture, ...] = (
    CanaryFixture(
        id="unlocked-telemetry-race",
        flaw_summary=(
            "Race condition: concurrent writers can corrupt the telemetry "
            "file because the plan appends to it without any locking, even "
            "though this very module's own `_append_jsonl_locked` "
            "demonstrates that an advisory file lock is required for "
            "exactly this kind of concurrent JSONL append."
        ),
        plan_text=(
            "Implementation plan: add a `record_canary_event(root_dir, "
            "event)` helper that appends one JSON line per canary run to "
            "`.ralph/routing_telemetry.jsonl`.\n\n"
            "1. Open the telemetry file in append mode ('a').\n"
            "2. Serialize `event` to a single JSON line with `json.dumps`.\n"
            "3. Write the line plus a trailing newline, then close the "
            "file.\n"
            "4. Since each write is a single `write()` call and Python "
            "file objects are already thread-safe, no additional "
            "synchronization is needed even if multiple dialogues call "
            "this helper concurrently from different threads or "
            "processes.\n"
            "5. Return the event's resolved task_id to the caller."
        ),
    ),
    CanaryFixture(
        id="reset-token-missing-expiry-check",
        flaw_summary=(
            "Missing validation / security hole: the plan verifies a "
            "password-reset token's signature but never checks its expiry "
            "or single-use status, so a token leaked from an old log or "
            "email months ago remains valid forever."
        ),
        plan_text=(
            "Implementation plan: add a `POST /password-reset/confirm` "
            "endpoint that accepts a reset token and a new password.\n\n"
            "1. Parse the token from the request body.\n"
            "2. Verify the token's HMAC signature against the server's "
            "signing secret; reject with 400 if the signature does not "
            "match.\n"
            "3. Look up the user id encoded in the token's payload.\n"
            "4. Hash the new password with the existing bcrypt helper and "
            "update the user's stored password hash.\n"
            "5. Return 200 with a confirmation message.\n\n"
            "The token's signature check is sufficient to trust it: a "
            "signed token cannot have been forged, so no expiry or "
            "single-use check is required before accepting it."
        ),
    ),
)

# Fallbacks for a config file missing the `canary_cadence` section (or one
# of its two keys) — same role as `DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD`
# and `DEFAULT_ROSTER_FALLBACK_CHAINS` above, never what production
# actually uses, since `routing-config.json` supplies its own section as of
# this ticket. Sourced from `routing_config`'s typed default (ticket 42,
# itself one week in seconds) rather than duplicated as a hand-maintained
# literal.
DEFAULT_CANARY_DIALOGUES_PER_CANARY = routing_config.DEFAULT_ROUTING_CONFIG.canary_cadence.dialogues_per_canary
DEFAULT_CANARY_SECONDS_BETWEEN_CANARIES = (
    routing_config.DEFAULT_ROUTING_CONFIG.canary_cadence.seconds_between_canaries
)


def _load_canary_cadence_config(config_path: Path) -> tuple[int, float]:
    """Read the canary cadence's two settings from `config_path`'s
    `canary_cadence` section (via `routing_config`, ticket 42), falling
    back to `routing_config.DEFAULT_ROUTING_CONFIG.canary_cadence`'s
    matching field for whichever key (or the whole section) is absent — this
    module's own `DEFAULT_CANARY_*` constants are merely aliases of those
    same fields, not a second source the fallback reads from. Same pattern,
    including the no-try/except contract, as `_load_code_review_risk_config`
    and `_load_roster_fallback_chains` above: production always calls this
    with the default `_CONFIG_PATH`, which is checked into the repo, so a
    missing/malformed `config_path` is a genuine caller mistake left to
    raise loudly rather than be swallowed.
    """
    cadence = routing_config.load_routing_config(config_path).canary_cadence
    return cadence.dialogues_per_canary, float(cadence.seconds_between_canaries)


def is_canary_dialogue(
    dialogues_since_last_canary: int,
    seconds_since_last_canary: float,
    *,
    config_path: Path = _CONFIG_PATH,
) -> bool:
    """Pure predicate: given how long it has been since the last canary —
    in dialogue count and in wall-clock time — should the next dialogue be
    a seeded-flaw canary instead of a real mission dialogue?

    This module is stateless per call (see the module docstring's "no
    model or network dependency" framing): nothing here remembers how many
    dialogues have run, or when the last canary fired. That bookkeeping is
    deliberately left to the caller — the same "injected counter/clock
    pair" pattern `reachability_check` already established for roster
    resolution (spec 0003 ticket 07) — because a scheduler that tracks
    session-wide dialogue counts and timestamps belongs to whatever
    orchestrates a session, not to a module that promises full offline,
    stateless exercisability. This function's only job is the pure "should
    it fire" decision; a caller consults it with its own tracked counter
    and clock before deciding whether to pass `is_canary=True` into
    `run_advisory_consultation_debate`.

    Fires (returns `True`) when EITHER condition below is met — "whichever
    comes first", the spec's own phrase:

    - `dialogues_since_last_canary >= dialogues_per_canary` (default 20,
      config `canary_cadence.dialogues_per_canary`). `>=`, not `>`: a
      caller passing exactly the configured count (the 20th dialogue since
      the last canary) fires on that dialogue, not the 21st.
    - `seconds_since_last_canary >= seconds_between_canaries` (default one
      week in seconds, config `canary_cadence.seconds_between_canaries`).
      Same inclusive boundary, for the identical reason.

    Both settings are read fresh from `config_path` on every call rather
    than cached at import time — mirrors `needs_code_review_consultation`'s
    identical contract for its own config reads — so a caller (or a test)
    pointing this at a different file always observes that file's current
    values, which is the only way to prove the cadence is genuinely
    config-driven rather than a Python-side literal that happens to match
    the spec's numbers today.
    """
    dialogues_per_canary, seconds_between_canaries = _load_canary_cadence_config(
        config_path
    )
    return (
        dialogues_since_last_canary >= dialogues_per_canary
        or seconds_since_last_canary >= seconds_between_canaries
    )


# Spec 0003 (CriticalDialogue) ticket 08: the literal substring
# `_render_consultation_transcript` writes into a canary transcript, and
# what a test greps for — same role `DEGRADED_INDEPENDENCE_MARKER` plays
# for ticket 07's marker, and named/exported for the identical reason: a
# caller never needs to hand-copy the marker text to check for it.


@dataclass(frozen=True)
class AdvisoryDebateRound:
    """One Planner/Critic exchange: the proposal offered and the verdict(s) it drew.

    `critic_response` is unrenamed and means exactly what it always meant:
    the sole Critic's response in pair mode, and — spec 0003 ticket 05 —
    Critic A's response in panel mode. Every pre-existing pair-mode
    construction and read site, in this module and in tests, keeps meaning
    exactly what it meant before panel mode existed.

    `critic_b_response` is new (ticket 05) and appended last with a `None`
    default so this stays additive, not a reshape: `None` on every pair-mode
    round, populated only for a panel-mode round, where a second,
    independently invoked Critic reviews the same Planner proposal that
    Critic A saw. A caller distinguishes a pair round from a panel round by
    `critic_b_response is None` — exactly what `_render_consultation_transcript`
    does below. Ticket 06 owns giving a *stalemate report* three properly
    labeled voices; this dataclass only needs to carry the second response
    at all, which is as far as this ticket's scope goes.
    """

    planner_proposal: str
    critic_response: str
    critic_b_response: str | None = None


@dataclass(frozen=True)
class AdvisoryDebateResult:
    """`occasion` records which of the four `Occasion` values this
    consultation ran under (spec 0003 ticket 01). Defaulted to "ambiguity",
    spec 0001's sole occasion, so every pre-existing construction of this
    dataclass — in this module and in tests — that never mentions occasion
    keeps meaning exactly what it meant before this field existed.

    `occasion` is appended after `error`, last in field order, rather than
    inserted next to `outcome` where it reads more naturally: per
    institutional memory's 2026-08-06 backward-compatibility gotcha, a new
    dataclass field must either be keyword-only or preserve the original
    field order, because every field here is a plain (not keyword-only)
    dataclass attribute and a positional construction beyond `outcome` — none
    exists in this repo today, but nothing forbids one tomorrow — would
    otherwise silently bind into the wrong field instead of failing loudly.
    Appending preserves the original order exactly; only a genuinely new
    positional argument could ever reach `occasion`.

    `degraded_independence` (spec 0003 ticket 07) is appended after
    `occasion` for the identical reason and by the identical rule: every
    pre-ticket-07 construction of this dataclass — in this module and in
    tests — that never mentions it keeps meaning exactly what it meant
    before this field existed, defaulting to `False`. True in exactly two
    cases, both meaning "two or more roles in this consultation shared a
    model family": `run_advisory_consultation_debate` resolved its roster
    via an injected `reachability_check` (see that function's docstring)
    and `resolve_roster` reported `RosterResolution.degraded_independence`;
    or the budget ladder's rung 2 (spec 0003 ticket 09, story 14)
    substituted its single cheap model into every role, which collapses
    the roster to one family by construction — see that substitution
    site's own comment. A run that triggers neither always carries `False`
    here, never silently `True`: this field is deliberately not inferred
    from `planner_model == critic_model` after the fact, because two roles
    sharing a literal model name by caller coincidence (an explicit
    `critic_model="Test Critic"` in a test, say) is not the same claim as
    "this dialogue's roster was forced into family reuse."

    `canary_result` (spec 0003 ticket 08) is appended after
    `degraded_independence` for the identical append-only reason and by the
    identical rule: every pre-ticket-08 construction of this dataclass — in
    this module and in tests — that never mentions it keeps meaning exactly
    what it meant before this field existed, defaulting to `None`.
    Populated only when `outcome == "canary"`: `"miss"` when the Critic
    approved the seeded-flaw fixture (it should not have), `"catch"` when
    it did not approve — objected, or produced an unparseable verdict; any
    non-approval is a catch, per the ticket's own instruction ("objecting
    (or any not-approved outcome) -> catch"), mirroring the
    VerdictContract's own asymmetric treatment of approval versus
    everything else. Every other outcome always carries `None` here, never
    a stale value from an unrelated canary run, because each call to
    `run_advisory_consultation_debate` builds exactly one
    `AdvisoryDebateResult` from scratch.

    `degradation_rung` (spec 0003 ticket 09) is appended after
    `canary_result`, last, by the identical append-only rule every field
    above it already follows: every pre-ticket-09 construction of this
    dataclass — in this module and in tests — that never mentions it keeps
    meaning exactly what it meant before this field existed, defaulting to
    `0`. Set to whatever `resolve_degradation_rung` decided for this call
    (`0`-`3`), copied verbatim by `_result` from the enclosing function's
    own `degradation_rung` local — see `run_advisory_consultation_debate`'s
    budget-ladder block. `0` on every outcome that never reached the
    budget check at all (`sensitivity_halt`, exactly like
    `degraded_independence` staying `False` for that same outcome) as well
    as on any ordinary, un-degraded call. Nonzero on every other outcome
    when the caller's `session_spend_so_far` placed this call at rung 1 or
    2 (the dialogue still ran, just degraded), and always `3` when
    `outcome == "budget_skipped"` (the dialogue did not run at all).

    `topology` (spec 0003 ticket 10) is appended after `degradation_rung`,
    last, by the identical append-only rule every field above it already
    follows: every pre-ticket-10 construction of this dataclass — in this
    module and in tests — that never mentions it keeps meaning exactly what
    it meant before this field existed, defaulting to `"pair"`, spec 0001's
    sole topology. Set from the same `panel_mode` local
    `run_advisory_consultation_debate` already computes via
    `_is_panel_topology(occasion, complexity)` (ticket 05) — never
    re-derived here, just reported — except on a canary run, where it is
    reassigned to `"pair"` regardless of `panel_mode`, mirroring
    `result_critic_model`'s identical canary reassignment immediately above
    it in that function: a canary always probes exactly one Critic (ticket
    08), so its result must never claim the panel topology it never
    actually ran under. This is resolved unconditionally, before the
    sensitivity gate, the budget check, or any worker is ever contacted, so
    every outcome — including `sensitivity_halt` and `budget_skipped` —
    carries a genuine topology, never a stale or absent one.

    `round_verdicts` (spec 0003 ticket 10) is appended last, by the
    identical rule: defaults to `()`, an empty tuple, for every
    pre-ticket-10 construction. Populated with one `AdvisoryRoundVerdict`
    per entry already appended to `rounds` above — same length, same order,
    appended at the same call site in the round loop, immediately after
    `_parse_critic_verdict` is called for that round, so the two sequences
    can never drift out of sync. A canary's single fixture-probe round
    (ticket 08) gets exactly one entry too, `critic_b=None`, kept parallel
    with the one entry `rounds` already carries for it. Every outcome that
    never appends to `rounds` at all (`sensitivity_halt`, `budget_skipped`,
    or a `worker_error` before any round completed) carries `()` here too,
    for the same reason.
    """

    rounds_run: int
    final_plan: str
    outcome: AdvisoryOutcome
    planner_model: str = "Claude Opus 5 (Thinking)"
    critic_model: str = "Codex 5.6 Sol"
    rounds: tuple[AdvisoryDebateRound, ...] = ()
    stalemate: AdvisoryStalemateReport | None = None
    error: str | None = None
    occasion: Occasion = "ambiguity"
    degraded_independence: bool = False
    canary_result: CanaryResult | None = None
    degradation_rung: DegradationRung = 0
    topology: RosterTopology = "pair"
    round_verdicts: tuple[AdvisoryRoundVerdict, ...] = ()
    executive_report: ExecutiveDialogueReport | None = None
    manifest_path: str | None = None
    security_veto: SecurityVeto | None = None

    @property
    def consensus_reached(self) -> bool:
        """True only when `outcome == "consensus"` — never independently settable.

        Frozen and derived so a result can never be constructed or mutated
        into claiming consensus its outcome does not back.
        """
        return self.outcome == "consensus"


def needs_advisory_consultation(complexity: str, confidence: float = 1.0) -> bool:
    """Determine whether task requires an advisory Planner-Critic debate loop."""
    normalized = complexity.lower().strip()
    if normalized == "ambiguous":
        return True
    return confidence < 0.7


# Spec 0003 (CriticalDialogue) ticket 03: the three sibling trigger
# predicates for the occasions `needs_advisory_consultation` above does not
# cover. Each is deliberately its own function rather than a branch bolted
# onto `needs_advisory_consultation` — that function is spec 0001's single
# predicate for the "ambiguity" occasion alone, and ticket 03's acceptance
# criteria require it to stay completely untouched (see
# `AdvisoryAmbiguityTriggerUnchangedTests` in the test suite for the
# characterization coverage that pins this).
#
# All three are pure predicates over caller-supplied signals: none of them
# invoke a worker, write an artifact, or otherwise reach outside the
# process. Deciding *whether* a dialogue should run is this ticket's job;
# deciding whether that dialogue blocks the caller (ticket 04) and what
# topology it runs under (ticket 05) are later tickets' work, wired at the
# call site, not inside these predicates.


def needs_plan_review_consultation(complexity: str) -> bool:
    """Plan-review occasion trigger: fires for Medium and Complex complexity,
    never for Simple or Trivial (spec 0003's Triggers paragraph: "Plan
    review: complexity >= Medium").

    Normalizes with the same `.lower().strip()` `needs_advisory_consultation`
    uses, because both predicates read the same caller-supplied complexity
    string — that is convergent reuse of a normalization idiom, not a
    dependency between the two functions.
    """
    normalized = complexity.lower().strip()
    return normalized in ("medium", "complex")


def _load_code_review_risk_config(config_path: Path) -> tuple[int, tuple[str, ...]]:
    """Read the code-review occasion's two risk-signal settings from
    `config_path`'s `critical_dialogue` section (via `routing_config`,
    ticket 42), falling back to
    `routing_config.DEFAULT_ROUTING_CONFIG.critical_dialogue`'s matching
    field for whichever key (or the whole section) is absent — this
    module's own `DEFAULT_CODE_REVIEW_DIFF_LINE_THRESHOLD` and
    `DEFAULT_SECURITY_SENSITIVE_PATH_PATTERNS` are merely aliases of those
    same fields, not a second source the fallback reads from. See the
    comment above `_CONFIG_PATH` for why a fallback exists at all and why
    it is never what production actually uses.

    Raises whatever `routing_config.load_routing_config` raises for a
    missing or malformed file: mirrors `routing_check.load_config`'s
    no-try/except contract rather than silently swallowing a bad
    `config_path`, which would hide a real caller mistake (production
    always calls this with the default `_CONFIG_PATH`, which is checked
    into the repo).
    """
    critical_dialogue = routing_config.load_routing_config(config_path).critical_dialogue
    return (
        critical_dialogue.code_review_diff_line_threshold,
        critical_dialogue.security_sensitive_path_patterns,
    )


def needs_code_review_consultation(
    complexity: str,
    *,
    tests_failing: bool = False,
    diff_line_count: int = 0,
    changed_paths: Sequence[str] = (),
    config_path: Path = _CONFIG_PATH,
) -> bool:
    """Code-review occasion trigger (spec 0003's Triggers paragraph: "Code
    review: complexity >= Medium always, plus risk signals at any tier").

    Medium/Complex complexity fires unconditionally, exactly like
    `needs_plan_review_consultation` — checked first so a Medium+ task never
    pays for a config read it does not need. Below that, three independent
    risk signals each fire it on their own at any complexity tier,
    including Trivial: failing tests, a diff whose `diff_line_count`
    *exceeds* (strictly greater than, not equal to) the configured
    threshold, or any `changed_paths` entry matching a configured
    security-sensitive pattern (case-insensitive substring match, the same
    style `_detect_sensitivity_marker` above already uses for
    `SENSITIVITY_MARKERS`). The threshold and patterns are read from
    `config_path` via `_load_code_review_risk_config` on every call rather
    than cached at import time, so a caller (or a test) pointing this at a
    different file always observes that file's current values.
    """
    normalized = complexity.lower().strip()
    if normalized in ("medium", "complex"):
        return True
    if tests_failing:
        return True
    threshold, patterns = _load_code_review_risk_config(config_path)
    if diff_line_count > threshold:
        return True
    lowered_patterns = [pattern.lower() for pattern in patterns]
    for changed_path in changed_paths:
        lowered_path = changed_path.lower()
        if any(pattern in lowered_path for pattern in lowered_patterns):
            return True
    return False


def needs_post_mortem_consultation(
    *,
    occasion: Occasion | None = None,
    failed: bool = False,
    consecutive_failures: int = 0,
    stalemate_occurred: bool = False,
) -> bool:
    """Post-mortem occasion trigger (spec 0003's Triggers paragraph:
    "Post-mortem: every failure, escalation (the protocol's 2-failure
    rule), and stalemate").

    `occasion` identifies which occasion's outcome is being evaluated for a
    possible post-mortem — the occasion of the task or dialogue that
    failed, escalated, or stalemated. When `occasion == "post-mortem"`,
    this function always returns `False`, regardless of the other three
    arguments: ticket 03's acceptance criteria state the recursion-guard
    rule generally ("a post-mortem's own outcome must NOT recursively
    trigger another post-mortem") before giving a stalemate as the concrete
    example ("if a post-mortem dialogue itself stalemates, that must not
    spawn a further post-mortem"), so the guard here covers every signal a
    post-mortem's own outcome could carry, not only `stalemate_occurred`.
    Every other occasion (or `None`, when the caller has no occasion to
    report — e.g. a plain task-execution failure that never reached a
    dialogue at all) is eligible for all three triggers below.

    `consecutive_failures >= ESCALATION_FAILURE_THRESHOLD` tracks the same
    protocol rule `agent_council.escalate_routing_effort` already encodes as
    `attempts >= ESCALATION_FAILURE_THRESHOLD` (its own docstring: "Escalate
    reasoning effort and model tier after 2+ failed worker attempts")
    rather than inventing a new threshold or a new piece of session state.
    The two constants are separately defined — see the comment above
    `ESCALATION_FAILURE_THRESHOLD` for why this module cannot simply import
    `agent_council`'s — and kept in sync by a test rather than a shared
    import.
    """
    if occasion == "post-mortem":
        return False
    if failed:
        return True
    if consecutive_failures >= ESCALATION_FAILURE_THRESHOLD:
        return True
    # The last trigger returns its own flag rather than adding a fourth
    # `if ...: return True` rung: `stalemate_occurred` is already a bool, so
    # the rung and the fallthrough would say the same thing twice.
    return stalemate_occurred


def _resolve_scoped_memory(
    task_description: str,
    root_dir: Path | None = None,
    target_files: Sequence[str] | None = None,
    scoped_memory: str | None = None,
) -> str:
    """Resolve the scoped-memory block shared by `_build_planner_prompt` and
    `_build_critic_prompt`.

    An explicit `scoped_memory` always wins — a caller supplying one has
    already decided what the Planner/Critic should see. Otherwise this
    scopes against `root_dir`'s adopted `memory` document when a
    `root_dir` is given, or `extract_scoped_memory`'s built-in
    `GOLDEN_RULES` catalog when not — both scored against `target_files` so
    a caller naming the files a task actually touches gets file-pattern
    matches folded into the ranking, not just keyword overlap with
    `task_description`.
    """
    if scoped_memory is not None:
        return scoped_memory
    if root_dir is not None:
        return get_scoped_memory(root_dir, task_description, target_files=target_files)
    return extract_scoped_memory(task_description, target_files=target_files)


def _build_planner_prompt(
    task_description: str,
    *,
    occasion: Occasion = "ambiguity",
    previous_plan: str | None = None,
    critic_feedback: str | None = None,
    scoped_memory: str | None = None,
    root_dir: Path | None = None,
    target_files: Sequence[str] | None = None,
) -> str:
    scoped_memory = _resolve_scoped_memory(task_description, root_dir, target_files, scoped_memory)
    return build_planner_prompt(
        task_description,
        occasion=occasion,
        previous_plan=previous_plan,
        critic_feedback=critic_feedback,
        scoped_memory=scoped_memory,
    )


def _build_critic_prompt(
    task_description: str,
    planner_plan: str,
    *,
    occasion: Occasion = "ambiguity",
    scoped_memory: str | None = None,
    root_dir: Path | None = None,
    target_files: Sequence[str] | None = None,
) -> str:
    # spec 0003 ticket 02's VerdictContract, reversed from spec 0001: the
    # verdict line now comes LAST, after rationale and engagement units, not
    # first. See the textual-contract comment above `_QUOTE_LINE_PATTERN`
    # for the exact QUOTE:/N. line shapes `_parse_critic_verdict` reads —
    # this prompt and that parser must keep asking for and reading the same
    # shape, since later tickets (panel topology, canaries, telemetry) build
    # on this one.
    #
    # The prompt still asks for an exact verdict line: the tolerance added in
    # `_is_tolerant_revise` is a parser-side safety net for what real models
    # actually emit, not a relaxation of the contract we ask for. Asking for
    # exactness and parsing with tolerance are not in tension — the ask stays
    # strict so most responses need no tolerance at all.
    #
    # The engagement-unit and verdict-line instructions, and the closing
    # "Planner's plan:" label, stay fixed across every occasion, unlike the
    # intro above: they are the VerdictContract's territory, and ticket 02
    # needs one shared shape to extend across all four occasions rather than
    # four independently-drifting copies.
    #
    # "at least one verified quote" (not "quotes or objections"): only a
    # quote is mechanically checked against the plan text, so only a quote
    # is proof the Critic engaged with something real. Objections are
    # encouraged and still counted, but — per `_parse_critic_verdict` — they
    # can never substitute for a quote in the approval decision, so the
    # prompt must not imply they can.
    scoped_memory = _resolve_scoped_memory(task_description, root_dir, target_files, scoped_memory)
    return build_critic_prompt(
        task_description,
        planner_plan,
        occasion=occasion,
        approve_verdict=CRITIC_VERDICT_APPROVE,
        revise_verdict=CRITIC_VERDICT_REVISE,
        scoped_memory=scoped_memory,
    )


def _detect_sensitivity_marker(text: str) -> str | None:
    """Return the first `SENSITIVITY_MARKERS` entry found in `text`, or None.

    Returns the marker constant itself, never the surrounding text it
    matched against — the caller reports this back as the reason a
    consultation halted, so the marker name alone must be able to explain
    the halt without ever repeating the task text or the secret value that
    tripped it.
    """
    return scan_sensitivity_markers(text, SENSITIVITY_MARKERS)


def _remove_stale_plan_artifact(plan_path: Path) -> str | None:
    """Ensure no plan artifact survives a non-consensus exit.

    Only this one path is touched — the module owns nothing else under
    `root_dir`. Cleanup failure (e.g. `plan_path` is a directory, or its
    parent is unwritable) must not raise out of the consultation and must
    not replace whatever error actually caused the non-consensus exit — it
    is reported back to the caller instead, who folds it into the result.

    The marker-only redaction boundary covers task text: nothing derived
    from `task_description` reaches a reason beyond the matched marker
    constant. A cleanup suffix may instead name `plan_path`, which comes
    from caller-injected `root_dir` (the repository root in production),
    never from the task. Suppressing that suffix was deliberately rejected:
    its OSError text tells the operator why a stale plan survived the halt,
    and already contains the path, so redaction would discard the diagnostic
    rather than make it shorter.
    """
    try:
        plan_path.unlink(missing_ok=True)
    except OSError as exc:
        return f"failed to remove stale plan artifact at {plan_path}: {exc}"
    return None


def _fold_error(existing: str | None, addition: str | None) -> str | None:
    """Combine a possibly-absent primary error with a possibly-absent addition.

    Used at every point in this module where a secondary failure can occur
    alongside a primary one: the `_result` choke point, where the primary
    error is itself optional depending on which exit path is folding a
    transcript- or telemetry-write failure into it, and the outcome branches
    above it, where the primary error (a halt reason or a caught worker
    exception) is always present but a cleanup failure alongside it may or
    may not be. Same non-negotiable rule everywhere it's called, and the one
    `_remove_stale_plan_artifact` documents too: a secondary I/O failure is
    reported, never allowed to mask or replace the primary outcome.
    """
    if addition is None:
        return existing
    if existing is None:
        return addition
    return f"{existing}; {addition}"




combine_panel_critic_feedback = _prompt_assembler.combine_panel_critic_feedback
_combine_panel_critic_feedback = _prompt_assembler.combine_panel_critic_feedback


def run_advisory_consultation_debate(
    task_description: str,
    invoke_worker: InvokeWorker | None = None,
    *,
    root_dir: Path,
    occasion: Occasion = "ambiguity",
    complexity: str = "medium",
    max_rounds: int = MAX_DEBATE_ROUNDS,
    target_files: Sequence[str] | None = None,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
    critic_a_model: str = "Codex 5.6 Sol",
    critic_a_effort: str = "high",
    critic_b_model: str = "Gemini 3.6 Flash",
    critic_b_effort: str = "high",
    task_id: str | None = None,
    reachability_check: IsFamilyReachable | None = None,
    roster_config_path: Path = _CONFIG_PATH,
    is_canary: bool = False,
    canary_fixture: CanaryFixture | None = None,
    session_spend_so_far: int = 0,
    budget_config_path: Path = _CONFIG_PATH,
    consultation_policy: dict[str, Any] | None = None,
) -> AdvisoryDebateResult:
    """Run the Planner/Critic exchange, revising on objection, and report the outcome.

    Round 1: the Planner proposes a plan from the task description alone,
    and the Critic judges it. If the Critic approves, the agreed plan is
    written to ``root_dir / "implementation_plan.md"`` and consensus is
    reported for that round. A failure writing that file is folded into
    the result's ``error`` field rather than raised: the Critic genuinely
    approved, so the outcome stays ``consensus`` and ``final_plan`` still
    carries the agreed text even though it never reached disk — a caller
    that trusts ``final_plan`` without also checking ``error`` is the one
    place in this module where that trust is not yet backed by the file
    system. Otherwise the Planner is asked again, this time holding its
    previous plan and the Critic's objection, and the exchange repeats up
    to ``max_rounds`` times.

    Four ways this can end without consensus, and each fails closed the
    same way — no plan artifact, no winner picked, the failure visible on
    the result:

    - Sensitivity halt: the task text itself carries a secret, credential,
      or personal-data marker. Checked before any worker is contacted — no
      Planner proposal, no Critic verdict, nothing sensitive ever leaves
      this process. The result names which marker tripped the halt (never
      the surrounding text) and states that human approval is required.
      Spec 0003 (CriticalDialogue) ticket 11: a marker match no longer
      halts immediately when ``reachability_check`` is supplied — instead
      the roster resolved for this call (see ``reachability_check`` below)
      is constrained to local families only, and the halt is deferred to
      whether that constrained resolution actually finds one. Omitting
      ``reachability_check`` (the default) still halts right here, exactly
      as spec 0001 always did — this module has no other way to establish
      that a local runtime exists.
    - Stalemate: every round runs and none is approved. The result carries
      both final positions and three resolution options.
    - Unparseable verdict: a Critic response has no readable verdict line.
      This ends the consultation immediately rather than being silently fed
      back to the Planner as if it were a reasoned rejection.
    - Worker error: ``invoke_worker`` raises. The exception is caught (never
      ``BaseException``, so Ctrl-C still propagates) and its message is
      carried on the result.

    Two further endings are deliberately not listed among those four,
    because each is an orthogonal case, not another flavor of "no
    consensus" (see ``AdvisoryOutcome``'s own comment for the taxonomy):

    - Canary: ``is_canary=True`` runs a seeded-flaw measurement probe of
      the Critic — there is no Planner proposal to agree or disagree
      about, so the outcome is always ``"canary"``. Documented in full at
      the ``is_canary`` paragraph below.
    - Budget skipped: the session's dialogue budget is fully exhausted
      (rung 3), so no Planner or Critic is contacted at all and the
      outcome is ``"budget_skipped"``. Documented in full at the
      ``session_spend_so_far`` paragraph below.

    A pre-existing ``implementation_plan.md`` under ``root_dir`` from an
    earlier run is removed on a ``budget_skipped`` exit and on every one
    of the four no-consensus exits above, with two exceptions — both
    canary-flavored, because a canary must neither create nor delete
    that artifact (see ``is_canary`` below): a ``budget_skipped`` exit
    that preempted an ``is_canary`` call removes nothing, and a
    ``worker_error`` that arises inside a canary run removes nothing
    either. Everywhere else, the artifact on disk is never staler than
    the result describing it.

    Every one of the seven outcomes — including consensus — writes a fresh,
    human-readable transcript to ``root_dir / ".scratch" / "planning_debate.md"``
    (never appended, so a stale transcript can't survive) and emits exactly
    one structured telemetry record to
    ``root_dir / ".ralph" / "routing_telemetry.jsonl"``. Every outcome except
    ``sensitivity_halt`` also appends one
    ``learning_journal.DialogueQualityRecord`` — spec 0004 ticket 24's
    dialogue-quality journal, occasion/topology/per-round verdicts and
    engagement counts/canary results/degradation and independence flags,
    correlated to this run by the same ``task_id`` the telemetry record
    carries. A halt writes none: it ran no round, so the record would
    describe a dialogue that never happened. On a
    ``sensitivity_halt`` the transcript carries only the matched marker
    constant, never the task text; every other outcome's transcript carries
    the full task description, plus each round's Planner/Critic exchange
    for every round that ran. A ``budget_skipped`` run has no rounds, so
    its transcript instead carries an explanatory note that no Planner or
    Critic was contacted; a ``canary`` run's single transcript round shows
    the fixture's flawed plan text under a fixture-labeled header rather
    than a Planner exchange — no Planner was invoked — alongside a
    ``CANARY_MARKER`` note naming the fixture and its seeded flaw (see
    ``_render_consultation_transcript`` for both). The telemetry record
    never carries task text or a matched secret value on any path — its
    complete field set is ``timestamp``, ``task_id``, ``rounds_run``,
    ``outcome``, ``planner_model``, ``critic_model``, ``kind``,
    ``degraded_independence``, ``canary_result``, ``degradation_rung``,
    ``occasion``, ``topology``, and ``round_verdicts`` (verdict labels and
    engagement-unit counts only, never plan or critique prose) — see
    ``AdvisoryTelemetryRecord`` for each field's own contract.
    ``task_id`` is the ``task_id`` keyword argument when supplied; otherwise
    it defaults to a truncated SHA-256 digest of ``task_description`` for
    every outcome except ``sensitivity_halt`` and ``canary``, and to a
    random identity, unrelated to the task text, for those two — for two
    distinct reasons: a digest is itself a confirmation oracle over
    guessable task text, and a canary keeps the real task text, so its
    digest would collide with the real mission's own ``task_id`` (see
    ``_resolve_task_id``).
    On a halt the same resolved id appears on both the transcript and the
    telemetry record, so the two stay correlated for an auditor even though
    neither carries the task text. A failure writing either artifact is
    folded into the result's ``error`` field rather than raised or allowed
    to replace the primary outcome. The same is true of the learning
    journal: if it cannot be wired to this run's worker calls — a caller
    ``task_id`` the journal's own validation rejects, say — the consultation
    runs un-instrumented and says so in ``error``, rather than failing as
    ``worker_error`` before a worker is contacted.

    ``occasion`` (spec 0003 ticket 01) selects which mission the Planner and
    Critic prompts carry — see ``_MISSION_COPY``. It defaults to
    ``"ambiguity"``, spec 0001's sole occasion, so a call site that never
    mentions it behaves exactly as before this parameter existed: same
    prompts, same outcomes, same call counts. It is recorded on the returned
    ``AdvisoryDebateResult`` so a caller can observe which occasion ran.
    Wiring the other three occasions' real trigger predicates and blocking
    stance is later tickets' work, not this function's.

    ``complexity`` (spec 0003 ticket 05) selects, together with ``occasion``,
    which topology this round loop runs: the pair topology spec 0001 shipped
    (one Planner, one Critic — ``critic_model``/``critic_effort``), or the
    panel topology (one Planner, two independently invoked Critics —
    ``critic_a_model``/``critic_a_effort`` and ``critic_b_model``/
    ``critic_b_effort``) ticket 05 adds. The panel topology is selected only
    when ``occasion`` is ``"plan-review"`` or ``"code-review"`` *and*
    ``complexity`` normalizes to ``"complex"`` (see ``_is_panel_topology``);
    every other combination — including ``"complex"`` ambiguity or
    post-mortem — keeps the pair topology completely unchanged, and a call
    site that never mentions ``complexity`` defaults to ``"medium"``, which
    never selects a panel, so every pre-ticket-05 call site behaves exactly
    as before this parameter existed.

    In a panel round both Critics review the exact same Planner proposal
    (the same ``critic_prompt`` text ``_build_critic_prompt`` already builds
    for pair mode, addressed to each Critic's own ``model``/``effort`` at
    the ``invoke_worker`` call site — the prompt text itself does not differ
    between Critic A and Critic B), and each response is parsed through the
    same ``_parse_critic_verdict`` (ticket 02) against that proposal.
    Consensus requires *both* Critics' verdict to be ``"approved"`` in the
    same round; an unparseable verdict from either Critic ends the
    consultation immediately, exactly as a single unparseable verdict does
    in pair mode; any other combination (a split verdict, or both Critics
    objecting) folds both responses into one feedback string
    (``_combine_panel_critic_feedback``) for the Planner's next revision
    prompt and starts another round, up to the same ``max_rounds`` cap pair
    mode obeys — panel mode adds no rounds of its own. A panel that never
    reaches consensus by the cap produces a stalemate report built by the
    same ``_build_stalemate_report`` pair mode uses, called with both
    Critics' own last responses kept separate (spec 0003 ticket 06): the
    report's ``critic_position`` carries Critic A's final position and its
    ``critic_b_position`` carries Critic B's, never a folded combination of
    the two — see ``AdvisoryStalemateReport``'s docstring.

    ``reachability_check`` (spec 0003 ticket 07) is the opt-in seam for
    family-aware roster resolution: ``None`` by default, which leaves every
    pre-ticket-07 call site's behaviour completely unchanged — the explicit
    ``planner_model``/``critic_model``/``critic_a_model``/``critic_b_model``
    arguments (or their existing string defaults) are used exactly as they
    always were, and ``degraded_independence`` on the result stays ``False``
    unless the budget ladder's rung 2 collapses the roster on its own — see
    ``session_spend_so_far`` below.
    Supplying a callable opts in: it is passed to ``resolve_roster`` (with
    the topology this call already selected via ``occasion``/``complexity``)
    as the injected ``is_family_reachable`` check, and the roster it returns
    *replaces* ``planner_model``/``critic_a_model``/``critic_model`` (kept
    equal to the resolved ``critic_a_model`` in both topologies, matching
    ``result_critic_model``'s existing pair/panel convention above) and, in
    panel mode, ``critic_b_model`` — whatever those parameters were
    explicitly passed as is then ignored for this call. This is a
    deliberate all-or-nothing seam, not a per-parameter merge: mixing "some
    roles explicit, some resolved" would need a way to tell "caller passed
    this explicitly" apart from "caller left this at its default string",
    which plain string parameters cannot express. A caller wanting some
    roles pinned and others resolved should call ``resolve_roster`` itself
    and pass its own choice of explicit model arguments — that public
    function is exactly the alternative integration point for that case,
    already exposed as its own callable rather than folded into this one.
    ``roster_config_path`` threads through to ``resolve_roster`` unchanged,
    defaulting to this module's own ``routing-config.json``, exactly like
    ``needs_code_review_consultation``'s ``config_path`` parameter. A
    ``RosterResolutionError`` (no reachable family at all for some role) is
    caught and reported as a ``worker_error`` outcome, the same fail-closed
    treatment every other pre-flight failure to reach a worker already gets
    in this function — except on a sensitive task, where it is
    ``sensitivity_halt`` instead; see the next paragraph.

    **Sensitive tasks (spec 0003 ticket 11, user story 19).** When
    ``_detect_sensitivity_marker`` matches the task text AND
    ``reachability_check`` was supplied (see the "Sensitivity halt" bullet
    above for the ``reachability_check is None`` case, which halts earlier
    and never reaches this paragraph at all), the callable passed to
    ``resolve_roster`` above is not ``reachability_check`` itself but
    ``lambda family: is_local_family(family) and reachability_check(family)``
    — a cloud family is reported unreachable regardless of what the
    caller's own probe says, so every role's fallback chain walks past its
    cloud candidates to its local entry on its own, with no change to
    ``resolve_roster`` itself. If that leaves some role with no reachable
    family at all, the ``RosterResolutionError`` this function always
    catches is reported as ``sensitivity_halt`` (carrying the matched
    marker, so the redacted transcript renders) rather than the
    ``worker_error`` a non-sensitive task gets for the identical exception —
    this is "the local runtime is unavailable" for a sensitive task, and
    user story 19 requires that to fail closed and escalate to the human,
    exactly like an absent ``reachability_check`` already does. One further
    carve-out lives in the budget ladder below: rung 2 ordinarily
    substitutes a single (cloud) model into every role, but on a sensitive
    task it degrades only ``planner_effort``/``critic_effort``/
    ``critic_a_effort``/``critic_b_effort`` and leaves the already-local
    roster this paragraph resolved completely untouched — see
    ``session_spend_so_far`` below and the rung-2 code block's own comment
    for why.

    ``is_canary`` (spec 0003 ticket 08) is the opt-in seam for a seeded-flaw
    canary round: ``False`` by default, which leaves every pre-ticket-08
    call site's behaviour completely unchanged. Set ``True``, this call
    skips the Planner entirely — there is nothing to plan, since a
    documented fixture (``canary_fixture``, defaulting to
    ``CANARY_FIXTURES[0]`` when not supplied) stands in for what the
    Planner would have produced — and shows only the Critic that fixture's
    ``plan_text``, addressed to ``critic_model``/``critic_effort`` exactly
    as a pair-mode round would be. This is deliberately unconditional
    regardless of whichever topology ``occasion``/``complexity`` would
    otherwise select: a canary always probes exactly one Critic, never
    ``critic_b_model``, even when this same occasion/complexity combination
    would normally run the panel topology. The spec does not say whether
    canaries should apply to panel mode at all, so this is the narrower,
    simpler pair-mode-only scope the ticket allows by default; extending
    the mechanism to probe both panel Critics independently is left to a
    future ticket if that ever proves necessary. The Critic's verdict is
    parsed by the same ``_parse_critic_verdict`` (ticket 02) every other
    round uses, verified against the fixture's own ``plan_text`` exactly as
    a real plan would be: an ``"approved"`` verdict is a canary **miss**
    (the Critic should have objected to the seeded flaw and did not); any
    other verdict — a reasoned objection or an unparseable response alike —
    is a **catch**. The result is reported on ``AdvisoryDebateResult.canary_result``
    (``"miss"`` or ``"catch"``) and the outcome is always ``"canary"``,
    never ``"consensus"`` or ``"stalemate"`` — see that field's and
    ``AdvisoryOutcome``'s own docstrings for why a canary is deliberately
    not folded into either. A canary round never writes
    ``implementation_plan.md`` and never calls ``_remove_stale_plan_artifact``
    either — not even on the rung-3 budget-preemption path, whose
    non-canary exit does remove a stale plan (see the budget paragraph
    below) — so it neither creates nor deletes that file: an
    already-current real plan from an earlier consensus in the same
    ``root_dir`` survives a later canary run completely untouched, which
    is what keeps a canary from ever contaminating a real mission's
    outcome. It still reaches the
    same ``_result`` choke point as every other exit path, so it still
    writes a transcript (carrying ``CANARY_MARKER`` and the fixture's id
    and flaw summary — see ``_render_consultation_transcript``) and exactly
    one telemetry record (carrying ``canary_result``) — the module's
    "every outcome gets both artifacts" invariant holds for canaries too.
    Absent an explicit ``task_id`` argument, that record's task identity is
    never the usual digest of ``task_description`` either: a canary keeps
    the real task text (only the plan is substituted), so a digest default
    would collide with the real mission's own ``task_id`` in any store
    keyed by it — see ``_resolve_task_id`` for the fail-safe this function
    shares with ``sensitivity_halt``.
    The sensitivity gate and (if supplied) roster resolution both still run
    ahead of a canary round exactly as they do for any other call: a
    canary does not bypass the redaction boundary on ``task_description``,
    and if ``reachability_check`` resolves a roster, the canary's sole
    Critic is invoked with the roster-resolved ``critic_model`` (which
    ``resolve_roster`` already sets equal to the resolved ``critic_a_model``
    in both topologies), not a hardcoded default.

    ``session_spend_so_far`` (spec 0003 ticket 09) is the opt-in seam for
    the per-session dialogue budget's degradation ladder: ``0`` by default,
    which leaves every pre-ticket-09 call site's behaviour completely
    unchanged, since ``resolve_degradation_rung(0, ...)`` always reads rung
    0 for any positive configured cap. This module holds no session state
    (same philosophy as ``reachability_check`` and ``is_canary``/
    ``is_canary_dialogue`` above): the caller tracks how many dialogues its
    own session has already run and passes that count in here, fresh, on
    every call — see ``resolve_degradation_rung``'s own docstring for the
    exact unit and thresholds. ``budget_config_path`` threads through to
    ``resolve_degradation_rung`` unchanged, defaulting to this module's own
    ``routing-config.json``, exactly like ``roster_config_path`` above.

    The resolved rung is checked immediately after the sensitivity gate,
    before roster resolution, before the canary branch, and before any
    ``invoke_worker`` call: rung 3 (full exhaustion) returns a
    ``"budget_skipped"`` result right there — no Planner or Critic is ever
    contacted, and this holds even for a call that also set ``is_canary``.
    A pre-existing ``implementation_plan.md`` under ``root_dir`` is removed
    exactly as every other early exit already does — with one carve-out:
    a preempted ``is_canary`` call skips that removal, because the plan
    sitting there belongs to a real mission's earlier result, and a
    canary's own result describes a probe, never the mission (see the
    canary invariant above). Either way the same transcript/telemetry
    choke point (``_result``) still fires, so the
    caller receives a real, inspectable result rather than silence. Rungs 1
    and 2 instead degrade this call in place before it proceeds: rung 1
    lowers the effective round cap to ``_DEGRADED_ROUND_CAP`` (reassigning
    the local ``max_rounds``, so both pair and panel round loops obey it
    automatically) immediately after the rung is resolved. Rung 2 lowers
    both ``planner_effort``/``critic_effort``/``critic_a_effort``/
    ``critic_b_effort`` to ``_DEGRADED_EFFORT`` AND reassigns
    ``planner_model``/``critic_model``/``critic_a_model``/``critic_b_model``
    to the single model ``_load_degraded_roster_model`` reads from
    ``routing-config.json``'s ``light_doer`` role block — a genuine roster
    change, not effort alone, per the ticket's own "fall back toward
    lighter/local families" language. Because that substitution puts one
    model in every seat, it collapses the roster to a single family by
    construction, and a rung-2 result therefore also reports
    ``degraded_independence`` — in the result, the telemetry record, and
    the transcript's ``DEGRADED_INDEPENDENCE_MARKER`` line, the same
    reporting path the roster-resolution case uses (spec 0003 story 14: a
    same-family fallback is never silent, whatever mechanism caused it).
    This override is applied *after* the
    roster-resolution block below, deliberately, so it wins even when
    ``reachability_check`` also resolved a roster for this call: budget
    exhaustion is a stronger, later-stage concern than independence — for a
    non-sensitive task. For a **sensitive** one (spec 0003 ticket 11), the
    model reassignment does not apply at all: ``_load_degraded_roster_model``
    reads a cloud model (``light_doer.name`` resolves to "Codex 5.6 Terra
    ..."), and substituting it into a sensitive dialogue's roster would put
    sensitive task text on a cloud worker purely because a session ran up
    its dialogue budget — privacy beats cost, so a sensitive task's rung 2
    degrades only the four effort locals and leaves its already-local
    roster (see the "Sensitive tasks" paragraph above) standing untouched;
    ``degraded_independence`` is therefore also left exactly as the roster
    resolution block set it, never forced to ``True`` by this rung the way
    it is for a non-sensitive task. The
    two rung-2 effects (effort, model) and rung 1's round reduction all
    compound rather than replace each other, so a rung-2 call also keeps
    rung 1's reduced round cap. The resolved rung is carried on the
    returned ``AdvisoryDebateResult`` as
    ``degradation_rung`` (and on the telemetry record of the same name)
    regardless of whether it ended up ``0``, so a caller — or an auditor
    reading telemetry — can always tell whether *this* dialogue ran
    degraded, never only infer it from the outcome value.

    Raises ``ValueError`` if ``max_rounds`` is not at least 1, or if
    ``occasion`` is not one of the four ``Occasion`` values: both are
    programming errors at the call site, not a genuine Planner-Critic
    disagreement, and must not be reported back as a fabricated stalemate.
    Validation deliberately precedes the sensitivity gate, so a sensitive
    task with an invalid `max_rounds` or `occasion` raises rather than
    returning a `sensitivity_halt` result. Neither ordering contacts a
    worker, so both preserve the security boundary; only the caller-facing
    report differs. The exception is the louder halt because it cannot be
    ignored, whereas reordering would give a genuine call-site bug a
    plausible-looking result instead of the error that says the caller's
    code is wrong.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")
    if occasion not in _MISSION_COPY:
        raise ValueError(
            f"unknown occasion {occasion!r}; expected one of {tuple(_MISSION_COPY)}"
        )

    panel_mode = _is_panel_topology(occasion, complexity)
    effective_consultation_policy = (
        consultation_policy
        if consultation_policy is not None
        else _load_consultation_policy(roster_config_path)
    )
    security_policy = effective_consultation_policy.get("security_veto", {})
    if not isinstance(security_policy, dict):
        security_policy = {}
    veto_handler = SecurityVetoHandler(
        veto_severities=security_policy.get("veto_severities"),
        security_threshold=security_policy.get("security_threshold", 0.80),
        enabled=security_policy.get("enabled", True),
    )
    weighting_policy = effective_consultation_policy.get("weighting", {})
    if not isinstance(weighting_policy, dict):
        weighting_policy = {}
    panel_consensus_table = ConsensusTable(
        policy=effective_consultation_policy.get("consensus_policy"),
        weights=weighting_policy.get("initial_weights"),
        quorum_threshold=weighting_policy.get("quorum_threshold", 0.60),
    )
    council_run_id = f"run-{secrets.token_hex(8)}"
    # The `critic_model` the result/transcript/telemetry actually report.
    # Pair mode reports the `critic_model` parameter exactly as it always
    # has (every pre-ticket-05 assertion on `result.critic_model` stays
    # valid unmodified); panel mode reports Critic A's model instead, since
    # `critic_model` itself is never invoked in that mode and would
    # otherwise report a model this run never contacted. Recording both
    # Critics' models on the result is ticket 10's telemetry-extension job,
    # not this one's — see this ticket's report for why that line is drawn
    # here.
    result_critic_model = critic_a_model if panel_mode else critic_model
    # Spec 0003 (CriticalDialogue) ticket 10: the topology the result and
    # telemetry record actually report — set once, here, from the same
    # `panel_mode` local `result_critic_model` above already reads, so the
    # two stay consistent by construction rather than by convention. The
    # sole exception is the canary block below, which reassigns this to
    # `"pair"` exactly where it reassigns `result_critic_model` to
    # `critic_model`, and for the identical reason: a canary probes exactly
    # one Critic regardless of what `panel_mode` computed, so its reported
    # topology must never claim "panel" for a run that never actually
    # invoked a second Critic. Unlike `result_critic_model`, roster
    # resolution (ticket 07) and the rung-2 budget override (ticket 09)
    # never reassign this: both change *which* model(s) are invoked, never
    # *how many* Critics the round loop addresses, so `panel_mode` — and
    # therefore this — stays correct through both of those blocks
    # unmodified.
    result_topology: RosterTopology = "panel" if panel_mode else "pair"
    # Spec 0003 (CriticalDialogue) ticket 07, and ticket 09's rung 2: set
    # (if at all) only by the roster-resolution block below or by rung 2's
    # single-model substitution just after it — both run after the
    # sensitivity gate and before this closure is ever invoked — see each
    # block's own comment for why. Declared here, before `_result` is
    # defined, purely so the sensitivity-halt branch (which calls `_result`
    # before either block ever runs) reads a real `False` rather than
    # raising `UnboundLocalError`: a halted task never reaches roster
    # resolution or the budget ladder at all, and correctly reports no
    # degradation for a dialogue that never ran.
    roster_degraded_independence = False
    # Spec 0003 (CriticalDialogue) ticket 09: set (if at all — to a nonzero
    # rung) by the budget-ladder check below, which runs after the
    # sensitivity gate and before this closure is ever invoked, for the
    # identical reason `roster_degraded_independence` above is declared
    # here rather than inline: the sensitivity-halt branch calls `_result`
    # before the budget ladder ever runs, and must read a real `0` rather
    # than raising `UnboundLocalError` — a halted task never reaches the
    # budget check at all, and correctly reports no degradation for a
    # dialogue that never ran.
    degradation_rung: DegradationRung = 0
    # `_result` must never perform configuration I/O. Early outcomes use the
    # safe policy default; normal dialogue paths replace it once below.
    budget_cap = DEFAULT_SESSION_DIALOGUE_CAP

    rounds: list[AdvisoryDebateRound] = []
    # Spec 0003 (CriticalDialogue) ticket 10: kept parallel with `rounds`
    # above — one `AdvisoryRoundVerdict` appended at the identical call site
    # every `rounds.append(...)` above already has, immediately after
    # `_parse_critic_verdict` is called for that round, so the two
    # sequences can never drift out of sync (same length, same order, every
    # outcome). See `AdvisoryDebateResult.round_verdicts`'s docstring.
    round_verdicts: list[AdvisoryRoundVerdict] = []
    previous_plan: str | None = None
    previous_critique: str | None = None
    # Panel mode only (spec 0003 ticket 06): each Critic's own last response,
    # tracked separately from `previous_critique` above — which stays the
    # *folded* string `_combine_panel_critic_feedback` builds for the
    # Planner's next revision prompt, unchanged from ticket 05. These two
    # carry the same information but serve different consumers: the folded
    # string is what the Planner reads mid-loop, and these two separated
    # strings are what a panel stalemate report reads at the cap, keeping
    # Critic A's and Critic B's final positions distinct rather than
    # re-parsing them back out of the fold. Both stay `None` in pair mode,
    # where they are never read.
    previous_critic_a_response: str | None = None
    previous_critic_b_response: str | None = None
    plan_path = root_dir / "implementation_plan.md"
    transcript_path = root_dir / _TRANSCRIPT_RELATIVE_PATH
    telemetry_path = root_dir / _TELEMETRY_RELATIVE_PATH

    # Set if the learning journal could not be wired to this run's worker
    # calls (see the `invoke_worker is None` branch below). Folded into
    # whatever outcome this run actually reaches, exactly like a transcript-
    # or telemetry-write failure: a secondary failure is reported, never
    # allowed to become the primary outcome.
    journal_wiring_error: str | None = None

    # Ticket 24: the run identity this consultation's `DialogueQualityRecord`
    # carries. Set only where this function owns the journal wiring below (and
    # only once the factory has accepted it), so it is present exactly when
    # there are `WorkerExecutionRecord`s sharing it to join against. A caller
    # that supplied its own journaled `invoke_worker` owns a run identity this
    # function cannot see; minting a second one here would read as an extra run
    # of the same task and inflate the rework count `_countable_runs` derives.
    dialogue_run_id: str | None = None

    def _result(
        outcome: AdvisoryOutcome,
        *,
        final_plan: str = "",
        stalemate: AdvisoryStalemateReport | None = None,
        error: str | None = None,
        sensitivity_marker: str | None = None,
        canary_result: CanaryResult | None = None,
        resolved_canary_fixture: CanaryFixture | None = None,
        manifest_path: str | None = None,
        security_veto: SecurityVeto | None = None,
    ) -> AdvisoryDebateResult:
        """The single choke point every return passes through.

        Writing the transcript and telemetry record here — rather than at
        each call site — makes "every exit path gets both artifacts" a
        structural guarantee instead of sixteen remembered writes: one per
        ``return _result(...)`` site, spread across all seven outcomes, and
        a count that only grows as later tickets add exits. Task identity
        is resolved here too, per outcome, rather than once up front: the
        `sensitivity_halt` and `canary` defaults (a random id apiece) must
        never be the same code path as every other outcome's default (a
        digest of the task text) — see `_resolve_task_id` for the two
        distinct reasons the digest is ruled out for each: a confirmation
        oracle over guessable task text for a halt, a `task_id` collision
        with the real mission for a canary. Because this closure runs at most once per
        call to `run_advisory_consultation_debate` (every branch below
        returns immediately through it), resolving per-call here is exactly
        as "once" as resolving up front would have been.
        """
        if (
            result_topology == "panel"
            and outcome in {"worker_error", "unparseable_verdict"}
            and manifest_path is None
        ):
            manifest_path = _write_panel_manifest(
                "WORKER_ERROR" if outcome == "worker_error" else "MALFORMED_VERDICT"
            )

        # Provisional: its `error` is pre-fold (the transcript- and
        # telemetry-write failures below haven't been folded in yet), and it
        # is what the renderers below actually see. The caller instead gets
        # the post-fold `AdvisoryDebateResult` built at the end of this
        # function. This gap is inherent — a write's own error can't be known
        # before the write happens — so a renderer must never read `.error`
        # off this object; neither renderer does today, but nothing enforces
        # that beyond this comment.
        executive_report = ExecutiveDialogueReport(
            _executive_dialogue_report.render_executive_summary(
                outcome,
                occasion,
                len(rounds),
                max_rounds,
                planner_model,
                result_critic_model,
                # A completed dialogue consumes one session unit, regardless
                # of how many revision rounds it needed. A pre-flight or
                # mid-round failure has no completed dialogue to report.
                session_spend=session_spend_so_far + (1 if rounds else 0),
                plan_path=str(plan_path),
                error=error,
            ),
            _executive_dialogue_report.format_budget_degradation_alert(
                degradation_rung, session_spend_so_far, budget_cap
            ),
        )
        if executive_report.budget_alert:
            print(executive_report.budget_alert, file=sys.stderr, end="")
        provisional_result = AdvisoryDebateResult(
            rounds_run=len(rounds),
            final_plan=final_plan,
            outcome=outcome,
            occasion=occasion,
            planner_model=planner_model,
            critic_model=result_critic_model,
            rounds=tuple(rounds),
            stalemate=stalemate,
            error=error,
            degraded_independence=roster_degraded_independence,
            canary_result=canary_result,
            degradation_rung=degradation_rung,
            topology=result_topology,
            round_verdicts=tuple(round_verdicts),
            executive_report=executive_report,
            manifest_path=manifest_path,
            security_veto=security_veto,
        )

        resolved_task_id = _resolve_task_id(task_description, task_id, outcome)

        if outcome == "sensitivity_halt":
            if sensitivity_marker is None:
                raise ValueError(
                    "sensitivity_halt outcome requires a sensitivity_marker "
                    "(programming error: this module's only sensitivity_halt "
                    "call site always supplies one)"
                )
            transcript = _render_sensitivity_halt_transcript(
                sensitivity_marker, resolved_task_id
            )
        else:
            transcript = _render_consultation_transcript(
                task_description, provisional_result, canary_fixture=resolved_canary_fixture
            )
        folded_error = _fold_error(
            provisional_result.error, _write_transcript(transcript_path, transcript)
        )

        record = _build_telemetry_record(provisional_result, task_id=resolved_task_id)
        folded_error = _fold_error(
            folded_error, _write_telemetry_record(telemetry_path, record)
        )

        # Spec 0004 ticket 24: the fourth journal family. Written here, at the
        # same choke point the transcript and telemetry record already use, for
        # the same reason — a per-call-site write is a guarantee that only
        # covers the sites someone remembered.
        #
        # One documented carve-out: a sensitivity halt writes no
        # dialogue-quality record. It ran no round, so the record would
        # describe a dialogue that never happened, and the journal must not
        # become the first exception to the halt boundary the rest of this
        # module keeps (`_render_sensitivity_halt_transcript`,
        # `_resolve_task_id`). `learning_journal.TaskLabel.for_halted_task`
        # already states this from the schema's side: "it runs no round, so
        # there is no `DialogueQualityRecord`".
        if outcome != "sensitivity_halt":
            folded_error = _fold_error(
                folded_error,
                _write_dialogue_quality_record(
                    provisional_result,
                    task_id=resolved_task_id,
                    run_id=dialogue_run_id,
                    root_dir=root_dir,
                ),
            )

        # Spec 0004 ticket 25 (fix pass 2): the outcome family's `plan` ground
        # truth. This consultation writes a record only when it can honestly
        # assert one thing: that the dialogue's Critic approved the Planner's
        # plan. That requires both `outcome == "consensus"` — nothing less
        # decisive counts — and `occasion in _PLAN_PRODUCING_OCCASIONS`,
        # because a `code-review` or `post-mortem` dialogue debates a diff or
        # a lesson, not a plan; `plan=accepted` about either would be a fact
        # asserted about an artifact that was never on the table.
        #
        # `stalemate` writes nothing, on purpose, not just "not yet reached
        # here". The three ways a human resolves a stalemate are enumerated
        # in `learning_outcomes.record_stalemate_resolution`, and option 1 is
        # "approve the Planner's architecture" — the exact plan a
        # `plan=rejected` record would already have condemned. A human who
        # resolves a stalemate that way would produce a `stalemate_resolution`
        # record and a `plan_outcome` record that flatly contradict each
        # other. `stalemate_resolution` is the one entry point allowed to say
        # how a stalemate ended; this choke point does not get a second,
        # earlier, and possibly wrong opinion.
        #
        # What `accepted=True` asserts, precisely: that the *dialogue*
        # accepted the plan — the Critic issued an APPROVE verdict this round.
        # That is not the same event as the developer's later sign-off on
        # `implementation_plan.md`, which happens after this function has
        # already returned and is recorded elsewhere. A reader of this record
        # must not read it as "the developer approved this plan"; it says
        # only that the Critic did.
        #
        # `plan=rejected` therefore has no producer anywhere in this module,
        # by design. The only actor who can honestly call a plan rejected is
        # the developer who rejects it — a documented orchestrator step, not
        # a debate outcome — because every path that reaches this choke point
        # either ended in the Critic's approval (worth recording) or ended
        # without an answer to "was the plan accepted" at all (a stalemate,
        # an unparseable verdict, a worker error, a canary, a budget skip, or
        # a sensitivity halt — none of which is a rejection).
        if outcome == "consensus" and occasion in _PLAN_PRODUCING_OCCASIONS:
            folded_error = _fold_error(
                folded_error,
                _write_plan_outcome_record(
                    task_id=resolved_task_id,
                    accepted=True,
                    run_id=dialogue_run_id,
                    root_dir=root_dir,
                ),
            )
        folded_error = _fold_error(folded_error, journal_wiring_error)

        return dataclasses.replace(provisional_result, error=folded_error)

    def _write_panel_manifest(
        status: str, security_veto: SecurityVeto | None = None
    ) -> str:
        """Write a terminal panel manifest, removing any plan if signing fails."""
        try:
            return write_council_manifest(
                status,
                council_run_id,
                root_dir,
                security_veto=security_veto,
            )
        except (OSError, RuntimeError) as exc:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            if cleanup_error is not None:
                raise RuntimeError(_fold_error(str(exc), cleanup_error)) from exc
            raise

    # Spec 0003 (CriticalDialogue) ticket 11: the gate now branches on
    # whether local reachability is even knowable, rather than halting on a
    # marker match unconditionally. Ticket 05's original reasoning — with no
    # way to prove a local runtime exists, "fail closed" is the only honest
    # answer — still holds exactly as written whenever `reachability_check`
    # was never supplied, so that half of this `if` is byte-for-byte the
    # same halt ticket 05 always produced. That is what keeps every
    # `AdvisorySensitivityGateTests` case (spec 0001; none of them ever pass
    # `reachability_check`) passing completely unmodified. But user story 19
    # asks for a sensitive task to be debatable "only between local models
    # from two local families," not to be unconditionally refused whenever
    # it happens to be sensitive — so a caller that DOES supply a seam for
    # asking "is this family up right now" gets asked, rather than being
    # short-circuited past it. `marker` (kept, not reassigned) is carried
    # forward past this point for exactly that reason: it still gates the
    # roster-resolution block below into a local-only shape, and it is what
    # that block's own `RosterResolutionError` handler reads to decide
    # whether "no reachable family" means `sensitivity_halt` or the ordinary
    # `worker_error` a non-sensitive task gets for the identical failure —
    # see that block's own comment for the fail-closed halt this deferral
    # ultimately still produces whenever local really is unavailable.
    marker = _detect_sensitivity_marker(task_description)
    if marker is not None and reachability_check is None:
        cleanup_error = _remove_stale_plan_artifact(plan_path)
        reason = (
            f"human approval required: task text matched sensitivity marker '{marker}'"
        )
        return _result(
            "sensitivity_halt",
            error=_fold_error(reason, cleanup_error),
            sensitivity_marker=marker,
        )

    # Spec 0003 (CriticalDialogue) ticket 09: the per-session dialogue
    # budget's degradation ladder. The rung is decided here, right after
    # the sensitivity gate (a halted task never needs a budget decision)
    # and before roster resolution, the canary branch, or any
    # `invoke_worker` call: `resolve_degradation_rung` is pure and reads no
    # session state of its own; `session_spend_so_far` is entirely
    # caller-tracked, exactly like ticket 07's `reachability_check` and
    # ticket 08's `is_canary_dialogue` seams — see this function's own
    # docstring and `resolve_degradation_rung`'s for the full contract.
    #
    # Only rung 3 and rung 1 are actually applied here, though. Rung 3 must
    # end the call before roster resolution, the canary branch, or any
    # `invoke_worker` call ever run, so it is checked immediately below.
    # Rung 1's round reduction has no roster dependency, so it is applied
    # immediately too. Rung 2's model/effort cheapening is deliberately
    # NOT applied here — see the block below the roster-resolution block
    # further down for why it has to run after roster resolution instead.
    budget_cap = _load_dialogue_budget_config(budget_config_path)
    degradation_rung = resolve_degradation_rung(
        session_spend_so_far, config_path=budget_config_path
    )
    if degradation_rung == 3:
        # Rung 3: full exhaustion. No Planner, no Critic, no roster
        # resolution, no canary — the dialogue simply does not run this
        # call. `_result` still fires (this is a normal `return` through
        # the module's one choke point, not a shortcut around it), so the
        # caller still gets a transcript and a telemetry record — see the
        # `"budget_skipped"` outcome's own comment on `AdvisoryOutcome` for
        # why that is exactly what "degradation is never silent" requires
        # even at the ladder's harshest rung.
        #
        # The stale-plan removal is guarded on `not is_canary`, though —
        # the preemption itself stays unconditional (a rung-3 canary still
        # returns `budget_skipped` with zero worker calls), but the
        # removal exists so the plan artifact is never staler than the
        # result describing it, and a canary's result describes a probe,
        # not the mission. Any `implementation_plan.md` sitting under
        # `root_dir` when a canary arrives is a REAL result's artifact,
        # still accurately described by that real result; deleting it here
        # would be exactly the contamination the canary invariant ("a
        # canary neither creates nor deletes that file") exists to prevent.
        cleanup_error = (
            None if is_canary else _remove_stale_plan_artifact(plan_path)
        )
        return _result("budget_skipped", error=cleanup_error)
    if degradation_rung >= 1:
        # Rung 1 (reduce rounds): reassigned as a plain enclosing-scope
        # local, exactly the mechanism ticket 07 already establishes below
        # for `planner_model`/`critic_model` — every downstream read of
        # `max_rounds` (the round loop's own `range(1, max_rounds + 1)`)
        # picks up the reduction automatically, in both pair and panel
        # mode, without either loop needing to know a budget ladder exists.
        max_rounds = min(max_rounds, _DEGRADED_ROUND_CAP)

    # Spec 0003 (CriticalDialogue) ticket 07: opt-in roster resolution.
    # Placed after the sensitivity gate (a halted task never needs a
    # roster) and before any worker is contacted (a resolved roster must be
    # in place before the first `invoke_worker` call uses it). Reassigning
    # `planner_model`/`critic_model`/`critic_a_model`/`critic_b_model` and
    # `result_critic_model` here, as plain enclosing-scope locals, is
    # sufficient for `_result` to see the resolved values: `_result` is a
    # closure that reads them at call time, not at its definition point
    # above, and its only call so far (`sensitivity_halt`) has already
    # returned by the time control reaches here.
    if reachability_check is not None:
        roster_topology: RosterTopology = "panel" if panel_mode else "pair"
        # Spec 0003 (CriticalDialogue) ticket 11: for a sensitive task
        # (`marker is not None` — and, by the sensitivity-gate block above,
        # that is only ever true here when `reachability_check` was indeed
        # supplied), no cloud family may be considered reachable, however
        # reachable the caller's own probe says it is. `resolve_roster`
        # itself stays completely ignorant of "sensitive" — it only ever
        # optimizes for cross-role independence, never for privacy — so
        # this composes that constraint on top of the caller's callable
        # instead of teaching `resolve_roster` a second, sensitivity-aware
        # resolution mode it would otherwise have no other use for.
        # `is_local_family` alone decides local-or-not; `reachability_check`
        # alone still decides up-or-down for whichever families remain
        # candidates — composition, not a new resolver. Every role's own
        # fallback chain then naturally walks past its cloud entries to its
        # local entry exactly as it already walks past any other
        # unreachable candidate; nothing about `resolve_roster`'s own walk
        # changes.
        #
        # `narrowed_reachability_check` exists only so the lambda below has
        # something to close over whose type mypy can see as
        # non-`None`: mypy does not carry a narrowed type
        # (`reachability_check is not None`, established by this block's own
        # `if`) into a nested function's free variables, since the outer
        # name could in principle be reassigned between the closure's
        # definition and its call — binding the already-narrowed value to
        # its own local name sidesteps that rather than fighting it with a
        # `# type: ignore`.
        narrowed_reachability_check = reachability_check
        effective_reachability_check = (
            narrowed_reachability_check
            if marker is None
            else (
                lambda family: is_local_family(family)
                and narrowed_reachability_check(family)
            )
        )
        try:
            roster = resolve_roster(
                roster_topology,
                is_family_reachable=effective_reachability_check,
                config_path=roster_config_path,
            )
        except RosterResolutionError as exc:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            # Spec 0003 (CriticalDialogue) ticket 11: for a sensitive task,
            # "no reachable family for some role" under the local-only
            # wrapper just above means exactly one thing — no local runtime
            # is actually up — which is precisely the condition user story
            # 19 requires to fail closed and escalate to the human, the same
            # way the sensitivity gate above already fails closed when no
            # `reachability_check` was ever supplied to ask the question at
            # all. Reusing `sensitivity_halt` here (rather than
            # `worker_error`, what this exact `except` reports for a
            # non-sensitive task) is what makes those two "local is
            # unavailable" paths converge on the same outcome and the same
            # redacted transcript (`_render_sensitivity_halt_transcript`
            # reads only `marker`/`task_id`, never `exc`), instead of a
            # sensitive task's roster exhaustion silently reading as a
            # generic worker error with no redaction boundary at all.
            if marker is not None:
                reason = (
                    f"human approval required: task text matched sensitivity "
                    f"marker '{marker}'; no local-family worker was reachable "
                    f"({exc})"
                )
                return _result(
                    "sensitivity_halt",
                    error=_fold_error(reason, cleanup_error),
                    sensitivity_marker=marker,
                )
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        planner_model = roster.model_for("planner")
        critic_a_model = roster.model_for("critic_a")
        critic_model = critic_a_model
        if panel_mode:
            critic_b_model = roster.model_for("critic_b")
        result_critic_model = critic_a_model if panel_mode else critic_model
        roster_degraded_independence = roster.degraded_independence

    # Spec 0003 (CriticalDialogue) ticket 09: rung 2's model/effort
    # cheapening. Deliberately placed here, after roster resolution rather
    # than alongside rung 1 above, so this override is what actually wins
    # for every downstream `invoke_worker` call: a rung-2 dialogue is
    # degraded because the session is out of budget, a stronger,
    # later-stage concern than "which family gives the best independence,"
    # so it takes priority over whatever `resolve_roster` just picked —
    # exactly as it already takes priority over the caller's own explicit
    # `planner_model`/`critic_model` arguments when `reachability_check` is
    # not supplied at all. Compounds on rung 1's round reduction above
    # rather than replacing it (see the module comment above
    # `DegradationRung`). `result_critic_model` is recomputed the same way
    # the roster-resolution block above computes it, so a panel run's
    # reported critic model stays consistent with what this rung actually
    # invokes.
    if degradation_rung >= 2:
        planner_effort = _DEGRADED_EFFORT
        critic_effort = _DEGRADED_EFFORT
        critic_a_effort = _DEGRADED_EFFORT
        critic_b_effort = _DEGRADED_EFFORT

        # Spec 0003 (CriticalDialogue) ticket 11: on a sensitive task
        # (`marker is not None`), rung 2 must degrade effort ONLY — it must
        # never touch which model fills each seat. `_load_degraded_roster_model`
        # reads `light_doer.name` from `routing-config.json`, which resolves
        # to "Codex 5.6 Terra ..." — a CLOUD model. Substituting it into
        # every role here unconditionally, the way a non-sensitive task's
        # rung 2 does just below, would launder a sensitive dialogue onto a
        # cloud worker the moment a session merely ran up its dialogue
        # budget — exactly the leak user story 19 exists to prevent, and a
        # far worse failure than the cost overrun rung 2 exists to control.
        # Privacy beats cost: a rung-2 sensitive dialogue is allowed to get
        # cheaper in effort, but never cheaper in family. The roster
        # resolved above is already local-only by construction whenever
        # `marker is not None` (see the roster-resolution block's own
        # `effective_reachability_check`), so leaving `planner_model`/
        # `critic_a_model`/`critic_model`/`critic_b_model` untouched here
        # simply keeps that already-local roster standing — there is
        # nothing more this rung needs to do for a sensitive task beyond
        # the effort reduction four lines above. Rung 1's round reduction
        # and rung 3's full skip both remain completely unaffected by
        # sensitivity, and correctly so: neither one ever touches which
        # family runs, so neither needed a carve-out here.
        if marker is None:
            degraded_model = _load_degraded_roster_model(budget_config_path)
            planner_model = degraded_model
            critic_a_model = degraded_model
            critic_model = degraded_model
            if panel_mode:
                critic_b_model = degraded_model
            result_critic_model = critic_a_model if panel_mode else critic_model
            # Spec 0003 story 14: a same-family fallback must be recorded as
            # degraded independence in both telemetry and transcript, whatever
            # mechanism caused it. One substituted model in every seat is a
            # single-family roster by construction — `classify_model_family`
            # maps the identical name to the identical family for every role —
            # which is exactly the "same family serves more than one role"
            # condition `resolve_roster` reports through this same flag. It is
            # set here as that constructive fact rather than re-derived from
            # the effective role models: the roster path computes the flag only
            # inside `resolve_roster`, and a derivation at this site would be a
            # computation whose answer is always True, dressed up as a check.
            # Rung 2 is allowed to *lose* cross-family independence (cutting
            # cost is its whole job); it is not allowed to lie about losing it
            # — a rung-2 dialogue is one model reviewing its own plan, the
            # exact self-preference hazard this flag exists to surface, and an
            # auditor filtering telemetry on `degraded_independence` must see
            # these dialogues too, not only `resolve_roster`'s own degraded
            # assignments. This deliberately includes `is_canary=True` runs,
            # even though a canary invokes only the Critic role: the flag
            # states the effective roster's family collapse, and on a canary
            # record it carries exactly the signal a canary auditor needs —
            # the probe measured the degraded cheap Critic, not the production
            # Critic. Mission-level aggregation never sees it, because canary
            # records are mandatorily filtered out (`outcome != "canary"`, per
            # `AdvisoryTelemetryRecord`'s own WARNING). None of this applies on
            # a sensitive task, whose roster (and therefore whose
            # `degraded_independence` value) this `if` leaves completely
            # alone — see this block's own comment above for why.
            roster_degraded_independence = True

    # The production loop below keeps its outcome decisions in this immutable
    # state machine. Presentation-oriented round lists remain in parallel for
    # the established result/transcript contract, while consensus, malformed
    # verdict, and terminal stalemate decisions all flow through `state`.
    state = DebateSessionState(occasion, complexity, max_rounds, panel_mode)

    # Spec 0003 (CriticalDialogue) ticket 08: the seeded-flaw canary round.
    # Placed after the sensitivity gate and (if opted into) roster
    # resolution, so a canary still fails closed on sensitive task text and
    # still uses a roster-resolved Critic when `reachability_check` is
    # supplied — but before the normal `invoke_worker` production-import
    # fallback below, which unconditionally calls
    # `_remove_stale_plan_artifact`. This branch resolves its own
    # `invoke_worker` fallback inline instead of falling through to that
    # shared block, specifically so it never calls that cleanup at all: a
    # canary must neither write NOR delete `implementation_plan.md` (see
    # this function's own docstring), and an already-current real plan
    # from an earlier consensus in this same `root_dir` must survive a
    # canary run completely untouched.
    #
    # `result_critic_model` is reassigned here to `critic_model` rather
    # than left at whatever `panel_mode` computed it to be above: a canary
    # always probes exactly one Critic — `critic_model`/`critic_effort`,
    # the pair-mode role — never `critic_a_model`/`critic_b_model`, even
    # when this occasion/complexity combination would otherwise select the
    # panel topology. Without this reassignment, a caller running a canary
    # under (for example) `occasion="plan-review", complexity="complex"`
    # with distinct `critic_model`/`critic_a_model` values would see
    # `result.critic_model` report a model this canary never actually
    # invoked.
    if is_canary:
        fixture = canary_fixture if canary_fixture is not None else CANARY_FIXTURES[0]
        result_critic_model = critic_model
        # Spec 0003 ticket 10: the identical reassignment, for the identical
        # reason, as `result_critic_model` immediately above — see this
        # block's own comment. A canary never invokes a second Critic, so
        # its reported topology must always be "pair", even when
        # `occasion`/`complexity` would otherwise select the panel
        # topology.
        result_topology = "pair"

        if invoke_worker is None:
            invoke_worker = DebateTransport(
                root_dir=root_dir,
                notifier=RecurringFailureNotifier(threshold=ESCALATION_FAILURE_THRESHOLD),
            ).invoke_worker

        canary_critic_prompt = build_canary_prompt(
            task_description, fixture.plan_text, occasion=occasion
        )
        try:
            canary_critic_response = invoke_worker(
                critic_model, critic_effort, canary_critic_prompt
            )
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            return _result("worker_error", error=str(exc))

        canary_critic_vote = _critic_response_from_payload(
            "critic", canary_critic_response, critic_model
        )
        security_veto = veto_handler.check((canary_critic_vote,))
        if security_veto is not None:
            return _result(
                "security_halt",
                security_veto=security_veto,
                error=str(security_veto),
            )

        canary_verdict = _parse_critic_verdict(canary_critic_response, fixture.plan_text)
        # Any non-approval is a catch (a reasoned objection or an
        # unparseable response alike) — only an "approved" verdict, under
        # the same VerdictContract every other round uses, is a miss. See
        # `AdvisoryDebateResult.canary_result`'s docstring for why this is
        # deliberately not a third canary state.
        canary_verdict_result: CanaryResult = (
            "miss" if canary_verdict.verdict == "approved" else "catch"
        )
        rounds.append(AdvisoryDebateRound(fixture.plan_text, canary_critic_response))
        # Spec 0003 ticket 10: kept parallel with the `rounds.append` just
        # above, `critic_b=None` since a canary never invokes a second
        # Critic — see `AdvisoryDebateResult.round_verdicts`'s docstring.
        round_verdicts.append(AdvisoryRoundVerdict(critic_a=canary_verdict))
        return _result(
            "canary",
            canary_result=canary_verdict_result,
            resolved_canary_fixture=fixture,
        )

    if invoke_worker is None:
        # `DebateTransport` (re-exported above but previously never
        # instantiated from this loop) is now the single source of truth for
        # isolated default-path process execution and for the failure
        # alerting `RecurringFailureNotifier` provides: it tracks consecutive
        # per-model failures and appends a durable `ERRORS.md` alert once
        # `ESCALATION_FAILURE_THRESHOLD` is reached -- the same escalation
        # rule this module's own `ESCALATION_FAILURE_THRESHOLD` constant
        # already documents. The base (unjournaled) path delegates straight
        # to `_transport.invoke_worker`; the journaled path below still runs
        # process execution through `_production_invoker`'s own journaling
        # wrapper (which needs to time the raw subprocess call itself) but
        # shares this exact `_transport.notifier` instance for alerting, so
        # there is never more than one notifier tracking this run's failures.
        _transport = DebateTransport(
            notifier=RecurringFailureNotifier(threshold=ESCALATION_FAILURE_THRESHOLD),
            root_dir=root_dir,
        )
        invoke_worker = _transport.invoke_worker
        try:
            # Any non-`sensitivity_halt` outcome resolves the same task_id
            # (see `_resolve_task_id`); the sensitivity gate above already
            # ruled that outcome out for this call, so resolving here is
            # exactly the id `_result` will resolve again for whichever
            # outcome this run actually reaches — the journal record and
            # this run's telemetry record stay correlated by TaskIdentity.
            journaled_task_id = _resolve_task_id(task_description, task_id, "consensus")
            journal_run_id = secrets.token_hex(8)
            journaled_invoke_worker = _current_production_invoker().make_journaled_invoke_worker(
                journaled_task_id, root_dir=root_dir, run_id=journal_run_id
            )
            dialogue_run_id = journal_run_id

            def invoke_worker(model: str, effort: str, prompt: str) -> str:
                """Journal via production_invoker, alerting through DebateTransport's shared notifier."""
                try:
                    output = journaled_invoke_worker(model, effort, prompt)
                except Exception as exc:  # re-raised untouched below; only tracked here.
                    _transport.notifier.record_failure(model, str(exc), _transport.root_dir)
                    raise
                _transport.notifier.record_success(model)
                return output
        except Exception as exc:  # noqa: BLE001 - instrumentation never aborts what it observes.
            journal_wiring_error = (
                f"worker-execution journaling disabled for this run: {exc}"
            )

    manifest_path: str | None = None
    for _round_number in range(1, max_rounds + 1):
        planner_prompt = _build_planner_prompt(
            task_description,
            occasion=occasion,
            previous_plan=previous_plan,
            critic_feedback=previous_critique,
            root_dir=root_dir,
            target_files=target_files,
        )
        try:
            planner_plan = invoke_worker(planner_model, planner_effort, planner_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        # `critic_prompt` is built once and reused verbatim for every Critic
        # in the round — one call in pair mode, two independent
        # `invoke_worker` calls (Critic A, Critic B) in panel mode — never
        # rebuilt per-Critic. Spec 0003 ticket 05: a panel Critic is
        # addressed as a distinct role by the `model` argument it is invoked
        # with, not by different prompt text, so `_build_critic_prompt`
        # needs no panel-awareness of its own.
        critic_prompt = _build_critic_prompt(
            task_description,
            planner_plan,
            occasion=occasion,
            root_dir=root_dir,
            target_files=target_files,
        )
        candidate_hash = hashlib.sha256(planner_plan.encode("utf-8")).hexdigest()

        if panel_mode:
            try:
                critic_a_response = invoke_worker(
                    critic_a_model, critic_a_effort, critic_prompt
                )
            except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("worker_error", error=_fold_error(str(exc), cleanup_error))
            try:
                critic_b_response = invoke_worker(
                    critic_b_model, critic_b_effort, critic_prompt
                )
            except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

            rounds.append(
                AdvisoryDebateRound(planner_plan, critic_a_response, critic_b_response)
            )
            critic_a_resp = _critic_response_from_payload(
                "critic_a",
                critic_a_response,
                critic_a_model,
            )
            critic_b_resp = _critic_response_from_payload(
                "critic_b",
                critic_b_response,
                critic_b_model,
            )
            # Same VerdictContract parser (ticket 02), applied independently
            # to each Critic's response, both checked against the same
            # reviewed artifact `planner_plan` — never against each other.
            verdict_a = _parse_critic_verdict(critic_a_response, planner_plan)
            verdict_b = _parse_critic_verdict(critic_b_response, planner_plan)
            # Spec 0003 ticket 10: kept parallel with the `rounds.append`
            # just above — both Critics' verdicts retained together, in one
            # entry, rather than as two separately-indexed lists that could
            # drift out of sync. See `AdvisoryRoundVerdict`'s docstring.
            round_verdicts.append(AdvisoryRoundVerdict(critic_a=verdict_a, critic_b=verdict_b))
            security_veto = veto_handler.check((critic_a_resp, critic_b_resp))
            if security_veto is not None:
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                manifest_path = _write_panel_manifest(
                    "SECURITY_HALT", security_veto=security_veto
                )
                return _result(
                    "security_halt",
                    security_veto=security_veto,
                    manifest_path=manifest_path,
                    error=_fold_error(str(security_veto), cleanup_error),
                )
            state = advance_debate_state(
                state,
                DebateRoundRecord(
                    _round_number,
                    planner_plan,
                    critic_a_response,
                    critic_b_response,
                    verdict_a.verdict,
                    verdict_b.verdict,
                ),
            )

            # An unparseable verdict from either Critic ends the panel
            # immediately; `advance_debate_state` is the authoritative
            # verdict transition for this decision.
            if state.error:
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                return _result("unparseable_verdict", error=_fold_error(state.error, cleanup_error))

            panel_status = panel_consensus_table.evaluate(
                (critic_a_resp, critic_b_resp), expected_hash=candidate_hash
            )
            if panel_status in {"UNANIMOUS", "QUALIFIED"}:
                panel_write_error: str | None = None
                try:
                    _atomic_text_write(plan_path, state.final_plan or planner_plan)
                except OSError as exc:
                    panel_write_error = (
                        f"failed to write plan artifact at {plan_path}: {exc}"
                    )
                manifest_path = _write_panel_manifest(panel_status)
                return _result(
                    "consensus",
                    final_plan=state.final_plan or planner_plan,
                    error=panel_write_error,
                    manifest_path=manifest_path,
                )

            if state.stalemate_report is not None or _round_number >= max_rounds:
                cleanup_error = _remove_stale_plan_artifact(plan_path)
                if state.stalemate_report is None:
                    state = dataclasses.replace(
                        state,
                        consensus_reached=False,
                        final_plan=None,
                        stalemate_report=_build_stalemate_report(
                            planner_plan, critic_a_response, critic_b_response
                        ),
                    )
                manifest_path = _write_panel_manifest("STALEMATE")
                return _result(
                    "stalemate",
                    stalemate=state.stalemate_report,
                    error=cleanup_error,
                    manifest_path=manifest_path,
                )

            # Any other combination — one approves and one objects, or both
            # object — is not consensus and starts another round.
            previous_plan = planner_plan
            # The folded string `_build_planner_prompt` expects for the
            # Planner's next revision prompt — completely unmodified for
            # panel mode, ticket 05's original reuse.
            previous_critique = _combine_panel_critic_feedback(
                critic_a_response, critic_b_response
            )
            # Each Critic's own last response, kept separate (ticket 06) so
            # a stalemate report at the cap can carry three distinct voices
            # instead of re-deriving them from the fold above.
            previous_critic_a_response = critic_a_response
            previous_critic_b_response = critic_b_response
            continue

        try:
            critic_response = invoke_worker(critic_model, critic_effort, critic_prompt)
        except Exception as exc:  # noqa: BLE001 - a worker failure must fail closed.
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("worker_error", error=_fold_error(str(exc), cleanup_error))

        rounds.append(AdvisoryDebateRound(planner_plan, critic_response))
        critic_resp = _critic_response_from_payload("critic", critic_response)
        # `planner_plan` is the reviewed artifact: the VerdictContract
        # (spec 0003 ticket 02) verifies the Critic's quotes against exactly
        # what the Critic was shown as "Planner's plan" in `critic_prompt`
        # above, never against the task description or anything else.
        verdict_result = _parse_critic_verdict(critic_response, planner_plan)
        # Spec 0003 ticket 10: kept parallel with the `rounds.append` just
        # above, `critic_b=None` since this is a pair-mode round. See
        # `AdvisoryDebateResult.round_verdicts`'s docstring.
        round_verdicts.append(AdvisoryRoundVerdict(critic_a=verdict_result))
        security_veto = veto_handler.check((critic_resp,))
        if security_veto is not None:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result(
                "security_halt",
                security_veto=security_veto,
                error=_fold_error(str(security_veto), cleanup_error),
            )
        state = advance_debate_state(
            state,
            DebateRoundRecord(
                _round_number,
                planner_plan,
                critic_response,
                critic_a_verdict=verdict_result.verdict,
            ),
        )

        if state.consensus_reached:
            write_error: str | None = None
            try:
                _atomic_text_write(plan_path, state.final_plan or planner_plan)
            except OSError as exc:
                write_error = f"failed to write plan artifact at {plan_path}: {exc}"
            return _result("consensus", final_plan=state.final_plan or planner_plan, error=write_error)

        if state.error:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("unparseable_verdict", error=_fold_error(state.error, cleanup_error))

        if state.stalemate_report is not None:
            cleanup_error = _remove_stale_plan_artifact(plan_path)
            return _result("stalemate", stalemate=state.stalemate_report, error=cleanup_error)

        previous_plan = planner_plan
        previous_critique = critic_response

    cleanup_error = _remove_stale_plan_artifact(plan_path)
    # Spec 0003 ticket 06: a panel stalemate gets a three-voice report —
    # Critic A's and Critic B's last responses stay separate fields, never
    # folded into one string — via `_build_stalemate_report`'s
    # `critic_b_position` parameter. Pair mode's call below is completely
    # unchanged: two positional arguments, `critic_b_position` left at its
    # `None` default, byte-for-byte the same report shape spec 0001 shipped.
    if state.stalemate_report is not None:
        stalemate = state.stalemate_report
    elif panel_mode:
        stalemate = _build_stalemate_report(
            previous_plan or "",
            previous_critic_a_response or "",
            previous_critic_b_response or "",
        )
    else:
        stalemate = _build_stalemate_report(previous_plan or "", previous_critique or "")
    manifest_path = (
        _write_panel_manifest("STALEMATE") if panel_mode else None
    )
    return _result(
        "stalemate",
        stalemate=stalemate,
        error=cleanup_error,
        manifest_path=manifest_path,
    )


def _run_dispatched_post_mortem(
    task_description: str,
    invoke_worker: InvokeWorker | None,
    *,
    root_dir: Path,
    max_rounds: int,
    planner_model: str,
    critic_model: str,
    planner_effort: str,
    critic_effort: str,
    task_id: str | None,
    reachability_check: IsFamilyReachable | None,
    roster_config_path: Path,
    session_spend_so_far: int,
    budget_config_path: Path,
) -> None:
    """`dispatch_post_mortem_consultation`'s actual thread target.

    `run_advisory_consultation_debate` already fails closed for every
    documented outcome (a worker error, a stalemate, an unparseable verdict,
    a sensitivity halt — and a `budget_skipped`, which this wrapper's own
    `session_spend_so_far` parameter can produce): each one reaches the
    function's own `_result`
    choke point, which writes the transcript and telemetry record before
    returning. Calling it from a background thread does not weaken any of
    that — those writes happen exactly the same way regardless of which
    thread is running the code.

    What a background thread changes is the *unhandled* case: an exception
    that is not one of those documented outcomes — a genuine bug, in this
    function or a future change to it, of a kind `run_advisory_consultation_debate`
    never anticipated catching. Raised from a synchronous call, that
    exception propagates to the caller: loud, ugly, but visible — the
    caller's process sees it. Raised inside a plain `threading.Thread`
    target, it instead hits Python's default unhandled-exception-in-thread
    behavior: printed once to stderr by `threading.excepthook` and then
    gone, with no transcript, no telemetry record, and nothing the
    dispatching caller can observe — a silent hole in exactly the kind of
    record-keeping this whole ticket exists to guarantee. This wrapper's
    only job is closing that hole: catch anything unexpected and record it
    through the same transcript/telemetry primitives
    `run_advisory_consultation_debate` itself uses (`_render_consultation_transcript`,
    `_write_transcript`, `_build_telemetry_record`, `_write_telemetry_record`,
    `_resolve_task_id` — same functions, same `_TRANSCRIPT_RELATIVE_PATH`/
    `_TELEMETRY_RELATIVE_PATH` paths, not a parallel implementation of any
    of them) rather than reinventing an error-reporting path for this one
    caller.

    The synthesized `AdvisoryDebateResult` this builds on an unexpected
    exception reuses the existing `"worker_error"` outcome rather than
    inventing an eighth `AdvisoryOutcome` value: `AdvisoryOutcome` is a closed
    `Literal`, and every caller that branches on it today was written
    against exactly seven values, none of them "the dispatch mechanism
    itself broke." `"worker_error"` is the closest existing meaning — "the
    consultation could not be trusted to have run correctly" — and reusing
    it keeps this a minimal recovery net, not new type-level surface area,
    exactly as this ticket is scoped to be. `rounds_run=0` and
    `final_plan=""` are honest here: unlike a genuine `worker_error` from
    inside the round loop, this exception could have occurred before a
    single round ran (e.g. while resolving the task id), so claiming any
    rounds happened would overstate what is actually known. The `error`
    field carries the exception's `str()`, prefixed to make this failure
    mode distinguishable in a transcript from an ordinary `invoke_worker`
    failure — never the exception object itself, so this function never
    needs to reason about whether some future exception type's `__repr__`
    could leak more than a plain message would (`except Exception`, not
    `BaseException`, so `SystemExit`/`KeyboardInterrupt` still propagate —
    the module's existing convention everywhere else it catches worker
    failures).

    One deliberate departure from `_render_consultation_transcript`'s normal
    output: that renderer never includes `result.error` in the transcript
    text, for any outcome, anywhere in this module — a synchronous caller
    already has `result.error` on the object it was handed back, so writing
    it to disk too would be redundant, and every existing test that reads a
    `worker_error` transcript confirms only the outcome line is expected
    there. This function has no such synchronous caller to fall back on —
    dispatch discards the eventual `AdvisoryDebateResult` on purpose, so if
    the crash detail is not written down here, it is gone the moment this
    thread ends, sitting nowhere any operator could ever read it. So this
    one crash-only transcript gets an appended section carrying
    `crash_result.error` that `_render_consultation_transcript`'s own output
    for every other outcome and every other call site still deliberately
    omits.

    **No `DialogueQualityRecord` is written on this path, deliberately.**
    `crash_result` is a guess, not a measurement: `rounds_run=0` and
    `round_verdicts=()` are hardcoded above because the exception may have
    fired before any round ran at all, so a dialogue-quality record built from
    it would assert "this dialogue ran zero rounds" about a dialogue whose
    real state is unknown. Worse, the `try` above wraps the whole call to
    `run_advisory_consultation_debate` — including its own `_result` choke
    point — so an exception raised after that inner `_result` already wrote
    its record would produce a *second* dialogue-quality record for one
    dialogue, breaking the one-record-per-dialogue guarantee on the very path
    meant to be a safety net. The transcript this function writes already
    carries the crash detail for an operator, so nothing is unobservable by
    skipping the fourth journal family here.
    """
    try:
        run_advisory_consultation_debate(
            task_description,
            invoke_worker,
            root_dir=root_dir,
            occasion="post-mortem",
            max_rounds=max_rounds,
            planner_model=planner_model,
            critic_model=critic_model,
            planner_effort=planner_effort,
            critic_effort=critic_effort,
            task_id=task_id,
            reachability_check=reachability_check,
            roster_config_path=roster_config_path,
            session_spend_so_far=session_spend_so_far,
            budget_config_path=budget_config_path,
        )
    except Exception as exc:  # noqa: BLE001 - last-resort net for a dispatched thread; see docstring above.
        crash_result = AdvisoryDebateResult(
            rounds_run=0,
            final_plan="",
            outcome="worker_error",
            occasion="post-mortem",
            planner_model=planner_model,
            critic_model=critic_model,
            error=f"dispatch_post_mortem_consultation: unexpected exception: {exc}",
        )
        resolved_task_id = _resolve_task_id(task_description, task_id, "worker_error")
        transcript = _render_consultation_transcript(task_description, crash_result)
        transcript += (
            "\n\n## Dispatch failure\n\n"
            "This post-mortem's own dispatch mechanism raised an exception "
            "outside `run_advisory_consultation_debate`'s documented failure "
            f"paths:\n\n{crash_result.error}\n"
        )
        _write_transcript(root_dir / _TRANSCRIPT_RELATIVE_PATH, transcript)
        record = _build_telemetry_record(crash_result, task_id=resolved_task_id)
        _write_telemetry_record(root_dir / _TELEMETRY_RELATIVE_PATH, record)


# Spec 0003 (CriticalDialogue) ticket 04: blocking stance per occasion. Plan
# review and code review need no new code at all: calling
# `run_advisory_consultation_debate` synchronously already blocks the
# caller, because it is an ordinary Python function call — the caller's next
# line simply does not execute until the round loop above returns. That is
# spec 0003's "Plan-review and code-review dialogues gate progress"
# (Implementation Decisions, "Blocking stance") in its entirety for those two
# occasions: nothing to build, only to characterize with a test, so a future
# change that accidentally makes either occasion non-blocking (e.g. by
# routing it through the dispatcher below by mistake) gets caught. See
# `AdvisoryBlockingStanceTests` in `test_routing.py`.
#
# Post-mortem needs the opposite stance: "Post-mortems run in the background
# and never block; their occurrence and outcome are still recorded." Two
# facts about this module made the mechanism choice easy. First, there is no
# threading or asyncio precedent anywhere in this file or its siblings to
# preserve or work around: `agent_council.py` uses `asyncio` internally, but
# for something unrelated to this loop, and nothing in either module has
# ever spawned a thread before this ticket. Second, there is today no
# *production* caller of `run_advisory_consultation_debate` at all — every
# call site is a test, confirmed by grep across the repo
# (`test_routing.py` and `test_production_invoker.py`, both under
# `skills/worker-routing/`) — so there is no existing production call-site
# contract this dispatcher needs to slot into, only a future one to make
# possible. Given both, `threading.Thread` is the smallest correct
# mechanism: stdlib, no new dependency, and its target (`_run_dispatched_post_mortem`,
# below) calls `run_advisory_consultation_debate` completely unmodified, so
# every one of that function's existing side effects (the transcript, the
# telemetry record, and — on consensus — the plan artifact) fires exactly as
# documented, just from a background thread instead of the caller's own; the
# wrapper adds only a last-resort exception net around that call (see
# `_run_dispatched_post_mortem`'s own docstring), not any change to the
# call itself. Nothing here is a job queue, a retry policy, or a supervisor:
# one dispatch is one thread, and `dispatch_post_mortem_consultation`'s only
# job is starting it and handing the caller its handle.
#
# That thread is started non-daemon (`daemon=False`, the `threading.Thread`
# default — spelled out explicitly below rather than left implicit, because
# getting this one flag wrong would quietly break the ticket's own
# guarantee). A daemon thread is killed without any cleanup the instant the
# interpreter has no non-daemon threads left to run — which, for a
# short-lived CLI or mission runner, could easily be moments after
# `dispatch_post_mortem_consultation` returns. A blocking call can *never*
# have that failure mode: the process is physically inside the call and
# cannot exit before the write happens. A daemon dispatch thread could —
# the process exits, the debate is killed mid-round, and the ticket's
# promise ("A post-mortem's eventual record ... is still written and
# discoverable after the fact, exactly as if it had blocked") silently does
# not hold. `daemon=False` instead makes the interpreter wait for this
# thread before it can exit at all, which is what actually backs "the mission
# path returns immediately, but the record still lands eventually" rather
# than merely "the record lands eventually, unless the process happens to
# exit first." This is not a new hang risk introduced by this ticket: each
# `invoke_worker` call already carries its own bounded timeout
# (`production_invoker.DEFAULT_TIMEOUT_SECONDS`, inherited from spec 0001's
# per-round time limit), and `MAX_DEBATE_ROUNDS` bounds how many times that
# timeout can be hit — so a non-daemon dispatch thread is bounded by the
# same ceiling a synchronous call already was, not an unbounded one.
def dispatch_post_mortem_consultation(
    task_description: str,
    invoke_worker: InvokeWorker | None = None,
    *,
    root_dir: Path,
    max_rounds: int = MAX_DEBATE_ROUNDS,
    planner_model: str = "Claude Opus 5 (Thinking)",
    critic_model: str = "Codex 5.6 Sol",
    planner_effort: str = "high",
    critic_effort: str = "high",
    task_id: str | None = None,
    reachability_check: IsFamilyReachable | None = None,
    roster_config_path: Path = _CONFIG_PATH,
    session_spend_so_far: int = 0,
    budget_config_path: Path = _CONFIG_PATH,
) -> threading.Thread:
    """Dispatch a post-mortem CriticalDialogue on a background thread and return immediately.

    Exposes a deliberate subset of `run_advisory_consultation_debate`'s
    parameters, not a mirror of them: the pair-mode roster and effort knobs
    (`planner_model`/`critic_model`/`planner_effort`/`critic_effort`),
    `max_rounds`, `task_id`, ticket 07's roster seam
    (`reachability_check`/`roster_config_path` — same names, same
    defaults), and ticket 09's budget seam
    (`session_spend_so_far`/`budget_config_path` — same names, same
    defaults), threaded through unchanged. The budget seam is not optional
    surface here: the post-mortem occasion fires on every failure,
    escalation, and stalemate — exactly the sessions most likely to be
    deep into their dialogue budget — so a dispatch path without it would
    make post-mortems the one occasion the degradation ladder could never
    reduce, cheapen, or skip.

    The roster seam is exposed for a closely related reason, surfaced only
    once ticket 11 gave the post-mortem occasion a sensitivity gate of its
    own: without `reachability_check`, this function always called
    `run_advisory_consultation_debate` with `reachability_check=None`, and
    that function halts a sensitive task unconditionally whenever
    `reachability_check is None` (see its own "Sensitivity halt"
    documentation) — so a sensitive post-mortem dispatched through the old,
    narrower signature could never hold the local-only dialogue user story
    19 requires, only ever the fail-closed halt spec 0001 already gave
    every occasion. Threading `reachability_check`/`roster_config_path`
    through here, unchanged in name, default, and meaning, is what lets a
    caller that can answer "is this local family up right now" give a
    sensitive post-mortem the same chance every other occasion already had
    to resolve a local-only roster instead of always escalating.

    The debate function's remaining knobs — the panel-topology
    models/efforts and `complexity` (a post-mortem never selects the panel
    topology; see `_is_panel_topology`) and the canary seam
    (`is_canary`/`canary_fixture`) — stay deliberately unexposed: neither
    has a post-mortem consumer today, and this keyword-only signature can
    grow either of them later without breaking an existing call site.
    `occasion` is hardcoded to `"post-mortem"` in the call below rather
    than exposed as a knob — this function exists specifically to dispatch the
    post-mortem occasion (its name says so), and a caller wanting a
    background dispatch of a different occasion is out of this ticket's
    scope (spec 0003 only specifies post-mortem as non-blocking) and would
    need its own function, not a parameter bolted onto this one that could
    be misused to silently make a supposedly-blocking occasion
    non-blocking.

    Delegates to `run_advisory_consultation_debate` as the background
    thread's target — via `_run_dispatched_post_mortem`, a thin wrapper
    described below, but the call inside it is unmodified — so every
    documented side effect (the transcript at
    `root_dir / ".scratch" / "planning_debate.md"` and the telemetry record
    at `root_dir / ".ralph" / "routing_telemetry.jsonl"`) still happens,
    just after this function has already returned control to its caller.
    The `AdvisoryDebateResult` that call eventually produces is not captured
    or exposed here: nobody is waiting for it — that is the entire point of
    "does not block" — and its outcome is fully recoverable from the
    transcript and telemetry it writes once the thread completes.

    `max_rounds` is validated synchronously, before the thread is even
    started, exactly the same check `run_advisory_consultation_debate`
    itself makes (raising `ValueError` for `< 1`). This is deliberate, not
    redundant: a bad `max_rounds` is a call-site programming error, and if
    it were left to surface only inside the background thread, it would
    become an unhandled thread exception — printed to stderr by Python's
    default thread excepthook, never raised back to the caller, and never
    written to a transcript or telemetry record, since the function would
    have failed before reaching its own choke point. That would hide exactly
    the kind of mistake this module raises loudly for everywhere else. Every
    other non-consensus outcome (a worker error, a stalemate, an
    unparseable verdict, a sensitivity halt) is a legitimate dialogue
    outcome rather than a programming error, so each is left to happen
    inside the thread and be recorded by `run_advisory_consultation_debate`'s
    own choke point exactly as it would be for a synchronous call.

    Returns the already-started `threading.Thread` (`daemon=False` — see the
    module comment above `dispatch_post_mortem_consultation` for why a
    daemon thread here would risk the record never being written at all). A
    caller does not need this handle to get the non-blocking guarantee; it
    is returned so a test — or a caller that genuinely wants to wait, e.g.
    at orderly shutdown — can `.join()` it deterministically instead of
    polling the filesystem or sleeping.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1, got {max_rounds}")

    thread = threading.Thread(
        target=_run_dispatched_post_mortem,
        args=(task_description, invoke_worker),
        kwargs={
            "root_dir": root_dir,
            "max_rounds": max_rounds,
            "planner_model": planner_model,
            "critic_model": critic_model,
            "planner_effort": planner_effort,
            "critic_effort": critic_effort,
            "task_id": task_id,
            "reachability_check": reachability_check,
            "roster_config_path": roster_config_path,
            "session_spend_so_far": session_spend_so_far,
            "budget_config_path": budget_config_path,
        },
        name="advisory-post-mortem-dispatch",
        daemon=False,
    )
    thread.start()
    return thread


def run_debate_loop(*args: Any, **kwargs: Any) -> AdvisoryDebateResult:
    """Execute the production Planner/Critic state machine."""
    return run_advisory_consultation_debate(*args, **kwargs)


# Historic name retained as an exact alias, rather than a forwarding wrapper:
# callers inspecting the public API receive the complete production signature.
run_critical_dialogue = run_advisory_consultation_debate


def run_canary_dialogue(*args: Any, **kwargs: Any) -> AdvisoryDebateResult:
    """Execute one seeded-flaw Critic probe through the production loop."""
    kwargs["is_canary"] = True
    return run_advisory_consultation_debate(*args, **kwargs)


def run_post_mortem_loop(*args: Any, **kwargs: Any) -> AdvisoryDebateResult:
    """Execute a post-mortem through the ordinary production loop."""
    kwargs.setdefault("occasion", "post-mortem")
    return run_advisory_consultation_debate(*args, **kwargs)
