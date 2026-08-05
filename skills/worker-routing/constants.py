#!/usr/bin/env python3
"""Shared constants for the worker routing protocol.

Single source of truth for the nested-worker short-circuit env var and the
exact prompt-injection warning text, so `protocol.md`, `provider_adapters.py`,
and the test suites never drift from each other via copy-pasted literals.
"""
from __future__ import annotations

ROUTING_ENV_VAR = "ROUTING_DEPTH"

NESTED_WORKER_WARNING = (
    "CRITICAL: You are running as a nested worker (ROUTING_DEPTH=1). "
    "Execute directly. Do not route this to another agent."
)
