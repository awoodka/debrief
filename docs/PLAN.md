# Debrief — Implementation Plan & Decision Record

_Last updated: 2026-06-29. **Single source of truth** for what we're building and why. Consolidates the
PRD (`PRD.md`) with every decision from the design interview. Returning after time away? Read this first._

---

## 1. What debrief is

A personal AI/ML **advisor** (not a summarizer) that runs **once a day** on the Claude Max subscription
via Claude Code. A deterministic Python collector casts a wide net over free sources and scores each item
for the **in-field-vs-mainstream traction gap**; five worldview subagents (a "council") each read the
full dataset and brief independently; a synthesis step weaves them into **one cohesive ≤10k-word morning
briefing** that leads with judgment, surfaces the gems, and ties developments to trends like a senior
advisor.

## 2. Fixed constraints (PRD §1–§3 — do not override)

- **Advisor, not summarizer.** Signature = catch the substantive thing **researchers picked up within
  ~a week that the crowd hasn't** (not zero-traction papers). Every item judged on: *real substance?* and
  *matters to me?*
- **Code quantifies; agents judge.** Deterministic collector attaches signals and **never hard-culls the
  gem decision.** Completeness *and* judgment. The reasoning is the product. Five independent worldviews,
  then synthesis (agreement = signal, disagreement = preserved information). Personalized via `PROFILE.md`.
