# 03 — Autonomous Planner-Critic Debate Engine & Interactive Resolution

**What to build:** An autonomous multi-round debate loop (up to 3 rounds) between a Planner model and a Critic model for complex architectural tasks, which generates a visual trade-off comparison matrix and an interactive decision prompt if a stalemate occurs.

**Blocked by:** 02 — Dynamic Model Registry, Effort Escalation & Fail-Safe Recovery

**Status:** ready-for-agent

- [ ] Autonomous debate protocol coordinates round-by-round exchange between Planner and Critic models
- [ ] Debate logs are persisted under `.scratch/planning_debate.md` with explicit consensus validation
- [ ] Stalemates reaching 3 rounds trigger an automatic halt and generate a visual trade-off comparison table
- [ ] Interactive prompt schema presents key points of disagreement with one-click decision options for human resolution
- [ ] Full unit test coverage in `test_routing.py` verifying debate round transitions, consensus detection, and stalemate handling
