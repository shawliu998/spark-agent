#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE="$ROOT/services/science-core"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

run_core_group() {
  local label="$1"
  shift
  printf '\n==> Agent evaluation / %s\n' "$label"
  (cd "$CORE" && "$PYTHON_BIN" -m pytest -q "$@")
}

run_core_group "Outcome" \
  tests/test_agent_runs.py::test_autonomous_local_literature_completes_through_durable_agent_loop \
  tests/test_agent_runs.py::test_confirmed_method_revision_creates_a_new_plan_with_existing_approvals \
  tests/test_agent_runs.py::test_rejected_method_revision_does_not_change_spec_or_generate_plan \
  tests/test_agent_loop_policy.py::test_complete_requires_every_invariant \
  tests/test_agent_loop_policy.py::test_literature_complete_requires_verified_frozen_result

run_core_group "Process" \
  tests/test_agent_loop_coordinator.py::test_remote_decision_crash_is_recovered_without_repeating_model_request \
  tests/test_agent_loop_coordinator.py::test_recovery_enqueues_one_observation_for_terminal_source \
  tests/test_agent_loop_coordinator.py::test_recovery_does_not_repeat_permanent_control_failure \
  tests/test_agent_loop_coordinator.py::test_recovery_caps_transient_control_failure_attempts \
  tests/test_agent_loop_policy.py::test_global_hard_limit_forces_stop \
  tests/test_agent_runs.py::test_literature_failure_enters_agent_loop_without_worker_restart \
  tests/test_agent_runs.py::test_pending_remote_invocation_recovers_without_a_second_model_call

run_core_group "Trust" \
  tests/test_agent_runs.py::test_literature_complete_revalidates_every_frozen_source_at_apply \
  tests/test_agent_runs.py::test_method_revision_resolution_rejects_tampered_observation \
  tests/test_agent_runs.py::test_confirmed_method_revision_apply_revalidates_immutable_inputs \
  tests/test_agent_runs.py::test_remote_gateway_approval_mismatch_never_starts_an_invocation \
  tests/test_intent_router.py::test_prompt_injection_remains_untrusted_and_cannot_select_fake_source \
  tests/test_intent_router.py::test_clarification_prompt_is_independent_and_treats_content_as_untrusted

printf '\n==> Agent evaluation / Citation and malicious-export trust\n'
pnpm --dir "$ROOT" --filter @ai4s/desktop test -- --run \
  researchReportExport CompetitiveResearchWorkspace agentLoopContract

printf '\n<== PASS: Agent v1.2 Outcome / Process / Trust evaluation\n'
