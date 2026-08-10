# 0003. Per-Step Evaluator Decoupling

* Status: accepted
* Date: 2026-08-08

## Context and Problem Statement

Policy checking logic (command safety, worker credit, code write detection, binding issues) was duplicated across `compute_metrics` and `RoutingAuditEngine.audit()`. Furthermore, evaluation logic mutated global accumulators directly.

## Decision Drivers

* High testability (enabling isolated unit tests for individual conversation steps).
* High leverage (a single deep evaluator module serving both metrics and audit reporting).
* Immutability and zero side-effects during single-step evaluation.

## Considered Options

1. **Isolated Step Analysis Record (`StepAnalysis`):** `_analyze_step(step, policy_context)` evaluates a single step in isolation and returns an immutable `StepAnalysis` value object.
2. **Mutating Global State:** Pass global lists and accumulators into the step evaluator function to be mutated in-place.

## Decision Outcome

Chosen option: **Option 1 (Isolated Step Analysis Record)**.

### Positive Consequences

* Single-step policy rules are concentrated in one deep function.
* `_analyze_step` becomes pure and trivial to unit-test.
* Aggregation of metrics becomes a simple map/reduce operation over `StepAnalysis` records.

### Negative Consequences

* Small allocation overhead for `StepAnalysis` objects per step (negligible for typical audit log sizes).
