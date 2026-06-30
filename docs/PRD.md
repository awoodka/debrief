# PRD & Build Plan — "Debrief": a Personal AI/ML Advisor Pipeline

> _Saved verbatim from the source PRD provided 2026-06-29. This is the original spec. For the
> consolidated, decision-resolved version we're actually building, see `PLAN.md`._

> **To the implementing agent (Claude Code, Opus 4.8):** This is a product requirements document and
> build plan for a tool I (the user) have designed carefully over a long research process. I have a
> **clear vision** for how it should behave. Your job is NOT to start coding immediately. Your job is to
> (1) read this whole document, (2) **interview me** using the Decision Forks in §9 — ask me those
> questions and let me answer before you build — and (3) only then produce an implementation plan and
> begin. Treat §1–§3 (Vision, Principles, Non-Negotiables) as fixed constraints you must not silently
> override. Everything in §9 is a genuine open choice where my preference matters; do not guess these.
> If during the build you hit a fork not listed in §9 that materially affects behavior, **stop and ask
> me** rather than assuming. I value getting the behavior right over getting it fast.

---

## 1. Vision (fixed)

**Debrief is an advisor, not a summarizer.** It pulls the AI/ML landscape into one place once or twice a
day and behaves like an exceptional research advisor who makes sure I never miss what matters — and who
*thinks*, rather than just lists.

Its signature capability: **catch the substantive paper or development that the research community
picked up within about a week, but that mainstream/hype channels (big newsletters, HN front page, tech
press) have NOT surfaced.** This is the "in-field vs mainstream traction gap." I am specifically NOT
interested in papers with no traction anywhere — the target is "researchers noticed, the crowd didn't
yet."

Everything the tool does should serve two questions about each item: **"Does this have real substance?"**
and **"Does this matter to *me* specifically, given what I'm building?"**

## 2. Principles (fixed)

- **Code quantifies; agents judge.** A deterministic Python collector does all fetching, dedup, and
  signal computation (no LLM — free, reliable, fast). LLM agents only reason over a pre-cleaned dataset.
  The collector must NEVER make the final "is this a gem / is this important" call with a hard threshold
  that discards items — it attaches signals and lets the agents judge.
- **Completeness AND judgment.** Judgment lives at the top of the output (the must-knows and the gems);
  completeness is preserved at the bottom (a full scannable index). Nothing important is ever silently
  dropped. "Tight but not over-shortened."
- **The reasoning is the product.** For flagged items, the advisor's *why* — why it has substance, why
  it matters to me — is the whole point. An item without reasoning is a feed entry, not advice.
- **Five independent worldviews, then synthesis.** Five agents each read the FULL dataset through their
  own evaluative lens, form independent opinions, then a synthesis step reconciles them — surfacing
  agreement (strong signal) and preserving disagreement (information, not noise).
- **Personalized.** A profile of me is the relevance engine that turns "minor to the world" into
  "relevant to my work."

## 3. Non-negotiables (fixed)

1. **Runs on my Claude Max subscription via Claude Code — NOT the Anthropic API.** No per-token billing.
   Subagents spawned from one Claude Code session draw from the subscription; that is the intended
   model. **Ensure no `ANTHROPIC_API_KEY` is set in the environment** (it silently overrides to paid) —
   detect and warn me if one is present.
2. **All data sources must be free.** Free APIs, RSS, or scraping. No paid tiers (no Crunchbase paid, no
   X API). Free API keys that cost nothing (GitHub PAT, optional Semantic Scholar / OpenAlex keys) are
   fine.
3. **Triggered manually** by a single slash command (`/debrief`). No scheduled/unattended automation in
   v1 (I've set that aside deliberately; design so it *could* be added later, but don't build it now).
4. **Five worldview subagents**, each reading the full dataset — not assigned narrow lanes/slices.
5. **Run time is not a concern.** Favor thoroughness over speed. Casting a wide net and enriching deeply
   is preferred over a fast shallow pass.

## 4. Architecture overview

```
/debrief  (manual, Claude Code, Max subscription)
   │
   ├─ 1. COLLECTOR  (python, no LLM, fail-soft per source)
   │      wide-net fetch → normalize → dedupe → ENRICH papers with traction signals
   │      → compute in_field / mainstream / divergence scores (attach raw signals, no hard cull)
   │      → persist per-item snapshots (for velocity / future trend layer)
   │      → write digest_input.{md,json}
   │
   ├─ 2. FAN-OUT  orchestrator dispatches the SAME digest + my PROFILE.md to 5 subagents IN PARALLEL
   │      Empiricist · Builder · Strategist · Contrarian · Connector
   │      (each: isolated context, own lens, cites the collector's signals as evidence, returns a brief)
   │
   ├─ 3. SYNTHESIS  merge 5 briefs → dedupe → elevate multi-lens picks → preserve lone-lens gems w/ reasoning
   │      → SHOW disagreements (don't average) → write debrief.{md,json}
   │
   └─ 4. (separate concern) dashboard renders debrief.json into tiers
```

