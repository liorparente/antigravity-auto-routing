# ADR 0008: Acceptance Gate Regression Scope and Anti-Ratchet Boundary

## Status
Accepted (2026-08-16)

## Context
Ticket 18 specified an acceptance gate (`acceptance_gate.evaluate_proposal`) to ensure that a learner proposal passes benchmark trials before auto-adoption, and that no scoreboard metric regresses.
In the original implementation, the gate read the scoreboard baseline before trials and current scoreboard after trials. Because the only new records written during the gate evaluation were the candidate's own probe `ReplayBenchmarkRecord`s, comparing baseline to current measured whether the candidate batch dragged down the trailing mean of earlier probe trials.

This caused two critical architectural defects:
1. **Inverted Incentive:** A historically degraded benchmark mean made subsequent candidate proposals easier to pass, while an excellent history made valid candidate proposals (e.g. scoring 0.85 against a 0.80 threshold) impossible to pass if the historical mean was 0.98.
2. **Noise Sensitivity & Conflation of Probe with Adoption:** Candidate trials are an un-adopted probe, not live system state. Conflating probe scoring with post-adoption regression detection introduced trailing window sampling noise into the deterministic per-trial threshold check.

## Decision
1. **Delineation of Responsibilities**:
   - **Pre-Adoption Acceptance Gate (Ticket 18 / `acceptance_gate.py`)**:
     - Evaluates un-adopted candidate proposals against absolute quality criteria: `threshold_met` (every trial in the probe batch individually succeeds and scores $\ge \text{score\_threshold}$).
     - Verifies fail-closed persistence: `journal_complete` is `True`.
     - Verifies non-regression of concurrent system activity: no non-benchmark scoreboard metric (discipline, critique authenticity, efficiency) regressed during the gate run (`not any(m != "mean_benchmark_score" for m in comparison.regressed)`).
     - Does *not* reject a candidate proposal solely because its probe scores differ from historical probe means in the trailing window.
   - **Post-Adoption Anti-Ratchet & Auto-Revert (Ticket 21 / `risk_tiered_application.py`)**:
     - Serves as the true system-level anti-ratchet guardian.
     - Operates on *adopted* state across real operational windows during weekly deep runs (`run_weekly_deep`).
     - Compares live post-adoption system metrics (including `mean_benchmark_score`) against the pre-adoption baseline, and automatically reverts the adopted change if performance regresses.
2. **Full Telemetry & Scoreboard Transparency**:
   - `GateDecision.comparison` continues to carry the unaltered `ScoreboardComparison`, ensuring complete visibility into all 8 metric movements (including `mean_benchmark_score`).
   - Every trial record is persisted to the journal (`learning_journal.ReplayBenchmarkRecord`), ensuring the replay benchmark trend has no silent gaps.

## Consequences
- **Positive**: Eliminates inverted incentives and trailing window sampling noise at gate time; establishes a clear, rigorous boundary between pre-adoption candidate verification and post-adoption regression rollback; preserves 100% telemetry and fail-closed persistence.
- **Negative / Trade-offs**: Ratchet detection for benchmark performance occurs during post-adoption tracking (Ticket 21) rather than pre-adoption probe evaluation. This is mitigated by automatic rollback on regression and anti-flapping guards.
