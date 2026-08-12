#!/usr/bin/env python3
"""End-to-end smoke tests for an LM Studio OpenAI-compatible server.

Run with: python3 test_lmstudio.py
Override defaults with LMSTUDIO_BASE_URL and LMSTUDIO_MODEL if needed.
"""

import json
import os
import unittest
from urllib.error import URLError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/")
MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen3-coder-next-mlx")
TIMEOUT_SECONDS = 120


def post_chat(messages, *, stream=False, temperature=0):
    """Call /chat/completions and return JSON, or an open SSE response if streaming."""
    payload = json.dumps(
        {
            "model": MODEL,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
    ).encode("utf-8")
    request = Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        response = urlopen(request, timeout=TIMEOUT_SECONDS)
    except URLError as error:
        raise AssertionError(
            f"LM Studio is unreachable at {BASE_URL}: {error}. "
            "Start its local server and load the configured model."
        ) from error
    if stream:
        return response
    with response:
        return json.loads(response.read().decode("utf-8"))


def completion_text(messages):
    response = post_chat(messages)
    return response["choices"][0]["message"]["content"].strip()


class LMStudioAPITests(unittest.TestCase):
    """Verify the local model responds correctly through all required API paths."""

    def test_1_health_check_ping_returns_ready(self):
        reply = completion_text(
            [{"role": "user", "content": "Reply with exactly: READY"}]
        )
        self.assertEqual(reply, "READY")

    def test_2_code_generation_returns_is_prime_function(self):
        reply = completion_text(
            [
                {
                    "role": "user",
                    "content": (
                        "Write only Python code for a function named is_prime(n) that returns "
                        "True exactly when integer n is prime."
                    ),
                }
            ]
        )
        self.assertIn("def is_prime", reply)
        self.assertIn("return", reply)

    def test_3_system_prompt_adherence(self):
        reply = completion_text(
            [
                {
                    "role": "system",
                    "content": "You must answer every request with exactly the word BLUE.",
                },
                {"role": "user", "content": "What color is the sky?"},
            ]
        )
        self.assertEqual(reply, "BLUE")

    def test_4_streaming_response(self):
        response = post_chat(
            [{"role": "user", "content": "Reply with exactly: STREAM_OK"}], stream=True
        )
        chunks = []
        with response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                event = json.loads(data)
                chunks.append(event["choices"][0].get("delta", {}).get("content", ""))
        self.assertEqual("".join(chunks).strip(), "STREAM_OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
