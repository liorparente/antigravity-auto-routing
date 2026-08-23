from __future__ import annotations

import dataclasses
import hashlib
import sys
from pathlib import Path
from typing import Any

# Add skills/worker-routing to path if available
WORKER_ROUTING_DIR = str(Path(__file__).resolve().parent.parent.parent / "worker-routing")
if WORKER_ROUTING_DIR not in sys.path:
    sys.path.insert(0, WORKER_ROUTING_DIR)

from dialogue_contracts import parse_perspective_review  # type: ignore[import-not-found]
from production_invoker import (  # type: ignore[import-not-found]
    WORKER_MODE_TOKEN,
    AsyncRunner,
    WorkerExecutionResult,
    build_worker_command,
    extract_review_payload,
    invoke_worker_async,
)
from prompt_assembler import build_perspective_reviewer_prompt  # type: ignore[import-not-found]


class ReviewerAdapter:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        raise NotImplementedError


class CLIReviewerAdapter(ReviewerAdapter):
    def __init__(
        self,
        provider_id: str,
        model: str,
        effort: str,
        executable: str = "",
        *,
        runner: AsyncRunner | None = None,
    ) -> None:
        super().__init__(provider_id)
        self.model = model
        self.effort = effort
        self._runner = runner

    def _get_args(self, prompt: str) -> list[str]:
        return build_worker_command(self.model, self.effort, prompt)

    def _parse_output(self, raw_output: str) -> dict[str, Any]:
        payload = extract_review_payload(raw_output)
        payload["provider"] = self.provider_id
        return payload

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        prompt = f"Round {round_spec} review for proposal:\n{envelope}"
        try:
            kwargs: dict[str, Any] = {}
            if self._runner is not None:
                kwargs["runner"] = self._runner
            result: WorkerExecutionResult = await invoke_worker_async(
                self.model,
                self.effort,
                prompt,
                timeout=float(deadline),
                **kwargs,
            )
            if not result.success:
                return {
                    "provider": self.provider_id,
                    "vote": "abstain",
                    "confidence": 0.0,
                    "error": result.error or "Execution failed",
                }
            payload = result.parsed_payload or extract_review_payload(result.raw_output)
            payload_dict = dict(payload)
            payload_dict["provider"] = self.provider_id
            return payload_dict
        except Exception as error:  # noqa: BLE001
            return {
                "provider": self.provider_id,
                "vote": "abstain",
                "confidence": 0.0,
                "error": str(error),
            }


class ClaudeAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str, *, runner: AsyncRunner | None = None) -> None:
        super().__init__("claude", model, effort, "claude", runner=runner)


class CodexAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str, *, runner: AsyncRunner | None = None) -> None:
        super().__init__("codex", model, effort, "codex", runner=runner)


class AgyAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str, *, runner: AsyncRunner | None = None) -> None:
        super().__init__("gemini", model, effort, "agy", runner=runner)


# Explicit confidence per `dialogue_contracts.PerspectiveVerdict` outcome, so
# every successful `PerspectiveReviewerAdapter.review()` carries a real
# confidence rather than relying on `ConsensusTable`'s own verdict-keyed
# default. "unparseable" gets the same 0.0 as abstain/error — it is not a
# usable vote either.
PERSPECTIVE_VERDICT_CONFIDENCE: dict[str, float] = {
    "approved": 1.0,
    "blocked": -1.0,
    "revise": -0.3,
    "unparseable": 0.0,
}


