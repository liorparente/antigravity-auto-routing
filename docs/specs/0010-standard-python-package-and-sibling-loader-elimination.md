# Spec 0010 — Standard Python Package Architecture & Sibling Loader Elimination

* **Status:** ready-for-agent
* **Date:** 2026-08-20
* **Related:** Spec 0001 (Advisory Consultation), Spec 0005 (Unified Worker Invocation), Spec 0008 (Debate Engine Modular Decomposition), Spec 0009 (Unified Consultation and Council Engine), ADR 0001, ADR 0004, ADR 0007, ADR 0010
* **Glossary:** **AllowedDirectAction**, **SecurityContext**, **WorkerInvocation**, **ReviewCouncilFacade**, **LearningJournal**, **LearnedState** (`CONTEXT.md`)

---

## Problem Statement

The `skills/worker-routing/` codebase is currently structured as a flat folder of loose Python scripts rather than a standard Python package. Because the modules lack a package root and cannot rely on standard package-relative imports, eight separate modules duplicate a dynamic path-based loader function (`_load_sibling(name: str) -> Any`) backed by `importlib.util.spec_from_file_location`.

This ad-hoc module loading architecture creates four critical failure modes:
1. **Module Identity Split & Mocking Bypass:** Loading files dynamically via file paths creates distinct module instances in Python's runtime memory separate from `sys.modules`. As recorded in `ERRORS.md` and `institutional-memory.md`, unit tests attempting to patch dependencies (such as mocking `production_invoker.invoke_worker` in `test_debate_transport.py`) fail silently to intercept runtime calls, causing test suites to spawn unmocked external subprocesses that hang indefinitely.
2. **Dynamic Resolution Overhead & Indirection:** Modules are forced to rely on runtime `__getattr__` dispatch tables, `importlib` reflection, and repetitive `_modules` tuple scanning, obfuscating call hierarchies and degrading IDE static analysis and Mypy type-checking.
3. **Fragile CLI Entry Points:** Direct terminal invocations (such as running `python3 routing_check.py`) fail when relative imports are introduced naively without an explicit package anchor.
4. **Distribution Desynchronization:** The installer (`install.sh`) copies scripts individually to `~/.gemini/` and `~/.codex/` without establishing a formal package marker (`__init__.py`), risking environment discrepancies across multi-harness setups.

---

## Solution

Transform `skills/worker-routing/` into a standard, self-contained Python package:
1. Introduce a package boundary (`__init__.py`) defining the public API surface.
2. Add a repository-level `pyproject.toml` establishing project metadata and static tool configuration.
3. Eradicate all instances of `_load_sibling` across every module, replacing them with standard, clean relative imports (`from .module import ...`).
4. Implement transparent, lightweight CLI bootstrapping in entrypoint scripts (e.g. `routing_check.py`) so that existing CLI commands, audit scripts (`routing-audit.sh`), and test runners continue to function with 100% backward compatibility.
5. Update `install.sh` and `uninstall.sh` to package, stage, and clean `__init__.py` across all supported agent environments (`~/.gemini/`, `~/.codex/`, `.agents/`).
6. Validate the entire migration across 1,000+ unit tests with zero regression and verify through a multi-model Council Review panel.

---

## User Stories

1. As a developer running unit tests, I want module imports to resolve to singleton instances in `sys.modules`, so that `unittest.mock.patch` reliably intercepts calls without leaking to unmocked subprocesses.
2. As a developer running tests in CI or locally, I want all 1,010+ unit tests to pass deterministically in seconds without hanging on subprocess timeouts.
3. As an orchestrator executing routing audits from the terminal, I want `python3 routing_check.py <log>` and `routing-audit.sh` to work seamlessly without requiring modifications to shell commands or manual `PYTHONPATH` exports.
4. As a maintainer editing code, I want standard relative imports (`from .dialogue_contracts import ...`) across all internal files, so that IDEs and static type checkers (Mypy/Pyright) provide instant autocomplete, jump-to-definition, and accurate type checking.
5. As an installer script (`install.sh`), I want to copy a clean, self-contained package directory containing `__init__.py` to target project roots and global skill directories, so that all supported AI harnesses (Antigravity, Claude Code, Codex) run identical, reliable code.
6. As an uninstaller script (`uninstall.sh`), I want to cleanly remove `__init__.py` alongside other installed skill files, so that uninstallation leaves no orphaned configuration artifacts.
7. As an auditor reviewing system architecture, I want the codebase to eliminate dynamic `importlib` workarounds and `__getattr__` proxies, so that the module dependency graph is statically verifiable and transparent.
8. As a developer writing new feature tickets, I want to import shared domain types directly from the package, so that new components build upon clear, well-defined module seams without duplicating loading logic.
9. As a developer exploring the repository, I want a standard `pyproject.toml` file at the root, so that linters, formatters, and test runners recognize standard project boundaries.
10. As a reviewer evaluating code safety, I want the transition to maintain complete encapsulation and zero behavioral divergence across all dialogue, learning, and scoring engines.

