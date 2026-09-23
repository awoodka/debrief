# Debrief — Roadmap

v1 is running: the collector, four summarizers, the five-seat council and a single-voice article, published every morning at `debrief.alexwoodka.com` (see *Automation* below). The rest of this file is how that runs and what's still deferred.

## Near-term (fast-follow once v1 works)

- **Debrief-as-memory**: feed the last several days' briefings (compact — e.g. past "What happened" lists and deks) into synthesis so the advisor narrates continuity ("third agentic-memory release this week"). Complements the quantitative velocity signal.
- **Tune divergence weights** against real output (weights live in one config file, easy to change).

## The website — hosting the debrief archive

**Live at `debrief.alexwoodka.com`, served from Alex's home server since 2026-09-14.** The archive is a
static site. Each run renders a self-contained `debrief.html` (all CSS/JS inlined), and
`dashboard/build_site.py` turns `data/debriefs/*/debrief.json` into `site/`:

- `site/index.html`: a minimal newest-first list (date, one-line `dek`, link)
- `site/<date>/index.html`: one debrief, self-contained, with a "← all debriefs" link

It renders **both schemas**: the flowing-article schema through `debrief.template.html`, and the
original `bottom_line`/`gems`/`landscape` schema through `debrief.template.prev.html`. There's no debrief
index table (`debrief.db` holds only the velocity `snapshots`); the builder scans the folders. Locally,
`make site` builds it and `make preview` serves it at `localhost:8000`. `site/` is git-ignored and rebuilt
on demand.

_History:_ from 2026-08-09 the archive was deployed to Cloudflare Pages with `make publish` (`wrangler`)
and served at `alexwoodka.com/debrief`. That deploy was retired on 2026-09-14, and the server's archive
started fresh rather than migrating the old issues.

_Still open:_
- **Visibility.** Everything is public. A public-latest / private-archive split (Cloudflare Access on an
  archive path) is still an option.
- **Home page polish.** The index is intentionally minimal.

## The dynamic dashboard (deferred — only if web-triggered runs are wanted)

A small local web app (FastAPI, mirroring Timbre's stack) that renders `debrief.json`, browses history,
and **triggers a run**. Served from the local machine via a **Cloudflare Tunnel** (the `make prod` pattern
already used for Timbre). **Trigger-from-dashboard wrinkle:** a "Run" button must keep runs on the Max
subscription, so it shells out to the **Claude Code CLI in headless mode** (never the paid API) — verify
the headless multi-agent path at build time.

## The trend layer

A weekly/monthly "trend debrief" built off the **velocity snapshot store** (per-item signals over time) + the archive of past debriefs. Surfaces multi-week tides, not just daily developments. The snapshot store keeps 90 days of per-run snapshots for this; older rows are deleted, not downsampled.

## Scaling the council

Currently **5 seats** (to keep per-run quota observable). When token usage proves comfortable, add a **6th seat — the Research Scientist** (pure substance/methodology), restoring a dedicated rigor lens alongside the Skeptic. Each seat is one markdown file in `.claude/agents/`, so adding one is a one-file drop.

## Automation — the daily run (on the server since 2026-09-14)

`/debrief` runs every day at **08:30 America/New_York** on Alex's home server, started by a systemd timer.
The start is randomized by up to 10 minutes, and a run takes about 30 minutes.
From 2026-08-12 it ran as a macOS launchd job instead; that job and `scripts/run_debrief.sh` are retired.

Each run is three throwaway Docker containers built from one pinned image (a fixed Claude Code version
and locked Python deps), then a publish step:

1. **collect**: `python -m collector.collect`. It gets the collector's API keys, never the Claude token.
2. **agent**: `claude -p "/debrief --skip-collect"` on the Max subscription, with no Bash tool, the code
   read-only, and only `data/` writable. A server-side prompt (`agent-prompt.md`) adapts the steps to
   having no shell.
3. **build**: `render.py` and `build_site.py`, with no network access.
4. **publish**: an atomic switch to the new release. The last five releases are kept for rollback.

The server's scripts (`run.sh`, `update.sh`, `agent-prompt.md`) live outside this repo.

**Keep in sync.** `agent-prompt.md` overrides `/debrief` by step number (Steps 0, 1, 1.5, 2 and 6), and
relies on `data/debriefs/<today>/enriched_digest.md` and the four `summaries-a.md`…`summaries-d.md`
shards. Renumbering steps or changing that shard contract here needs a matching change on the server.

Ops notes:
- **Updating:** push to GitHub, then run `update.sh` on the server. It pulls and rebuilds the image, and
  the next run uses it. Nothing restarts.
- **Login:** the run uses a token from `claude setup-token`, which lasts a year. When it lapses, the run
  fails at its first check, before collecting, and says so in its log.
- **Quota:** each run is heavy (collector, 4 summarizers, a 5-seat council, Opus synthesis). A day that
  hits the subscription cap fails and logs it.
- **Local runs:** `/debrief` still works interactively on the Mac for testing. It renders locally and
  doesn't publish.

## Other deferred enrichments

- **OpenAlex** for richer trend / citation-graph enrichment (free key, ~2026 requirement).
- **Mass-market newsletters** (TLDR AI, The Batch, AlphaSignal). They were planned as web-archive scrapes,
  which were never built; a forwarding-inbox parser is the fallback if scraping doesn't work out. The six
  wired newsletters all come in over RSS.
- **Longer snapshot retention** if the trend layer wants deeper history (disk is not the constraint).

## Ops / housekeeping

- `data/` (debriefs, snapshot DB, cache) is git-ignored. The live copy is on the server; back it up separately if the debrief history matters long-term.
