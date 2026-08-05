import asyncio
import time
import uuid
import yaml
import socket
from pathlib import Path
import litellm

# Explicitly use the storage module for data types and db ops
import storage

JUDGE_MODEL = "claude-opus-5"
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"

class Evaluator:
    """
    Deep Module for evaluating AI models via benchmarks and storing results.
    Exposes a clean `run_suite` interface.
    """
    def __init__(self, benchmarks_dir: str = "benchmarks"):
        self.benchmarks_dir = Path(benchmarks_dir)
        storage.init_db()

    def _is_lm_studio_available(self) -> bool:
        """Check if LM Studio is running locally on port 1234."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(("127.0.0.1", 1234))
                return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                return False

    async def _run_benchmark(self, model: str, tier: str, task: dict) -> storage.EvaluationResult:
        # Inject UUID to bypass any API-side caching for accurate latency measurement
        prompt = task['prompt'] + f"\n\n[UUID: {uuid.uuid4()}]"
        
        start_time = time.time()
        first_token_time = None
        token_count = 0
        full_response = ""
        error_msg = None
        success = False
        score = 0.0
        
        try:
            # Detect local model request
            kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True}
            if model.startswith("local/"):
                if not self._is_lm_studio_available():
                    raise ConnectionError("LM Studio is not available on 127.0.0.1:1234")
                kwargs["api_base"] = LM_STUDIO_URL
                kwargs["model"] = "openai/" + model.split("local/")[1]
            
            response = await litellm.acompletion(**kwargs)
            
            async for chunk in response:
                if first_token_time is None:
                    first_token_time = time.time()
                
                # Using dict.get() instead of chaining attributes to prevent Message Chains smell
                delta = getattr(chunk.choices[0], "delta", None)
                if delta:
                    content = getattr(delta, "content", "") or ""
                    full_response += content
                    token_count += 1
                
            end_time = time.time()
            
            ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else 0
            tps = token_count / (end_time - first_token_time) if first_token_time and end_time > first_token_time else 0
            
            cost_usd = 0.0
            if not model.startswith("local/"):
                cost_usd = litellm.cost_calculator.completion_cost(
                    completion_response=litellm.ModelResponse(
                        model=model,
                        usage=litellm.Usage(prompt_tokens=len(prompt)//4, completion_tokens=token_count)
                    )
                ) or 0.0

            # Scoring
            if tier == "low":
                success = task['expected_format'] in full_response.lower()
                score = 100.0 if success else 0.0
            else:
                # LLM as a judge using Claude 3.5 Sonnet
                judge_prompt = f"Evaluate this response for correctness based on the prompt.\nPrompt: {prompt}\nResponse: {full_response}\nScore 0-100. Return only the number."
                judge_res = await litellm.acompletion(model=JUDGE_MODEL, messages=[{"role": "user", "content": judge_prompt}])
                
                msg = getattr(judge_res.choices[0], "message", None)
                score_str = getattr(msg, "content", "").strip() if msg else ""
                
                try:
                    score = float(score_str)
                    success = score >= 80.0
                except ValueError:
                    score = 0.0
                    success = False

        except Exception as e:
            error_msg = str(e)
            ttft_ms = 0
            tps = 0
            cost_usd = 0

        return storage.EvaluationResult(
            model=model,
            tier=tier,
            task_id=task.get('id', 'unknown'),
            ttft_ms=ttft_ms,
            tps=tps,
            cost_usd=cost_usd,
            success=success,
            score=score,
            error_msg=error_msg
        )

    async def run_suite_async(self):
        if not self.benchmarks_dir.exists():
            raise FileNotFoundError(f"Benchmarks directory not found: {self.benchmarks_dir}")

        tasks_to_run = []
        for file in self.benchmarks_dir.glob("*.yaml"):
            tier = file.stem
            with open(file) as f:
                tasks = yaml.safe_load(f)
                
                # Setup models to evaluate
                models = ["local/qwen3-coder-30b"] if self._is_lm_studio_available() else []
                if tier == "low":
                    models.extend(["gemini/gemini-1.5-flash"])
                else:
                    models.extend(["gemini/gemini-1.5-pro"])
                
                for model in models:
                    for task in tasks:
                        tasks_to_run.append((model, tier, task))
        
        sem = asyncio.Semaphore(5)
        
        async def bounded_run(model, tier, task):
            async with sem:
                result = await self._run_benchmark(model, tier, task)
                storage.log_run(result)
                return result
                
        results = await asyncio.gather(*(bounded_run(m, t, tk) for m, t, tk in tasks_to_run))
        return results

    def run_suite(self, output: str = "report.md"):
        """Public synchronous interface for the Evaluator."""
        results = asyncio.run(self.run_suite_async())
        
        # Trigger report generation
        import router_config_generator
        router_config_generator.generate_config(output)
        
        return results

if __name__ == "__main__":
    evaluator = Evaluator()
    evaluator.run_suite(output="model_evaluator_report.md")