---

## Implementation Decisions

### 1. Hybrid Packaging Architecture
- A dedicated `__init__.py` file is introduced inside `skills/worker-routing/`, exposing the canonical public interface of the protocol (including `ReviewCouncil`, `run_critical_dialogue`, `LearningJournal`, `LearnedState`, and core contracts).
- A root-level `pyproject.toml` is created with standard metadata, declaring package roots and tool settings without forcing mandatory external package installation for basic script usage.

### 2. Complete Sibling Loader Elimination
- Remove the `_load_sibling` function definition and all dynamic `importlib.util.spec_from_file_location` invocations from every module:
  - `advisory_consultation.py`
  - `debate_orchestrator.py`
  - `debate_state_machine.py`
  - `debate_transport.py`
  - `dialogue_transcript.py`
  - `dialogue_contracts.py`
  - `dialogue_degradation.py`
  - `prompt_assembler.py`
  - `sensitivity_redactor.py`
  - `executive_dialogue_report.py`
  - `consultation_policy.py`
  - `learning_journal.py`
  - `learned_state.py`
  - `learning_outcomes.py`
  - `learning_scoreboard.py`
  - `risk_tiered_application.py`
  - `production_invoker.py`
- Replace with direct, explicit relative imports: `from .dialogue_contracts import AdvisoryOutcome, Occasion`.

### 3. Transparent CLI Bootstrap Mechanism
- For scripts intended for direct execution via CLI (such as `routing_check.py`), implement a non-intrusive package resolution fallback:
  ```python
  if __package__ is None or __package__ == "":
      import sys
      from pathlib import Path
      sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
      __package__ = "worker-routing"
  ```
- This guarantees that both direct script execution (`python3 routing_check.py`) and module execution (`python3 -m skills.worker-routing.routing_check`) resolve internal package imports identically.

### 4. Thin Facade Simplification
- In `advisory_consultation.py`, eliminate dynamic `__getattr__` lookup loops over the `_modules` tuple. Replace with explicit static imports and re-exports, retaining complete backward compatibility for external callers while providing full static analysis clarity.

### 5. Multi-Harness Installer Synchronization
- Update `install.sh` to include `__init__.py` in the list of synchronized skill files copied to `~/.gemini/config/skills/worker-routing/`, `~/.codex/skills/worker-routing/`, and local project directories.
- Update `uninstall.sh` to remove `__init__.py` upon uninstallation.

---

## Testing Decisions

### 1. Test Philosophy & Behavioral Verification
- Tests must verify behavior through public interfaces without testing internal loader mechanisms.
- All existing unit tests in `skills/worker-routing/test_*.py` and `skills/council-review/tests/test_*.py` must pass unmodified or updated solely for clean package imports.

### 2. High-Level Test Seams
- **Seam 1: Mock Interception Integrity:** Explicitly verify that mocking `production_invoker` or `debate_state_machine` inside `test_debate_transport.py` intercepts runtime calls with zero module identity splitting.
- **Seam 2: CLI Invocation Seam:** Verify direct execution of `routing_check.py` and `routing-audit.sh` against plain text and JSONL fixture logs.
- **Seam 3: Full Suite Regression Seam:** Execute the complete 1,010+ test suite via `python3 -m unittest discover` to ensure zero regression across all state machines, transports, and journal stores.

### 3. Prior Art in Codebase
- `skills/worker-routing/test_debate_transport.py`
- `skills/worker-routing/test_production_invoker.py`
- `skills/worker-routing/test_routing.py`
- `skills/council-review/tests/test_council_review.py`

---

## Out of Scope

- Decomposing `debate_orchestrator.py` into separate sub-modules (deferred to subsequent tickets under Candidate 1).
- Modifying the underlying protocol text in `protocol.md` or `SKILL.md`.
- Altering the JSON schema or file formats of `learning_journal.py` or `.ralph/`.

---

## Further Notes

Following implementation, invoke `install.sh .` to synchronize all updated package files across active harness directories, and record learnings in `institutional-memory.md`.
