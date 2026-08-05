import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "worker-routing"))
from constants import NESTED_WORKER_WARNING, ROUTING_ENV_VAR

class ReviewerAdapter:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict:
        raise NotImplementedError

class CLIReviewerAdapter(ReviewerAdapter):
    def __init__(self, provider_id: str, model: str, effort: str, executable: str):
        super().__init__(provider_id)
        self.model = model
        self.effort = effort
        self.executable = executable

    def _get_args(self) -> List[str]:
        raise NotImplementedError

    async def _run_cli(self, args: List[str], stdin_data: str, deadline: int) -> str:
        if NESTED_WORKER_WARNING not in stdin_data:
            stdin_data = f"{NESTED_WORKER_WARNING}\n\n{stdin_data}"
        proc = await asyncio.create_subprocess_exec(
            self.executable, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={ROUTING_ENV_VAR: "1", "PATH": os.environ.get("PATH", "")}
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(input=stdin_data.encode()), timeout=deadline)
            if proc.returncode != 0:
                raise RuntimeError(f"{self.provider_id} failed: {stderr.decode()}")
            return stdout.decode()
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"{self.provider_id} exceeded deadline")

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict:
        args = self._get_args()
        try:
            out = await self._run_cli(args, envelope, deadline)
            return {"provider": self.provider_id, "vote": "approve"}
        except Exception as e:
            return {"provider": self.provider_id, "vote": "abstain", "error": str(e)}

class ClaudeAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str):
        super().__init__("claude", model, effort, "claude")

    def _get_args(self) -> List[str]:
        return ["-p", "--model", self.model, "--output-format", "stream-json", "-c", f"model_reasoning_effort={self.effort}"]

class CodexAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str):
        super().__init__("codex", model, effort, "codex")

    def _get_args(self) -> List[str]:
        return ["review", "--uncommitted", "-c", f"model={self.model}", "-c", f"model_reasoning_effort={self.effort}"]

class AgyAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str):
        super().__init__("agy", model, effort, "agy")

    def _get_args(self) -> List[str]:
        return ["-p"]

class LMStudioAdapter(ReviewerAdapter):
    def __init__(self, model: str, effort: str):
        super().__init__("lm-studio")
        self.model = model
        self.effort = effort

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict:
        # Fake REST call for LM Studio
        return {"provider": self.provider_id, "vote": "approve"}

class FakeReviewerAdapter(ReviewerAdapter):
    def __init__(self, provider_id: str, fixed_responses: List[dict]):
        super().__init__(provider_id)
        self.fixed_responses = fixed_responses
        self.call_count = 0

    async def review(self, envelope: str, round_spec: int, deadline: int) -> dict:
        if self.call_count < len(self.fixed_responses):
            resp = self.fixed_responses[self.call_count]
            self.call_count += 1
            return resp
        return {"provider": self.provider_id, "vote": "abstain"}