class PerspectiveReviewerAdapter(ReviewerAdapter):
    """Reviews an artifact through one of the four Council perspectives
    (spec 0012 / workflow-v2 ticket 07), rather than a vendor-branded
    identity. `provider_id` is the perspective itself (e.g.
    `"reviewer_security"`) — the same value `ConsensusTable._identity` and
    `SecurityVetoHandler.check` read from a vote's `"perspective"` field, so
    weighting and the unilateral security veto both key off this adapter's
    output correctly.

    Uses `build_perspective_reviewer_prompt`/`parse_perspective_review`
    instead of `CLIReviewerAdapter`'s plain vote/confidence contract, since a
    perspective reviewer's response carries structured findings and a
    reserved `VERDICT: BLOCK` line neither of those understand.
    """

    def __init__(
        self,
        perspective: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        occasion: str = "plan-review",
        task_description: str = "",
        runner: AsyncRunner | None = None,
    ) -> None:
        super().__init__(perspective)
        self.perspective = perspective
        # `model` defaults to the perspective's own role id: `build_worker_command`
        # resolves an abstract role (e.g. "reviewer_security") through the
        # default `RoleResolver` when no concrete model is supplied.
        self.model = model or perspective
        self.effort = effort or ""
        self.occasion = occasion
        self.task_description = task_description
        self._runner = runner

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        prompt = build_perspective_reviewer_prompt(
            self.perspective,
            self.task_description or "Proposal review",
            envelope,
            occasion=self.occasion,
        )
        try:
            kwargs: dict[str, Any] = {}
            if self._runner is not None:
                kwargs["runner"] = self._runner
            result: WorkerExecutionResult = await invoke_worker_async(
                self.model,
                self.effort,
                prompt,
                timeout=float(deadline),
                **kwargs,
            )
            if not result.success:
                return {
                    "provider": self.perspective,
                    "perspective": self.perspective,
                    "vote": "abstain",
                    "confidence": 0.0,
                    "error": result.error or "Execution failed",
                }
            review_result = parse_perspective_review(
                result.raw_output, envelope, default_perspective=self.perspective
            )
            return {
                "provider": self.perspective,
                "perspective": review_result.perspective or self.perspective,
                "vote": review_result.verdict,
                "confidence": PERSPECTIVE_VERDICT_CONFIDENCE.get(review_result.verdict, 0.0),
                "verified_quote_count": review_result.verified_quote_count,
                "objection_count": review_result.objection_count,
                "findings": [dataclasses.asdict(finding) for finding in review_result.findings],
                "raw_vote": review_result.raw_vote,
                "candidate_hash": hashlib.sha256(envelope.encode("utf-8")).hexdigest(),
            }
        except Exception as error:  # noqa: BLE001
            return {
                "provider": self.perspective,
                "perspective": self.perspective,
                "vote": "abstain",
                "confidence": 0.0,
                "error": str(error),
            }


class LMStudioAdapter(ReviewerAdapter):
    """Local LM Studio endpoint invocation.

    Not yet wired to a real endpoint — a stub that fabricated `"approve"`
    would silently rubber-stamp every review and stalemate it adjudicates,
    including while LM Studio is offline. Abstain with zero confidence
    instead, so a caller relying on this adapter fails closed rather than
    auto-approving.
    """

    def __init__(self, model: str, effort: str) -> None:
        super().__init__("lm-studio")
        self.model = model
        self.effort = effort

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        return {
            "provider": self.provider_id,
            "vote": "abstain",
            "confidence": 0.0,
            "findings": [],
            "error": "LMStudioAdapter is a stub — no local endpoint is wired up",
        }


class FakeReviewerAdapter(ReviewerAdapter):
    def __init__(self, provider_id: str, fixed_responses: list[dict[str, Any]]) -> None:
        super().__init__(provider_id)
        self.fixed_responses = fixed_responses
        self.call_count = 0

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        if self.call_count < len(self.fixed_responses):
            resp = self.fixed_responses[self.call_count]
            self.call_count += 1
            return resp
        return {"provider": self.provider_id, "vote": "abstain", "confidence": 0.0}


def build_adapter(config: dict[str, Any]) -> ReviewerAdapter:
    pid = str(config.get("id", ""))
    model = str(config.get("model", ""))
    effort = str(config.get("effort_mapping", {}).get("high", "high"))

    if pid == "claude":
        return ClaudeAdapter(model, effort)
    elif pid == "codex":
        return CodexAdapter(model, effort)
    elif pid in ["gemini", "agy"]:
        return AgyAdapter(model, effort)
    elif pid == "lm-studio":
        return LMStudioAdapter(model, effort)
    else:
        raise ValueError(f"Unknown reviewer provider id: {pid}")


__all__ = [
    "WORKER_MODE_TOKEN",
    "AgyAdapter",
    "CLIReviewerAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "FakeReviewerAdapter",
    "LMStudioAdapter",
    "PerspectiveReviewerAdapter",
    "ReviewerAdapter",
    "build_adapter",
]
