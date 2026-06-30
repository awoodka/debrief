# Debrief — Roadmap

Everything here is **deliberately deferred** from v1. v1 = the collector + the five-seat council + synthesis + a tiered markdown briefing, run manually once a day. These are clean seams we've designed *for* but are **not** building yet.

## Near-term (fast-follow once v1 works)

- _(Reddit + Product Hunt + broader Hacker News were pulled into **v1** — see `docs/PLAN.md` §3 A2, content balance. No longer deferred.)_
- **Debrief-as-memory**: feed the last several days' briefings (compact — e.g. past Bottom Lines) into synthesis so the advisor narrates continuity ("third agentic-memory release this week"). Complements the quantitative velocity signal.
- **Tune divergence weights** against real output (weights live in one config file, easy to change).

## The dashboard (the next big piece)

A small local web app (FastAPI, mirroring Timbre's stack) that:

- renders today's `debrief.json` as a clean, navigable briefing,
- browses **past entries** (history) from the SQLite debrief index,
- eventually **triggers a run**.

**Hosting:** served from the local machine, exposed on a domain via **Cloudflare Tunnel** — the same `make prod` pattern already used for Timbre — so it's reachable from anywhere.

**Trigger-from-dashboard wrinkle:** a "Run" button must keep runs on the Max subscription, so it shells out to the **Claude Code CLI in headless mode** (never the paid API). Verify the headless multi-agent path at build time.

## The trend layer

A weekly/monthly "trend debrief" built off the **velocity snapshot store** (per-item signals over time) + the archive of past debriefs. Surfaces multi-week tides, not just daily developments. This is why the snapshot store keeps a rolling ~90-day high-resolution window (then downsamples).

## Scaling the council

Currently **5 seats** (to keep per-run quota observable). When token usage proves comfortable, add a **6th seat — the Research Scientist** (pure substance/methodology), restoring a dedicated rigor lens alongside the Skeptic. Each seat is one markdown file in `.claude/agents/`, so adding one is a one-file drop.

## Optional automation

v1 is manual (`/debrief`). The architecture leaves room for a scheduled wrapper (cron / Claude Code routine) — deliberately **not** built, set aside by choice.

## Other deferred enrichments

- **OpenAlex** for richer trend / citation-graph enrichment (free key, ~2026 requirement).
- **Forwarding-inbox newsletter parser** as a fallback if the newsletter web archives become unscrapeable.
- **Longer snapshot retention** if the trend layer wants deeper history (disk is not the constraint).

## Ops / housekeeping

- `data/` (debriefs, snapshot DB, cache) is local and git-ignored. Set up a periodic backup of `data/` if the debrief history matters to you long-term.
