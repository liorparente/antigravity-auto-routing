#!/usr/bin/env bash
set -euo pipefail

# The canonical Python target list used by both Ruff and Mypy in CI.
PYTHON_MODULES=(
  skills/worker-routing/__init__.py
  skills/worker-routing/routing_check.py
  skills/worker-routing/test_routing.py
  test_suite.py
  skills/worker-routing/agent_council.py
  skills/worker-routing/critical_dialogue.py
  skills/worker-routing/test_critical_dialogue.py
  skills/worker-routing/dialogue_contracts.py
  skills/worker-routing/dialogue_degradation.py
  skills/worker-routing/executive_dialogue_report.py
  skills/worker-routing/test_executive_dialogue_report.py
  skills/worker-routing/dialogue_transcript.py
  skills/worker-routing/prompt_assembler.py
  skills/worker-routing/test_prompt_assembler.py
  skills/worker-routing/sensitivity_redactor.py
  skills/worker-routing/test_sensitivity_redactor.py
  skills/worker-routing/debate_state_machine.py
  skills/worker-routing/consultation_policy.py
  skills/worker-routing/debate_transport.py
  skills/worker-routing/test_debate_orchestrator.py
  skills/worker-routing/test_debate_state_machine.py
  skills/worker-routing/test_debate_transport.py
  skills/worker-routing/test_dialogue_contracts.py
  skills/worker-routing/test_dialogue_degradation.py
  skills/worker-routing/test_dialogue_transcript.py
  skills/worker-routing/learning_journal.py
  skills/worker-routing/production_invoker.py
  skills/worker-routing/test_production_invoker.py
  skills/worker-routing/learning_outcomes.py
  skills/worker-routing/test_lmstudio.py
  skills/worker-routing/learning_scoreboard.py
  skills/worker-routing/test_learning_scoreboard.py
  skills/worker-routing/learning_report.py
  skills/worker-routing/test_learning_report.py
  skills/worker-routing/learning_report_html.py
  skills/worker-routing/test_learning_report_html.py
  skills/worker-routing/acceptance_gate.py
  skills/worker-routing/test_acceptance_gate.py
  skills/worker-routing/learned_state.py
  skills/worker-routing/test_learned_state.py
  skills/worker-routing/risk_tiered_application.py
  skills/worker-routing/test_risk_tiered_application.py
  skills/worker-routing/learner_worker.py
  skills/worker-routing/test_learner_worker.py
  skills/worker-routing/test_declarative_schema.py
  skills/worker-routing/routing_config.py
  skills/worker-routing/test_routing_config.py
  skills/worker-routing/probe_models.py
  skills/worker-routing/test_probe_models.py
  skills/worker-routing/switch_profile.py
  skills/worker-routing/test_switch_profile.py
  skills/worker-routing/regenerate_institutional_memory.py
  skills/worker-routing/provider_adapters.py
  skills/council-review/tests/test_council_review.py
)

if [[ "${1:-}" == "--print-targets" ]]; then
  printf '%s\n' "${PYTHON_MODULES[@]}"
  exit 0
fi

if (( $# != 0 )); then
  printf 'usage: %s [--print-targets]\n' "$0" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TYPECHECK_PARENT="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
TYPECHECK_DIR="$(mktemp -d "${TYPECHECK_PARENT%/}/worker-routing-mypy.XXXXXX")"
trap 'rm -rf "$TYPECHECK_DIR"' EXIT
ln -s "${REPO_ROOT}/skills/worker-routing" "${TYPECHECK_DIR}/worker_routing"

MYPY_TARGETS=()
for target in "${PYTHON_MODULES[@]}"; do
  case "$target" in
    skills/worker-routing/*)
      MYPY_TARGETS+=("${TYPECHECK_DIR}/worker_routing/${target#skills/worker-routing/}")
      ;;
    *)
      MYPY_TARGETS+=("$target")
      ;;
  esac
done

cd "$REPO_ROOT"
mypy --config-file pyproject.toml "${MYPY_TARGETS[@]}"
