# 04 — Spec vs Diff Audit Engine & Local Issue Tracking (`.scratch/issues/`)

**What to build:** Post-execution audit module that parses completed `git diff` output, compares modified files against the mission brief, alerts on unauthorized scope creep, and creates local `.scratch/issues/` Markdown tickets (with optional GitHub Issues sync).

**Blocked by:** 03 — Autonomous Planner-Critic Debate Engine & Interactive Resolution

**Status:** ready-for-agent

- [ ] Audit engine analyzes `git diff` against task specification goals and constraints upon completion
- [ ] Unauthorized modifications trigger a warning alert and log detailed scope creep findings
- [ ] Scope creep issues generate formatted Markdown tickets under `.scratch/issues/<NN>-<slug>.md`
- [ ] Optional sync adapter supports pushing generated scope creep tickets to GitHub Issues
- [ ] Full unit test coverage in `test_routing.py` verifying audit diff parsing, warning generation, and ticket creation
