# Debrief

Your AI/ML landscape, read every morning by a council of advisors — not a feed, an *advisor*.

Debrief pulls the day's AI research and tooling into one place and has five expert "seats" read **all** of it independently, each through its own lens, then synthesizes their views into one cohesive morning briefing. Its signature job: catch the substantive paper or release that the **research community picked up within the last week, but that the mainstream hype channels haven't surfaced yet** — the in-field-vs-mainstream traction gap — and tie it to the broader trends, the way a senior advisor would.

## The idea

Most AI newsletters tell you what's already loud. Debrief is built to tell you what's *becoming* important before it's obvious — and why it matters to **you specifically**, given what you're building.

Two questions drive everything it does about each item:

1. **Does this have real substance?**
2. **Does this matter to me, given my work?**

## How it works

```
/debrief  (manual, once a day, on your Claude Max subscription via Claude Code)
   │
   ├─ 1. COLLECTOR  (Python, no LLM — free, deterministic, fail-soft)
   │      wide-net fetch → normalize → dedupe → enrich papers with traction signals
   │      → compute in-field / mainstream / divergence → snapshot for velocity
   │
   ├─ 2. THE COUNCIL  (5 subagents, in parallel, each reads the FULL dataset)
   │      Engineer · Founder · Investor · Competitive Scout · Skeptic
   │      each forms its own view, citing the collector's signals as evidence
   │
   ├─ 3. SYNTHESIS  merge 5 briefs into ONE cohesive advisor voice
   │      → lead with the call, attribute reasoning, surface genuine disagreement
   │
   └─ 4. OUTPUT  a tiered briefing (≤10k words): Bottom Line → Gems → Landscape → Full Index
```

### The council

- **The Engineer** — what makes me faster this week; what to adopt.
- **The Founder** — what's newly possible; where's the white space *(pivot offense)*.
- **The Investor** — where attention, talent, and capital are flowing.
- **The Competitive Scout** — what just shipped that threatens or commoditizes my work *(pivot defense)*.
- **The Skeptic** — is this real or overhyped; reads the traction gap both ways.

All five read your `PROFILE.md`, so every seat advises *you specifically*.

### The gem mechanism

Each item is scored by deterministic code and **never culled by a hard threshold** — the code attaches signals; the council judges.

- **In-field score** = how much the research community is engaging (HF linked artifacts, GitHub implementations, researcher chatter, citations).
- **Mainstream score** = how much the general tech public is engaging (HN, tech press, mass-market newsletters).
- **Divergence** = in-field − mainstream. **High in-field, low mainstream = a gem candidate.**

## Status

🚧 **In active development.** The architecture and every design decision are locked (see `docs/PLAN.md`); the pipeline is being built next. What exists today:

- `PROFILE.md` — the personalization template (fill it in)
- `docs/PRD.md` — the product requirements
- `docs/PLAN.md` — the full implementation plan + decision record
- `ROADMAP.md` — what's deferred (dashboard, trend layer, automation)

## Running it (planned)

```bash
cp .env.example .env     # add your free GitHub PAT, Semantic Scholar key, Bluesky app password
# (do NOT set ANTHROPIC_API_KEY — debrief runs on your Max subscription, not the paid API)
/debrief                 # from within Claude Code
```

## Principles

- **Code quantifies; agents judge.** Deterministic collection and scoring; LLM reasoning only over a clean dataset.
- **Completeness *and* judgment.** Strong opinions up top, a complete scannable index at the bottom — nothing important silently dropped.
- **The reasoning is the product.** *Why* something matters is the whole point.
- **Runs on your subscription, all sources free.** No per-token billing, no paid APIs.
