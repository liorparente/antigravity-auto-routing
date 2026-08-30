# 05 — Contract: Delete Shallow Facades and Verify Total Cleanliness

**What to build:** Delete `skills/worker-routing/advisory_consultation.py`, `skills/council-review/scripts/council_review.py`, and `skills/worker-routing/debate_orchestrator.py`. Verify clean linting (`ruff check`), strict static typing (`mypy`), and complete test suite execution (`python3 -m unittest test_suite.py`).

**Blocked by:** 04 — Migrate: Update Test Suites to Target CriticalDialogue

**Status:** done-with-documented-deviation

- [x] Delete `skills/worker-routing/advisory_consultation.py`
- [x] Delete `skills/council-review/scripts/council_review.py`
- [x] Delete legacy `skills/worker-routing/debate_orchestrator.py`
- [x] Run `ruff check .` with zero lint errors
- [ ] Run `mypy skills/worker-routing` with zero type errors
- [x] Run `python3 -m unittest test_suite.py` with 100% pass rate

**Verification evidence (2026-08-30):** The literal command
`.venv/bin/mypy skills/worker-routing` is structurally impossible because Mypy
rejects the hyphenated package directory (`worker-routing contains __init__.py
but is not a valid Python package name`, exit 2). The canonical CI validation
instead used a unique, automatically cleaned temporary `worker_routing` alias
to the absolute source path; Ruff passed and Mypy reported no issues across all
54 Python targets. The full unittest suite passed 1,778 tests with one skip.

**Shared entry-point TDD evidence (2026-08-30):** Before implementation,
`CiWorkflowStructuralTests.test_documentation_and_ci_use_the_same_shared_typecheck_entry_point`
failed because `skills/worker-routing/typecheck.sh` did not exist. After extracting
the canonical 54-target list and temporary-alias behavior into that public command,
the six focused CI structure/closure tests passed. The shared command then reported
zero Mypy issues in 54 source files; repository Ruff, Bash syntax, ShellCheck, and
`git diff --check origin/main` passed; the complete unittest discovery run passed
1,779 tests with one skip. README, CLAUDE.md, and CI now invoke the same type-check
entry point.

**Final architectural closure (2026-08-30):** Spec 0015's three deferred
findings were remediated with focused RED/GREEN coverage. `critical_dialogue`
now exposes exactly its three declared entry points and rejects public leaf
pass-through aliases; package and subprocess imports contain no executable
`sys.path` mutation and copied installations smoke-test the standalone adapter
path; credential classification is owned exclusively by
`sensitivity_redactor`. The provider adapters moved under their owning
`worker_routing` package. Final QA passed 1,783 tests with one skip, Mypy passed
all 54 canonical targets, Ruff/Bash/ShellCheck/diff checks were clean, and the
independent Standards and Spec 0015 reviews both returned zero findings.
