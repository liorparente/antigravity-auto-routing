#!/usr/bin/env python3
"""Backward-compatible, ultra-slim facade for CriticalDialogue."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _load_sibling(name: str) -> Any:
    try:
        return __import__(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Keep the historic flat API while the implementation lives in focused modules.
_debate_orchestrator = _load_sibling("debate_orchestrator")
_prompt_assembler = _load_sibling("prompt_assembler")
_sensitivity_redactor = _load_sibling("sensitivity_redactor")
_executive_dialogue_report = _load_sibling("executive_dialogue_report")
_dialogue_degradation = _load_sibling("dialogue_degradation")
_dialogue_contracts = _load_sibling("dialogue_contracts")
_dialogue_transcript = _load_sibling("dialogue_transcript")

_modules = (
    _debate_orchestrator, _prompt_assembler, _sensitivity_redactor,
    _executive_dialogue_report, _dialogue_degradation, _dialogue_contracts,
    _dialogue_transcript,
)

# Bind the execution entry points explicitly for static callers and type checkers.
run_advisory_consultation_debate = _debate_orchestrator.run_advisory_consultation_debate
run_debate_loop = _debate_orchestrator.run_debate_loop
run_canary_dialogue = _debate_orchestrator.run_canary_dialogue
run_post_mortem_loop = _debate_orchestrator.run_post_mortem_loop
def dispatch_post_mortem_consultation(*args: Any, **kwargs: Any) -> Any:
    """Compatibility wrapper preserving patches to the historic facade API."""
    _debate_orchestrator.run_advisory_consultation_debate = run_advisory_consultation_debate
    return _debate_orchestrator.dispatch_post_mortem_consultation(*args, **kwargs)

def __getattr__(name: str) -> Any:
    for module in _modules:
        try:
            return getattr(module, name)
        except AttributeError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__() -> list[str]:
    return sorted(set(globals()) | {key for module in _modules for key in vars(module)})