## 5. The collector (deterministic, free, fail-soft)

Fetch a WIDE net, normalize to one schema, dedupe across sources, enrich papers with traction signals,
score divergence, persist snapshots. One dead source must never sink the run (per-source try/except).

**Sources (all free):** arXiv API (cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, stat.ML); HF Daily Papers JSON;
HF paper-pages JSON (`numTotalModels`, `numTotalDatasets`, `linkedSpaces`, `upvotes`); Semantic Scholar
(`influentialCitationCount`, `citationCount`, `tldr`); GitHub code/repo search; OpenAlex (optional);
lab/company RSS (OpenAI, DeepMind, Google Research, HF blog; community feeds for Anthropic/Meta/Mistral/
xAI); `huggingface/ai-deadlines` YAML; TechCrunch/VentureBeat AI RSS; HN Algolia; GitHub Search +
Trending scrape; newsletters (Last Week in AI, Import AI, Ahead of AI, Interconnects, Latent Space);
Bluesky public AppView; Reddit (r/LocalLLaMA, r/MachineLearning). X/Twitter = manual-only.

**Divergence (transparent heuristic, NOT a cull):**
```
in_field_score   ← w1*influentialCitationCount + w2*(hf linked-artifact counts)
                   + w3*github_impls + w4*researcher_mentions + w5*citationCount
mainstream_score ← w6*(big newsletter?) + w7*(HN points) + w8*(mainstream press) + w9*hf_upvotes
divergence       = in_field_score − mainstream_score
```
Attach raw signals + scores to every paper. high-in-field/low-mainstream → gem; high/high → must-know;
low/low → kept in full index; low-in-field/high-mainstream → hype-to-flag. Persist per-item snapshots
(velocity).

## 6. The five worldview agents

Markdown subagents, each reads the full enriched digest + `PROFILE.md`, cites concrete signals as
evidence, returns a structured brief. Differentiate by **what each values and the questions it asks**,
NOT personality. Plain isolated subagents (no inter-agent messaging). Each gem-hunter told to privilege
high-divergence items.

1. **Empiricist** — substance & methodology (primary gem-finder).
2. **Builder** — applied leverage / usable artifacts.
3. **Strategist** — field dynamics & power.
4. **Contrarian** — consensus-checker; uses divergence both ways; rigorous, not negative.
5. **Connector** — personal relevance via `PROFILE.md`.

## 7. Synthesis + tiered output

Synthesis merges the five briefs: dedupe; elevate multi-lens picks; preserve lone-lens gems WITH
reasoning; **show disagreements rather than averaging.** Writes `debrief.{md,json}`.

**Tiers:** 1) Bottom Line (3–6 must-knows). 2) Against the Grain (gem tier — the signature). 3) Worth
Attention / By Lens. 4) Full Index (everything past a minimal bar). 5) Deadlines / Events.

## 8. The `/debrief` command + PROFILE.md

- **`/debrief`** = markdown in `.claude/commands/`: bash pre-step `python collector/collect.py` → parallel
  fan-out → collect briefs → synthesize → tiered `debrief.{md,json}` → print Bottom Line to terminal.
- **`PROFILE.md`** = the relevance engine, ~1 page, dated. Sections: active projects · stack · goals ·
  baseline/don't-explain · open questions · adjacent interests · anti-interests.

## 9. DECISION FORKS — interview before building

(A) scope & taxonomy; (B) gem/divergence engine; (C) the five agents; (D) synthesis & output;
(E) sources & access; (F) state/persistence/ops; (G) future-proofing. **See `PLAN.md §3` for how every
one of these was resolved.**

## 10. Build sequence

1. Collector core (no LLM) → `digest_input` → inspect by hand. 2. Traction enrichment + snapshot store.
3. Five agents (draft prompts → review → wire). 4. `/debrief` command. 5. PROFILE.md. 6. Dashboard.
7. Verify (subscription not API; no `ANTHROPIC_API_KEY`; live run; tune).

## 11. Acceptance criteria

- `/debrief` runs end-to-end on the Max subscription, zero API charges, no `ANTHROPIC_API_KEY`.
- A single dead source does not break a run (fail-soft).
- The agents produce genuinely different briefs (not rewordings).
- Output is tiered; gem tier surfaces high-divergence items with reasoning + signals + confidence; Full
  Index keeps everything past the minimal bar.
- The gem mechanism provably catches a planted high-in-field/low-mainstream example.
- Personalization visibly reflects `PROFILE.md`.
- Clean seams exist for the trend layer + future automation, neither built in v1.

## 12. Re-verify at build (flux early–mid 2026)

Claude Code subscription-vs-API policy & model aliases; OpenAlex free-key requirement; Semantic Scholar
rate limits (backoff mandatory); community lab RSS freshness; Bluesky `searchPosts` auth; Reddit
app-approval flow.
