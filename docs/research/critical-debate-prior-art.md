# Prior Art: Genuinely Critical Model Debate

* Ticket: 02 of wayfinder map "self-improving-orchestrator" (research; feeds ticket 04)
* Date: 2026-08-11
* Local anchor: `docs/specs/0001-advisory-consultation.md` — the shipped Planner–Critic loop (verdict-line
  contract, 3-round cap, fail-closed parsing). Its remaining fear is **false consensus**: a Critic
  "approve" that reflects no engagement. This survey collects what primary sources establish about
  making inter-model critique genuinely adversarial, and about detecting rubber-stamping.

All citations are to the papers' own arXiv pages (abstract or full text), not secondary summaries.

## 1. Multi-agent debate: measured gains and limits

- The foundational result: multiple model instances that "propose and debate their individual responses
  and reasoning processes over multiple rounds" significantly improve mathematical/strategic reasoning
  and factual validity, reducing hallucinations — with one identical procedure across tasks.
  ("Improving Factuality and Reasoning in Language Models through Multiagent Debate", Du et al. —
  https://arxiv.org/abs/2305.14325)
- Debate helps weak judges supervise strong debaters: non-expert LLM judges reached 76% accuracy (48%
  naive baseline) and human judges 88% (60% baseline) on QuALITY comprehension questions; optimizing
  debaters for *persuasiveness* — not truth — still improved judge accuracy. ("Debating with More
  Persuasive LLMs Leads to More Truthful Answers", Khan et al. — https://arxiv.org/abs/2402.06782)
- Critical follow-up 1: across debate protocols, "multi-agent debating systems, in their current form,
  do not reliably outperform" self-consistency and ensembling; protocols are hyperparameter-sensitive.
  The exception: *modulating agent agreement levels* (making agents less agreeable) can surpass all
  non-debate protocols tested. ("Should we be going MAD? A Look at Multi-Agent Debate Strategies for
  LLMs", Smit et al. — https://arxiv.org/abs/2311.17371)
- Critical follow-up 2: a systematic evaluation of five MAD frameworks on nine benchmarks and four
  foundation models found MAD fails to reliably beat Chain-of-Thought and Self-Consistency even at
  higher inference cost — but *model heterogeneity* (Heter-MAD: agents consulting output from a
  different foundation-model family) consistently boosts MAD. ("If Multi-Agent Debate is the Answer,
  What is the Question?" — https://arxiv.org/abs/2502.08788)
- Boundary condition motivating a second model at all: LLMs "struggle to self-correct their responses
  without external feedback, and at times, their performance even degrades after self-correction" —
  intrinsic self-review is not a critique mechanism. ("Large Language Models Cannot Self-Correct
  Reasoning Yet", Huang et al. — https://arxiv.org/abs/2310.01798)

Net: debate is not free quality. Its measured value concentrates where there is genuine disagreement,
asymmetric information (judge weaker than debaters), heterogeneous models, and tuned agreeableness.

## 2. Failure modes: sycophancy, conformity, degeneration-of-thought, echo chambers

- **Degeneration-of-Thought (DoT):** once a model "has established confidence in its solutions, it is
  unable to generate novel thoughts later through reflection even if its initial stance is incorrect."
  The MAD framework's fix is structural: two debaters in a tit-for-tat state plus a judge, with *modest*
  adversarial intensity working best. ("Encouraging Divergent Thinking in Large Language Models through
  Multi-Agent Debate", Liang et al. — https://arxiv.org/abs/2305.19118)
- **Sycophancy is trained-in:** five state-of-the-art assistants "consistently exhibit sycophancy"
  across free-form tasks; humans and preference models "prefer convincingly-written sycophantic
  responses over correct ones a non-negligible fraction of the time", so preference optimization itself
  pushes models toward agreement over accuracy. ("Towards Understanding Sycophancy in Language Models",
  Sharma et al. — https://arxiv.org/abs/2310.13548)
- **Capitulation under invalid pressure:** ChatGPT-class models "cannot maintain their beliefs in truth
  for a significant portion of examples when challenged by oftentimes absurdly invalid arguments" —
  models that solved a task correctly abandon the answer when merely contradicted. ("Can ChatGPT Defend
  its Belief in Truth?", Wang, Yue & Sun — https://arxiv.org/abs/2305.13160)
- **Conformity compounds per round:** BenchForm (ICLR 2025) measures conformity/independence rates in
  multi-agent interaction: average conformity 23.5–47.2% across misleading protocols; conformity *rises
  with interaction length* (Llama3-70B: 33.9% at 1 round → 44.4% at 5 rounds) and rises even faster
  with majority size. Mitigations that measurably help: an empowered ("stay independent") persona and a
  reflection/double-check step (doubt-protocol conformity 69.9% → 35.2% for Llama3-70B). ("Do as We Do,
  Not as You Think: the Conformity of Large Language Models", Weng et al. —
  https://arxiv.org/abs/2501.13381)
- **Echo chamber / false consensus:** in multiagent debate, agents converge to a single shared answer,
  and that consensus is not always correct — agents can confidently affirm a wrong consensus; slower
  convergence (via prompt design) produced *better* final consensus. (Du et al. —
  https://arxiv.org/abs/2305.14325)

## 3. Judge/critic biases; heterogeneous rosters as mitigation

- The LLM-as-judge foundation paper names the three canonical judge biases — **position bias, verbosity
  bias, self-enhancement bias** — while showing GPT-4-class judges can still reach >80% agreement with
  human preference. ("Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", Zheng et al. —
  https://arxiv.org/abs/2306.05685)
- Position bias is decisive, not cosmetic: rankings "can be easily hacked by simply altering their order
  of appearance" — reordering alone made Vicuna-13B beat ChatGPT on 66/80 queries under a GPT-4 judge.
  Mitigations: Multiple Evidence Calibration (rationale *before* verdict), Balanced Position Calibration
  (evaluate both orders and aggregate), human-in-the-loop on high-entropy cases. ("Large Language Models
  are not Fair Evaluators", Wang et al. — https://arxiv.org/abs/2305.17926)
- Self-preference has a mechanism: frontier models recognize their own generations with non-trivial
  accuracy, and fine-tuning shows a "linear correlation between self-recognition capability and the
  strength of self-preference bias" — evaluators favor text they identify as their own. ("LLM Evaluators
  Recognize and Favor Their Own Generations", Panickssery et al. — https://arxiv.org/abs/2404.13076)
- Bias is pervasive at scale: CoBBLEr found bias indicators (order, egocentric/self-preference,
  bandwagon, etc.) in ~40% of evaluator comparisons, with only ~49.6% rank overlap with humans.
  ("Benchmarking Cognitive Biases in Large Language Models as Evaluators", Koo et al. —
  https://arxiv.org/abs/2309.17012)
- Judges skew lenient: a 13-judge study found only the largest judges reasonably aligned, systematic "a
  tendency toward leniency", and that high percent-agreement can mask large score divergence — so
  approval rates alone overstate quality. ("Judging the Judges", Thakur et al. —
  https://arxiv.org/abs/2406.12624)
- Twelve judge biases can be quantified automatically by principle-guided perturbation of inputs and
  checking verdict stability (CALM framework). ("Justice or Prejudice? Quantifying Biases in
  LLM-as-a-Judge", Ye et al. — https://arxiv.org/abs/2410.02736)
- **Heterogeneous rosters as mitigation:** a Panel of LLM evaluators drawn from *disjoint model
  families* outperformed a single large judge across six datasets, exhibited less intra-model bias, and
  cost over seven times less. ("Replacing Judges with Juries", Verga et al. —
  https://arxiv.org/abs/2404.18796) Heter-MAD independently found cross-family heterogeneity boosts
  debate frameworks (https://arxiv.org/abs/2502.08788). Caveat from MAD: with mixed-family rosters the
  judge itself is not neutral — using different LLMs for debaters vs judge produced unfair evaluation —
  so the adjudicator's own family bias must be controlled, not assumed away.
  (Liang et al. — https://arxiv.org/abs/2305.19118)

## 4. Topologies and round counts

- **Pair + adjudicator (2 debaters, 1 judge)** is the canonical adversarial shape: MAD's tit-for-tat
  pair with a judge and adaptive termination (Liang et al. — https://arxiv.org/abs/2305.19118); Khan et
  al. run assigned-opposite-stance debaters over three rounds before a weaker judge rules
  (https://arxiv.org/abs/2402.06782).
- **Panel (multi-referee):** ChatEval's referee team beats a single evaluator *only* with diverse role
  prompts — identical role prompts collapsed accuracy to single-agent level (53.8% vs 60.0% with
  diverse roles); sequential one-by-one communication (60%) beat simultaneous-talk variants (55%).
  ("ChatEval", Chan et al. — https://arxiv.org/abs/2308.07201)
- **Society-of-agents consensus:** accuracy rises monotonically with agent count, but rounds saturate —
  "additional debate rounds above four led to a similar final performance to 4 rounds"; most gain is in
  the first rounds. (Du et al. — https://arxiv.org/abs/2305.14325)
- **More rounds can actively hurt:** interaction rounds worsen conformity (BenchForm, 1→5 rounds above —
  https://arxiv.org/abs/2501.13381); Khan et al. found *identical* judge accuracy between static and
  interactive debate for human judges, and interactive debate performed *worse* for LLM judges
  (https://arxiv.org/abs/2402.06782); scaling call count in Vote/Filter-Vote systems is non-monotone —
  more calls help easy queries and hurt hard ones. ("Are More LLM Calls All You Need?", Chen et al. —
  https://arxiv.org/abs/2403.02419)

Net: a 2-model pair (or pair + third-family adjudicator) with ≤3–4 rounds captures nearly all measured
benefit; value per round comes from forced role/stance divergence, not from more talking.

## 5. Verdict-contract design: formats that force engagement

Spec 0001's contract (verdict line first; unparseable ⇒ not approved) is a sound floor. Primary sources
support several strengthenings:

- **Rationale before verdict:** Multiple Evidence Calibration — the evaluator must generate supporting
  evidence/reasoning *before* emitting the score — measurably reduces position-driven snap verdicts.
  (Wang et al. — https://arxiv.org/abs/2305.17926) MT-Bench likewise uses chain-of-thought and
  reference-guided judging to counter limited judge reasoning. (Zheng et al. —
  https://arxiv.org/abs/2306.05685)
- **Evidence citation with mechanical verification:** Khan et al.'s debaters must quote the source in
  `<quote>` tags; a tool re-checks each quote against the text and relabels it verified/unverified, and
  judges are told to trust only verified quotes. Stronger debaters used more verified quotes; the most
  common failure was the *correct* side choosing quotes poorly. This is a checkable engagement signal,
  not a stylistic one. (https://arxiv.org/abs/2402.06782)
- **Critique as atomic, scoreable claims:** MetaCritique decomposes a critique into Atomic Information
  Units and scores critique *precision* (are its claims factual) and *recall* (does it cover what a
  reference critique covers), aggregated into F1 — and shows higher-scoring critiques lead to better
  refinements. A critique format that yields extractable AIUs is therefore directly measurable. ("The
  Critique of Critique", Sun et al. — https://arxiv.org/abs/2401.04518)
- **Critique is a distinct, trainable skill:** CriticBench separates generation/critique/correction
  (GQC) and finds critique capability correlates with but lags generation, and improves markedly with
  critique-focused training — do not assume a strong Planner-model makes a strong Critic. (Lin et al. —
  https://arxiv.org/abs/2402.14809)
- **Feedback must be consumable:** Self-Refine's loop (feedback → refine, with feedback prompted to be
  specific and actionable) yields ~20% absolute average improvement across seven tasks — the revision
  path needs critique the Planner can act on, not a bare verdict. (Madaan et al. —
  https://arxiv.org/abs/2303.17651)

## 6. Rubber-stamp detection: measurable signals of non-engagement

Signals with primary-source grounding, composable into telemetry:

1. **Capitulation probe (flip-under-challenge):** re-challenge a verdict with a content-free or invalid
   counterargument; a genuine critic holds, a sycophant flips. Models measurably abandon correct
   positions under absurd challenges (https://arxiv.org/abs/2305.13160) and sycophancy is systematic in
   preference-trained assistants (https://arxiv.org/abs/2310.13548).
2. **Order/position-swap consistency:** re-run the comparison with positions swapped; verdicts that
   follow position rather than content indicate no engagement. Reordering alone flipped 66/80 outcomes
   under a GPT-4 judge (https://arxiv.org/abs/2305.17926); CALM generalizes this into automated
   perturb-and-check bias quantification (https://arxiv.org/abs/2410.02736).
3. **Seeded-flaw canaries (leniency measurement):** score the Critic on artifacts with known-planted
   defects; approval of a known-broken plan is direct rubber-stamp evidence. Judges show a measured
   leniency tendency (https://arxiv.org/abs/2406.12624), and CriticBench's protocol — evaluating
   critics against solutions whose correctness is known — is the template
   (https://arxiv.org/abs/2402.14809).
4. **Critique informativeness:** count verified quotes/references to the actual plan
   (https://arxiv.org/abs/2402.06782) and extractable atomic critique claims with their
   precision/recall (https://arxiv.org/abs/2401.04518). A bare "approve" carries zero AIUs and zero
   verified references — quantifiably indistinguishable from no review.
5. **Conformity/independence tracking:** BenchForm's conformity rate and independence rate, and their
   growth across rounds, are drop-in metrics for logging how often the Critic simply adopts the
   Planner's position over time (https://arxiv.org/abs/2501.13381). CoBBLEr's bandwagon/egocentric
   probes cover the panel case (https://arxiv.org/abs/2309.17012).
6. **Consensus ≠ correctness prior:** converged agreement can be confidently wrong (Du et al. —
   https://arxiv.org/abs/2305.14325), so "agreed in round 1" should be treated as a *flag to inspect*
   (spec 0001, user story 9), never as evidence of quality by itself.

## Implications for ticket 04 (dialogue topology & roles)

- Make Planner and Critic **different model families**, and any adjudicator a third family with
  order-balanced comparison — heterogeneity is the best-evidenced structural debias (PoLL, Heter-MAD,
  Panickssery), but the adjudicator's own family bias must be checked (MAD).
- Keep the **3-round cap** (evidence saturates at 3–4 rounds and conformity grows per round); spend
  design budget on forced stance divergence and Critic agreeableness tuning, not extra rounds.
- Upgrade the verdict contract: **rationale before verdict**, plus required *verified* plan quotes and
  enumerable atomic objections — approval without engagement units is machine-detectable and should
  parse as "not approved".
- Give the Critic an **independence persona plus a reflection step** (BenchForm's two measured
  mitigations) rather than a generic "review this" prompt.
- Build rubber-stamp telemetry from day one: capitulation probes, position-swap consistency,
  seeded-flaw canaries, AIU/quote counts, and round-1-bare-approval flags feeding the existing routing
  telemetry log.