- **Max subscription via Claude Code, never the Anthropic API.** Detect + warn on any `ANTHROPIC_API_KEY`.
  All sources free. Manual `/debrief` trigger only (design for automation, don't build it). Five seats,
  each reading the full dataset. Run time is no concern — thoroughness over speed.

## 3. Locked decisions (the design interview)

### A. Scope & taxonomy
- **arXiv categories:** the six — cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, stat.ML.
- **Time windows (split):** **papers fetched ~14 days back** (so in-field traction has time to mature);
  **news/repos/social tight** (since last run). Papers deduped across runs.
- **Dataset size — minimal-pulse floor:** the collector **fetches AND snapshots everything**, but the
  digest the agents read = items with *any pulse* (nonzero in-field signal, or news/repos/discussion
  above a tiny bar). The zero-signal arXiv tail stays on disk, not sent to agents. Honors completeness +
  the no-cull rule while keeping agent context sane.

### A2. Content balance (added 2026-06-30)
Research is a **slice, not the spine** — this is a startup-developer's advisor, so online discourse,
Hacker News, and new startups/companies get the most attention; papers are one input among many.
- **Collect wide on research (miss nothing), emphasize narrowly.** The arXiv firehose stays fully
  collected and lives in the Full Index; we rebalance *attention / narrative weight*, never cull.
- **Adaptive research budget ≈ 25% of the surfaced brief** — a soft *ceiling*, not a quota. Usually
  less; **flexes up when something genuinely breaks** (major release / high-divergence) so breaking
  research is never missed. The rest = discourse + HN + startups/funding + tools.
- **New discourse/startup sources pulled into v1** (were fast-follow): **Reddit via public RSS**
  (NO API key — r/LocalLLaMA, r/MachineLearning, r/artificial, r/startups; IP rate-limited, so fetched
  spaced + fail-soft, `top/day|week`), **Lobsters** (HN-like AI discourse), **broader Hacker News**
  (front + Show + Launch HN), and **Product Hunt** (optional, needs a token). Plus funding RSS
  (TechCrunch / VentureBeat). Note: Reddit RSS omits upvote scores → Reddit = *discourse presence*.
- **The gem generalizes beyond papers:** apply in-field-vs-mainstream divergence to **repos, tools,
  and startup launches** too — "noticed by practitioners before the crowd" can be a hot dev tool or
  an under-the-radar startup, not only a paper.

### B. The gem / divergence engine (the heart)
- **In-field score leads with FAST signals.** Citations lag (a 3–14-day-old paper has ~0). So weight
  **HF linked-artifacts + GitHub implementations + researcher chatter highest**; influential/raw citations
  are a lagging bonus that mainly confirms slightly older items.
- **Gem timing = snapshot + velocity.** Base the gem call on the current in-field score with a soft
  recency lean; layer cross-run **velocity** (acceleration) as a booster once snapshot history exists.
  Works on run 1, sharpens each run.
- **Weights:** I'll propose principled starting weights (in one config file) and we **calibrate against
  the first real run**, not set numbers in a vacuum.
- **The divergence axis (where the "researchers vs crowd" line sits):**
  - **In-field = the AI research/practitioner community:** HF linked-artifacts **and upvotes**, GitHub
    impls, ML chatter (Bluesky/Reddit), **researcher-facing newsletters** (Import AI, Interconnects,
    Ahead of AI, Latent Space), citations.
  - **Mainstream = the general tech public:** HN front page, tech press (TechCrunch/VentureBeat),
    **mass-market newsletters** (TLDR AI, The Batch, AlphaSignal).
  - **Newsletters are split by tier** (mass-market = mainstream; researcher-facing = mild in-field).
  - **HF Daily Papers upvotes = in-field** (user's call — a researcher-community signal). *Watch item:* a
    paper trending atop HF can surface as a "gem" despite high upvotes; if it over-fires on run 1, add a
    small "already-salient" dampener. Tunable with the weights.

### C. The council (five seats)
Replaced the PRD's abstract lenses with **role-based seats tuned to a startup developer**, each defined
by *what it values and the questions it asks* (not a persona voice), each **gem-biased** (privileges
under-the-radar items), each personalized via `PROFILE.md`:

| Seat | Asks | (covers PRD's) |
|---|---|---|
| **The Engineer** | Given my stack, what makes me faster/better this week; what to adopt before it's everywhere? | Builder |
| **The Founder** | What's newly possible? Where's the white space? *(pivot offense)* | Builder-ish |
| **The Investor** | Where are attention/talent/capital flowing; what's the trajectory? | Strategist |
| **The Competitive Scout** | What just shipped that threatens/commoditizes my work? *(pivot defense)* | — (new) |
| **The Skeptic** | Real or overhyped? Reads divergence both ways; carries the substance check. | Empiricist + Contrarian |

- **Personalization is woven into all five** via `PROFILE.md` (no separate Connector seat).
- **Models (REVISED 2026-07-01):** five seats on **Sonnet 5**; **synthesis on Opus** (orchestrator).
  Exact model alias/id confirmed at build (§12) — `model: sonnet` frontmatter alias resolves to the
  current Sonnet, or use the explicit `claude-sonnet-5`-style id.
- **Web access (REVISED 2026-07-01 — relaxes §2 "collector does all fetching"):** seats **CAN navigate
  the live web + the links in the digest** (WebFetch / WebSearch), so a seat can open a flagged
  paper/repo/thread and verify substance itself — key for judging the (A) fresh/unscored papers and for
  adversarial gem-checking. Trade: variable quota + less determinism. The collector still packs enough
  for triage; seats fetch depth on demand (so no need to pre-pack full article bodies).
- **Prompts reviewed before wiring.** I draft all five system prompts for sign-off, then wire them.
- **Scaling seam:** each seat = one markdown file in `.claude/agents/`. 6th seat when quota allows =
  **Research Scientist** (dedicated rigor).

### D. Synthesis & output
- **Synthesis = the orchestrator itself** (already holds the five briefs; runs on Opus natively).
- **Bottom Line tone = opinionated & ranked**, every call **calibrated** (confidence + what would flip it).
- **Output v1 = terminal BLUF + a rich `debrief.md`** engineered to read like a real briefing. Clean
  dashboard is a **fast-follow** over the same `debrief.json`.
- **Disagreements = prominent, as integrated *bull case / bear case / my read* prose** in the advisor's
  voice (reserved for decision-relevant splits) — not a mechanical seat-vs-seat box.
- **Cohesive voice:** the body is organized **by theme**, one integrated voice with **inline lens
  attribution**; raw per-seat briefs are demoted to an appendix / `debrief.json`.
- **Length:** **≤10k words is a CAP, not a target.** As long as the day's substance warrants, never
  padded. Tier word-counts are maxima, not quotas.

### E. Sources & access
- **Social / discourse:** **Bluesky** (app password) + **Reddit via public RSS** (no API key — IP
  rate-limited, fetched spaced + fail-soft) + **Lobsters**. Plus **broader HN** (front/Show/Launch) and
  optional **Product Hunt** for the discourse + startup pillars (see A2).
- **Mass-market newsletters:** **scrape their web archives** (tldr.tech/ai, deeplearning.ai/the-batch,
  AlphaSignal's site) — protects the mainstream axis without an email inbox. Forwarding-inbox is the
  fallback. *(Re-verify archive scrapeability at build.)*
- **Credentials for v1:** **GitHub PAT** (essential — the only real must-have). **Semantic Scholar key
  is optional and skippable** — its API works key-less (backoff), and citations are a low-value lagging
  signal here. Bluesky app-password + Product Hunt token are optional bonuses. OpenAlex deferred.

### F. State, persistence, ops
- **Storage = four tiers by lifetime:**

  | Tier | What | Lifetime |
  |---|---|---|
  | **Permanent** | `debrief.md` + `debrief.json` (dated) + SQLite debrief index | Forever (tiny: ~tens–hundreds of MB/yr) |
  | **Rolling** | per-item signal snapshots (velocity engine) | ~90 days high-res, then downsample |
  | **Ephemeral** | API response cache | TTL + hard size cap, self-evicting |
  | **Transient** | raw `digest_input` | Overwritten each run (latest kept for inspection) |

- **Snapshot store = SQLite** (`data/debrief.db`) — holds the rolling snapshots **and** the debrief index.
- **Caching = smart per-source TTL** (hours for news/social, long for paper metadata), **size-capped +
  self-evicting** (this is the only thing that could bloat; it's bounded by design). Backoff everywhere.
- **Repo = Python collector**, the layout below, `git init` in `debrief/`, README, `.gitignore`
  excluding `.env` and `data/`.

### G. Future seams (design for, build none)
Trend layer (off the snapshot store + debrief archive), the dashboard, optional automation. The velocity
store + debrief index *are* those seams. See `ROADMAP.md`.

## 4. Repository layout

```
debrief/
  README.md            ROADMAP.md            PROFILE.md (the relevance engine)
  .env.example         .gitignore
  docs/                PRD.md, PLAN.md (this file)
  collector/           Python: fetch → normalize → dedupe → enrich → divergence → snapshot → digest
  .claude/agents/      the five seat subagents (markdown)
  .claude/commands/    debrief.md (the /debrief command)
  data/                (git-ignored) debriefs, debrief.db, cache, latest digest_input
  dashboard/           (future) FastAPI app over debrief.json + the SQLite index
```

## 5. Data flow (one run)

```
/debrief
  → collector/collect.py  (no LLM, fail-soft per source)
       fetch wide → normalize to one schema → dedupe → enrich papers with traction signals
       → compute in_field / mainstream / divergence → snapshot to SQLite → write data/digest_input.{md,json}
  → orchestrator dispatches digest + PROFILE.md to 5 seats IN PARALLEL (Sonnet, Read-only)
  → each returns a structured brief citing the collector's signals
  → orchestrator (Opus) synthesizes → one cohesive tiered briefing
       → write data/debriefs/<date>/debrief.{md,json}; index the run in SQLite
       → print the Bottom Line (+ top gem) to the terminal
```

**Output tiers** (cohesive, by theme, ≤10k words):
1. **Bottom Line** — opinionated, ranked, calibrated. *(also printed to terminal)*
2. **Against the Grain (gems)** — the signature; deep reasoning per gem: substance, why-it-matters-to-me,
   divergence signals, confidence + what would flip it.
3. **The Landscape** — integrated narrative, inline lens attribution, bull/bear callouts on real splits.
4. **Full Index** — everything past the minimal-pulse bar, terse, signals attached *(completeness)*.
5. **Deadlines / Events** — small standing section.
   - *Appendix: raw per-seat briefs (transparency), in `debrief.json`.*

## 6. Build sequence

> **Pause points are explicit.** Nothing past a pause proceeds without my sign-off.

1. **Repo scaffold + docs + git.** ← _done this turn._
2. **Collector core** (no LLM): wide arXiv sweep + free RSS/news/repos → one schema → dedupe → fail-soft
   → `data/digest_input.{md,json}`. **⏸ PAUSE — I inspect the raw output by hand before we go further.**
3. **Traction enrichment**: HF paper-pages + Semantic Scholar (mandatory backoff) + GitHub-impl search;
   compute divergence; attach raw signals; add the SQLite snapshot store + caching.
4. **Five seats**: draft the criteria-based, signal-citing, gem-biased system prompts. **⏸ PAUSE — I
   review the prompts**, then wire them. Test that the briefs are genuinely different.
5. **`/debrief` command**: bash pre-step + parallel fan-out + Opus synthesis → tiered `debrief.{md,json}`
   + terminal Bottom Line.
6. **PROFILE.md**: template exists; I fill in real content; confirm the seats visibly use it.
7. **Dashboard**: deferred (see ROADMAP).
8. **Verify**: subscription not API; no `ANTHROPIC_API_KEY`; full live run; review the first real debrief
   and tune weights + prompts.

## 7. Acceptance criteria

- `/debrief` runs end-to-end on the Max subscription, **zero API charges, no `ANTHROPIC_API_KEY`**.
- **Fail-soft:** a single dead source doesn't break a run (demonstrable).
- The five seats produce **genuinely different** briefs (not five rewordings).
- Output is **tiered + cohesive**; the gem tier surfaces high-divergence items with reasoning + signals +
  calibrated confidence; the Full Index keeps **everything past the minimal bar** (nothing silently lost).
- The gem mechanism **provably catches a planted high-in-field/low-mainstream example**.
- Personalization **visibly reflects `PROFILE.md`**.
- **Clean seams** exist for the trend layer, the dashboard, and automation — none built in v1.

## 8. Re-verify at build (flux, early–mid 2026)

Claude Code subscription-vs-API policy & current model aliases (Sonnet/Opus); newsletter web-archive
scrapeability; Semantic Scholar rate limits (backoff mandatory); community lab RSS freshness (Olshansk);
Bluesky `searchPosts` auth; HF paper-pages JSON fields; arXiv rate limits (1 req/3s + backoff).
