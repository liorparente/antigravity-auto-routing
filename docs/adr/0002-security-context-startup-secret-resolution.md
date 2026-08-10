# 0002. Security Context Startup Secret Resolution

* Status: accepted
* Date: 2026-08-08

## Context and Problem Statement

Previously, `HMACValidator` and helper functions like `calibration_signature_issue` loaded calibration secrets on-demand from disk or required passing `root_dir` through multiple layers of evaluation functions (`compute_metrics`). This degraded code locality and introduced pass-through adapters that failed the deletion test.

## Decision Drivers

* High code locality (avoiding parameter pollution through unrelated functions).
* Fast, predictable in-memory signature verification without redundant disk reads per step.
* Decoupling secret resolution from per-step log metric calculations.

## Considered Options

1. **Startup Resolution (SecurityContext):** Load the calibration secret once during initialization into an immutable `SecurityContext` instance and pass it directly to the engine/evaluator.
2. **On-Demand Resolution:** Continue resolving secrets dynamically from disk on each step verification call.

## Decision Outcome

Chosen option: **Option 1 (Startup Resolution)**.

### Positive Consequences

* Removes `root_dir` parameter pollution from `compute_metrics` and per-step verification routines.
* Eliminates the pass-through `HMACValidator` class.
* Improves execution performance by resolving keys once at entry.

### Negative Consequences

* Dynamic secret rotation during a single audit run is not supported without re-instantiating `SecurityContext` (acceptable given log audit runs are short-lived execution scripts).
