# 0004. Dynamic Local Model Routing and Judicial Consultation Policy

* Status: Accepted
* Date: 2026-08-09

## Context and Problem Statement

The system previously relied primarily on cloud worker routing for complex tasks, but lacked a formal policy for dynamically offloading tasks to local models (e.g., LM Studio) when appropriate. We need an objective, privacy-safe, and quality-calibrated policy for model selection, advisory debates, and human security escalation.

## Decision Drivers

* **Privacy and Security:** Protection of credentials, keys, and confidential code.
* **Execution Quality:** Zero-defect execution through deep thinking and objective model consensus.
* **Cost and Efficiency:** Maximizing local model usage for simple/routine work while maintaining reliability.

## Considered Options

1. Pure Cloud Routing (Status Quo)
2. Static Rule-based Local Offloading
3. Dynamic Complexity-Sensitivity Routing with Judicial Advisory Consultation and Proactive Human Alerts (Selected)

## Decision Outcome

Chosen option: **Dynamic Complexity-Sensitivity Routing with Judicial Advisory Consultation and Proactive Human Alerts**.

### Key Rules

1. **Dual Routing Criteria:** Tasks are evaluated on both technical complexity and data sensitivity.
2. **Proactive Security Gate:** Any presence or suspicion of security keys, secrets, or confidential user data triggers an immediate proactive alert to the human user for approval before continuing.
3. **Judicial Advisory Consultation:** When task complexity classification is ambiguous, the orchestrator triggers an objective bi- or tri-lateral debate loop (up to 3 rounds) between Planner and Critic models (e.g., Claude Code and Codex) to yield constructive feedback and a unified execution plan.
4. **Structured Technical Logging & Fallback:** If a local model is unavailable or fails, a structured technical bug report is logged, and execution proceeds seamlessly to the next model in the fallback chain.
5. **Full Audit Logging:** Detailed telemetry (Task ID, complexity, latency, decision rationale, outcome) is recorded continuously in the local routing log.

## Positive Consequences

* Guarantees zero leak of sensitive credentials by enforcing human pre-approval.
* Eliminates guesswork on ambiguous tasks via multi-round judicial model debate.
* Ensures full auditability of all routing decisions and fallback events.
