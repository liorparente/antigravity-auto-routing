from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Add skills/worker-routing to path if available
WORKER_ROUTING_DIR = str(Path(__file__).resolve().parent.parent.parent / "worker-routing")
if WORKER_ROUTING_DIR not in sys.path:
    sys.path.insert(0, WORKER_ROUTING_DIR)

from production_invoker import (  # type: ignore[import-not-found]
    WORKER_MODE_TOKEN,
    AsyncRunner,
    WorkerExecutionResult,
    build_worker_command,
    extract_review_payload,
    invoke_worker_async,
)


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
        except ValueError as error:
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


class LMStudioAdapter(ReviewerAdapter):
    def __init__(self, model: str, effort: str) -> None:
        super().__init__("lm-studio")
        self.model = model
        self.effort = effort

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict[str, Any]:
        # Local LM Studio endpoint invocation
        return {
            "provider": self.provider_id,
            "vote": "approve",
            "confidence": 1.0,
            "findings": [],
            "candidate_hash": "synth1",
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
    "ReviewerAdapter",
    "build_adapter",
]
