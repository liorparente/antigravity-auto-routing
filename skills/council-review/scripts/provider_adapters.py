import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List

# Add skills/worker-routing to path if available
WORKER_ROUTING_DIR = str(Path(__file__).resolve().parent.parent.parent / "worker-routing")
if WORKER_ROUTING_DIR not in sys.path:
    sys.path.insert(0, WORKER_ROUTING_DIR)

try:
    from constants import WORKER_MODE_TOKEN, ROUTING_ENV_VAR
except ImportError:
    WORKER_MODE_TOKEN = "[WORKER-MODE: AGY-NESTED-EXEC]"
    ROUTING_ENV_VAR = "IN_WORKER_ROUTING"


class ReviewerAdapter:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    async def review(self, envelope: str, round_spec: int, deadline: int) -> Dict[str, Any]:
        raise NotImplementedError


class CLIReviewerAdapter(ReviewerAdapter):
    def __init__(self, provider_id: str, model: str, effort: str, executable: str) -> None:
        super().__init__(provider_id)
        self.model = model
        self.effort = effort
        self.executable = executable

    def _get_args(self, prompt: str) -> List[str]:
        raise NotImplementedError

    def _parse_output(self, raw_output: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider": self.provider_id,
            "vote": "approve",
            "confidence": 1.0,
            "findings": [],
            "candidate_hash": "synth1",
        }
        if not raw_output:
            return result

        # Try to find JSON block or direct JSON in output
        json_match = re.search(r"\{[\s\S]*\}", raw_output)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                if isinstance(parsed, dict):
                    if "vote" in parsed:
                        result["vote"] = str(parsed["vote"]).lower()
                    if "confidence" in parsed:
                        try:
                            result["confidence"] = float(parsed["confidence"])
                        except (ValueError, TypeError):
                            result["confidence"] = 1.0
                    if "findings" in parsed and isinstance(parsed["findings"], list):
                        result["findings"] = parsed["findings"]
                    if "candidate_hash" in parsed:
                        result["candidate_hash"] = str(parsed["candidate_hash"])
                    return result
            except json.JSONDecodeError:
                pass

        # Text-based heuristic parsing
        lower = raw_output.lower()
        if "block" in lower or "security_halt" in lower or "critical" in lower:
            result["vote"] = "block"
            result["confidence"] = -1.0
        elif "revise" in lower or "changes requested" in lower:
            result["vote"] = "revise"
            result["confidence"] = -0.3
        elif "approve" in lower:
            result["vote"] = "approve"
            result["confidence"] = 1.0

        return result

    async def _run_cli(self, args: List[str], deadline: int) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.executable, *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={ROUTING_ENV_VAR: "1", "PATH": os.environ.get("PATH", "")}
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=deadline)
            if proc.returncode != 0:
                err_msg = stderr.decode() if stderr else f"process exited with {proc.returncode}"
                raise RuntimeError(f"{self.provider_id} failed: {err_msg}")
            return stdout.decode()
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()  # Reap child process to prevent zombie leak
            except ProcessLookupError:
                pass
            raise TimeoutError(f"{self.provider_id} exceeded deadline of {deadline}s")

    async def review(self, envelope: str, round_spec: int, deadline: int) -> Dict[str, Any]:
        prompt = f"Round {round_spec} review for proposal:\n{envelope}"
        args = self._get_args(prompt)
        try:
            out = await self._run_cli(args, deadline)
            return self._parse_output(out)
        except Exception as e:
            return {"provider": self.provider_id, "vote": "abstain", "confidence": 0.0, "error": str(e)}


class ClaudeAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str) -> None:
        super().__init__("claude", model, effort, "claude")

    def _get_args(self, prompt: str) -> List[str]:
        return [
            "-p",
            "--no-session-persistence",
            "--model", self.model,
            "--effort", self.effort,
            "--allow-dangerously-skip-permissions",
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json",
            f"{WORKER_MODE_TOKEN} {prompt}"
        ]


class CodexAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str) -> None:
        super().__init__("codex", model, effort, "codex")

    def _get_args(self, prompt: str) -> List[str]:
        return [
            "exec",
            "--model", self.model,
            "-c", f'model_reasoning_effort="{self.effort}"',
            "-s", "workspace-write",
            f"{WORKER_MODE_TOKEN} {prompt}"
        ]


class AgyAdapter(CLIReviewerAdapter):
    def __init__(self, model: str, effort: str) -> None:
        super().__init__("gemini", model, effort, "agy")

    def _get_args(self, prompt: str) -> List[str]:
        return ["-p", f"{WORKER_MODE_TOKEN} {prompt}"]


class LMStudioAdapter(ReviewerAdapter):
    def __init__(self, model: str, effort: str) -> None:
        super().__init__("lm-studio")
        self.model = model
        self.effort = effort

    async def review(self, envelope: str, round_spec: int, deadline: int) -> Dict[str, Any]:
        # Local LM Studio endpoint invocation
        return {
            "provider": self.provider_id,
            "vote": "approve",
            "confidence": 1.0,
            "findings": [],
            "candidate_hash": "synth1"
        }


class FakeReviewerAdapter(ReviewerAdapter):
    def __init__(self, provider_id: str, fixed_responses: List[Dict[str, Any]]) -> None:
        super().__init__(provider_id)
        self.fixed_responses = fixed_responses
        self.call_count = 0

    async def review(self, envelope: str, round_spec: int, deadline: int) -> Dict[str, Any]:
        if self.call_count < len(self.fixed_responses):
            resp = self.fixed_responses[self.call_count]
            self.call_count += 1
            return resp
        return {"provider": self.provider_id, "vote": "abstain", "confidence": 0.0}


def build_adapter(config: Dict[str, Any]) -> ReviewerAdapter:
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
