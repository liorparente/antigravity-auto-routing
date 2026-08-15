# ADR 0007: Council Review Dynamic Weighting, Soft Confidence Scoring, and Unilateral Security Veto

## Status
Accepted (2026-08-15)

## Context
The auto-routing system requires a reliable multi-agent peer review mechanism to evaluate implementation plans and code diffs before execution. 
We initially evaluated a tri-model panel composed of Claude Opus 5 (45%), Codex 5.6 Sol (45%), and Gemini 3.1 Pro (10%).

Game-theoretic analysis (using the Banzhaf Power Index) demonstrated that under naive binary majority voting ($q = 0.50$), $(0.45, 0.45, 0.10)$ collapses mathematically to equal $(1/3, 1/3, 1/3)$ voting power, while under supermajority ($q \ge 0.60$), Gemini becomes a 0% power dummy player.
Furthermore, in software security, a majority of lenient models must never override a genuine security vulnerability detected by a single reviewer.

## Decision
1. **Tri-Model Roster & Baseline Weights**:
   - Panel: Claude Opus 5 (Thinking), OpenAI Codex 5.6 Sol, Google Gemini 3.1 Pro (High).
   - Initial weights: `{"claude": 0.40, "codex": 0.40, "gemini": 0.20}` with dynamic weight bounds `[0.05, 0.65]` and quorum threshold $0.60$.
2. **Continuous Soft-Confidence Scoring**:
   - Replaced binary pass/fail voting with continuous confidence scores $s_i \in [-1.0, +1.0]$.
   - Applied an asymmetric loss multiplier ($1.5\times$ on negative scores) to heavily penalize approvals of broken or vulnerable code.
3. **Unilateral Security Veto**:
   - Any single model detecting a Critical or High severity threat with verified locus immediately triggers a `SECURITY_HALT`, overriding weighted majority approval.
4. **Local-Only Privacy Enforcement**:
   - When `privacy_mode="local-only"` is requested, cloud providers are bypassed completely, and review is executed strictly via local LM Studio adjudicators (Fail-Closed).

## Consequences
- **Positive**: Eliminates single-model blind spots, avoids the Banzhaf voting quota paradox, guarantees fail-closed security for high-risk vulnerabilities, and enables privacy-safe local review.
- **Negative / Trade-offs**: Slightly increased token latency on complex architectural reviews (up to 3 parallel CLI runs). Mitigated by asynchronous execution and automated caching.
