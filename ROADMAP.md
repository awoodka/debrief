# Debrief — Roadmap

Everything here is **deliberately deferred** from v1. v1 = the collector + the five-seat council + synthesis + a tiered markdown briefing, run manually once a day. These are clean seams we've designed *for* but are **not** building yet.

## Near-term (fast-follow once v1 works)

- _(Reddit + Product Hunt + broader Hacker News were pulled into **v1** — see `docs/PLAN.md` §3 A2, content balance. No longer deferred.)_
- **Debrief-as-memory**: feed the last several days' briefings (compact — e.g. past Bottom Lines) into synthesis so the advisor narrates continuity ("third agentic-memory release this week"). Complements the quantitative velocity signal.
- **Tune divergence weights** against real output (weights live in one config file, easy to change).

## The website — hosting the debrief archive (decided 2026-08-09)

**Decision: host as a static site on Cloudflare Pages** (not the FastAPI/Tunnel app below). Two facts make
this the simple path: each run already renders a **self-contained** `debrief.html` (all CSS/JS inlined, no
server needed), and **alexwoodka.com is already registered + DNS-hosted on Cloudflare**, so a custom
domain and access-gating are one click away. Home page should be **as simple as possible**: just the list
of debriefs, newest first. The dynamic app (next section) is deferred until a "Run from the web" button is
actually wanted.

_Built (2026-08-09, this session):_
- **`dashboard/build_site.py`** — scans `data/debriefs/*/debrief.json` and writes a static `site/`:
  `site/index.html` (a **minimal** newest-first list — date + one-line `dek` + link) and
  `site/<date>/index.html` per run, each fully self-contained, with a "← all debriefs" nav link injected.
  Renders **both schemas** — the new flowing-article schema via `debrief.template.html`, the original
  `bottom_line`/`gems`/`landscape` schema via `debrief.template.prev.html` — so all past debriefs stay
  viewable. (The "SQLite debrief index" assumed elsewhere was never built — `debrief.db` holds only the
  velocity `snapshots` table — so the builder just scans the folders; add an index table later only if
  search/trends need it.)
- **`Makefile`** — `make site` (build), `make preview` (build + serve at localhost:8000), `make publish`
  (build + `wrangler pages deploy`). `site/` is git-ignored and rebuilt on demand.

_Remaining (needs your Cloudflare account / your choices):_
1. **Deploy** — run the first-hosting steps below (interactive `wrangler login`, then `make publish`),
   then add the custom domain `debrief.alexwoodka.com` in the Pages project settings.
2. **Durable store / backup** — `data/debriefs/` is git-ignored + local; put the source debriefs in a
   **private Git repo** so the archive is backed up (and Pages can auto-deploy on push if you prefer that
   to `wrangler`).
3. **Wire into the pipeline** — call `make publish` from `/debrief` Step 6 so each run updates the live
   site. Left manual for now so publishing stays a deliberate step.
4. **Home page polish + visibility** — the index is intentionally minimal; a richer landing page and the
   public-latest / private-archive split (Cloudflare Access) are the next session's work.

_First hosting steps:_ `npm i -g wrangler` → `wrangler login` → `wrangler pages project create debrief` →
`wrangler pages deploy site` (gives a `*.pages.dev` URL to confirm) → add the custom domain in the Pages
project settings (DNS auto-created since the domain is already on Cloudflare).

_Decisions:_
- **URL — decided (2026-08-09): start with the subdomain `debrief.alexwoodka.com`.** The domain is fresh
  (nothing at the apex today), so a subdomain is the fastest to stand up. A path (`alexwoodka.com/debrief`)
  can be adopted later — either by making the apex one Pages project with `/debrief` as a folder, or via a
  Cloudflare routing rule — without redoing the archive.
- **Visibility — leaning (confirm at build): public latest / private archive.** Newest debrief at a
  permanent public URL; the full back-catalog under a gated path (`…/archive/*`) behind **Cloudflare
  Access** (email login, free).

## The dynamic dashboard (deferred — only if web-triggered runs are wanted)

A small local web app (FastAPI, mirroring Timbre's stack) that renders `debrief.json`, browses history,
and **triggers a run**. Served from the local machine via a **Cloudflare Tunnel** (the `make prod` pattern
already used for Timbre). **Trigger-from-dashboard wrinkle:** a "Run" button must keep runs on the Max
subscription, so it shells out to the **Claude Code CLI in headless mode** (never the paid API) — verify
the headless multi-agent path at build time.

## The trend layer

A weekly/monthly "trend debrief" built off the **velocity snapshot store** (per-item signals over time) + the archive of past debriefs. Surfaces multi-week tides, not just daily developments. This is why the snapshot store keeps a rolling ~90-day high-resolution window (then downsamples).

## Scaling the council

Currently **5 seats** (to keep per-run quota observable). When token usage proves comfortable, add a **6th seat — the Research Scientist** (pure substance/methodology), restoring a dedicated rigor lens alongside the Skeptic. Each seat is one markdown file in `.claude/agents/`, so adding one is a one-file drop.

## Automation — daily autonomous run (built 2026-08-12)

`/debrief` now runs itself every morning. A macOS **launchd LaunchAgent**
(`scripts/com.alexwoodka.debrief.plist`, installed to `~/Library/LaunchAgents/`) fires at **10:00**
daily and runs **`scripts/run_debrief.sh`**, which invokes `claude -p "/debrief"` **headless on the Max
subscription** (verified: non-`--bare` headless uses the stored subscription login, no API key). Because
Step 6 already publishes, a successful run auto-updates `alexwoodka.com/debrief`.

Why local, not a cloud "routine": the pipeline needs the local repo, the Python venv, `data/`, and the
`wrangler` login — a cloud agent runs on a fresh clone with none of those.

Wrapper details worth knowing: it sets an explicit PATH (launchd's is minimal) so `claude`
(`~/.local/bin`), `wrangler` (`~/.npm-global/bin`), and `node` are reachable; it refuses to run if
`ANTHROPIC_API_KEY` is set (honors the subscription-only rule); it uses `--dangerously-skip-permissions`
so an unattended run never hangs on a tool prompt; and it writes a dated log to `logs/debrief-<date>.log`.

Ops notes:
- **Laptop sleep:** if the Mac is asleep at 10:00, launchd runs the job the next time it wakes (i.e. when
  the lid opens). No `pmset` wake hack needed.
- **Login expiry:** the `claude` subscription login expires periodically; if it lapses the run fails and
  says so in the log — re-run `claude` interactively to re-login.
- **Quota:** each run is heavy (collector + 4 summarizers + 5-seat council + Opus synthesis); a day that
  hits the subscription cap just fails and logs it.
- Change the time: edit Hour/Minute in the plist, then `launchctl unload -w … && launchctl load -w …`.
  Remove: `launchctl unload -w ~/Library/LaunchAgents/com.alexwoodka.debrief.plist`.
  Test now: `launchctl start com.alexwoodka.debrief` (watch `logs/debrief-<today>.log`).

## Other deferred enrichments

- **OpenAlex** for richer trend / citation-graph enrichment (free key, ~2026 requirement).
- **Forwarding-inbox newsletter parser** as a fallback if the newsletter web archives become unscrapeable.
- **Longer snapshot retention** if the trend layer wants deeper history (disk is not the constraint).

## Ops / housekeeping

- `data/` (debriefs, snapshot DB, cache) is local and git-ignored. Set up a periodic backup of `data/` if the debrief history matters to you long-term.
