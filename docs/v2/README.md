# Debrief v2: collector and triage

v2 replaces the v1 collector and its five-seat council with three layers:

1. **Collector.** Deterministic Python that fetches, stores the raw responses, builds stories, and attaches discussion.
2. **Triage.** Filters, classifies, and caps, then writes the index. Rules first; Claude through Claude Code headless for the judgment calls.
3. **The skill.** One Claude Code skill reads the index and writes the brief. It's built separately.

[PLAN.md](PLAN.md) is the approved design. [BRIEF.md](BRIEF.md) is the original brief; where they disagree, the plan wins. [SOURCES.md](SOURCES.md) is the latest source probe.

Status: **M0 and M1 are done**: the source probe, plus `collect` with the five primary adapters, the raw store, the seen-set and story building. Discussion, triage and the output contract arrive in M2–M4. The v1 pipeline (`collector/`, `.claude/`, `dashboard/`) still runs the live site from `main` and isn't touched here.

## Development setup

Everything runs in a Docker image, because the server has no Python venv support:

```sh
scripts/dev build                                  # build debrief-v2:dev (Python 3.13, locked deps, pinned Claude Code)
scripts/dev pytest -q                              # run the tests
scripts/dev python -m debrief probe-sources        # probe every enabled source once
```

`scripts/dev` mounts the repo at `/app` and the data dir at `/data`.

- **Data dir.** `DEBRIEF_DATA_DIR` sets it; the default is `/srv/data/debrief-v2`. The script refuses to use the v1 data dir.
- **Source keys.** By default the container gets source keys from `./.env` (gitignored).
- **Claude.** `scripts/dev --claude …` swaps that for the Claude token file instead. The two are never passed together.

## Commands

| Command | What it does |
|---|---|
| `debrief probe-sources` | Requests every enabled source once, through the same rate limits the collector uses. Writes `docs/v2/SOURCES.md` and `$DEBRIEF_DATA_DIR/probe/probe-<time>.json`. |
| `debrief probe-sources --source ID [--source ID]` | Re-probes just those sources (disabled ones included) and merges them into the latest full probe. |
| `debrief probe-sources --include-disabled` | Also requests disabled sources. |
| `debrief collect` | Fetches every collectable source, keeps the raw responses, and builds stories. Writes `runs/<date>/collect-<run>.md` and `.json`. Exit code 3 if any source failed. |
| `debrief collect --source ID [--source ID]` | Just those sources. |
| `debrief collect --replay YYYY-MM-DD [--into PATH]` | Rebuilds that day from its raw files into a new database, with no network. Default target: `<data>/replay/`. |
| `debrief triage`, `snapshot-fixtures` | Not built yet (M3, M6). |

## How `collect` works

For each source in `sources.yml` order:

1. The adapter fetches, and every response is written to the raw store before anything parses it.
2. In one transaction, the builder stores items, stories and mentions. Only then are the source's ETags and cursor saved, so a crash means a retry, never a gap.

A source is collected when it's enabled, has a role other than `context`, and its adapter exists. Context sources are lookups for later stages.

Each source gets one status in the run report:

- `ok`: answered with no failures. Zero new items is a quiet day, still `ok`.
- `partial`: some requests failed.
- `failed`: every request failed, or the source crashed. It's counted in `source_state.consecutive_failures`, and its cursor doesn't move.
- `empty`: it answered with nothing in it.

Cursors, by adapter:

| Adapter | New means | Cursor |
|---|---|---|
| `rss` | entry key not in the seen-set | ETag / Last-Modified per feed |
| `listing_page` | post link not in the seen-set | none needed; the page is re-read each run |
| `hf_daily_papers` | paper not in the seen-set | last date; each run re-reads from the day before it to today |
| `arxiv_rss` | announce type `new` or `cross` | last announcement date; missed weekdays are backfilled from OAI-PMH |
| `github_releases` | release id not in the seen-set | ETag per repo |

**The age guard.** On a first run, a feed hands over its whole history; OpenAI's has 1,315 entries. So an anchor whose publish date is more than `collect.window_days` (7) old when first seen is recorded as seen, not made a story.
- For feeds and APIs, whose dates are reliable, the guard always applies.
- For scraped pages, it applies only on the source's first run, because a scraped date can be wrong. After that, a link missing from the seen-set is itself the evidence that the post is new.
- Listing pages also fetch only their 5 newest links on a first run.

## Data layout

```
$DEBRIEF_DATA_DIR/
  raw/<date>/<source>/<run>.jsonl.gz   every response, byte-exact, pruned after raw_retention_days (60)
  state/debrief.sqlite                 cursors, ETags, the seen-set (items), stories, mentions, runs
  runs/<date>/collect-<run>.{md,json}  the per-run report
  probe/                               probe-sources results
  replay/                              databases built by --replay
```

## Being a polite client

Every request goes through `src/debrief/http.py`:

- **Rate buckets.** Requests go out one at a time. Requests in the same bucket are spaced by its `interval_s` (`config/settings.yml`). A bucket entry is a host, which covers its subdomains, optionally with a path prefix.
- **arXiv shares one budget.** The terms allow one request every 3 s across all our machines, and the v1 collector on the same server shares that budget. So don't run v2 against arXiv during the v1 window (08:30–09:10 ET).
- **User-Agent.** `debrief/2.0 (personal AI/ML news digest; <contact>)`. Set `DEBRIEF_CONTACT` to an email; some APIs require one (SEC EDGAR).
- **Conditional requests.** Callers pass the stored ETag and Last-Modified, and a 304 comes back as `not_modified`.
- **429 and 5xx.** The client honours `Retry-After` up to 120 s, otherwise backs off exponentially. Each request is tried at most 3 times.
- **robots.txt** is parsed per RFC 9309 (`src/debrief/robots.py`). The standard library's parser can't, because it handles neither `*` nor `$`. Policy:
  - Page scraping never fetches a disallowed page.
  - Where robots.txt disallows a feed or an API path, we use the site's official alternative instead. Examples: GitHub releases come from the REST API, because `/*.atom$` is disallowed. PyPI uses per-project RSS, because `/pypi/*/json` is disallowed. arXiv lookups use OAI-PMH, because `export.arxiv.org` disallows everything.
  - A source with no allowed route is disabled, with the reason in its notes (Lobsters, YouTube feeds).

## Adding a source

Add an entry to `config/sources.yml`, then run `debrief probe-sources --source <id>`:

```yaml
- id: example-blog              # stable; used in raw paths and the index
  name: Example Lab blog
  adapter: rss                  # which adapter fetches it (rss, listing_page, hn, ...)
  roles: [anchor]               # anchor | discussion | reference | context; a list, so a source can hold two
  tier: 1                       # 1 always considered · 2 filtered normally · 3 attach-only (never an anchor)
  kind: announcement            # default story kind for anchors
  org: Example Lab
  url: https://example.com/feed.xml
  probe: {kind: feed}           # how probe-sources checks it
  notes: anything worth knowing # shown in SOURCES.md
```

The rest of the field reference:

- **`url` placeholders.** `{each}` expands over an `each:` list, one request per entry (repos, orgs, packages). Date placeholders: `{today}`, `{yesterday}`, `{week_ago}` (all `YYYY-MM-DD`) and `{since_24h}` (unix seconds).
- **`candidates`.** Instead of `url`, a list of unverified endpoints that the probe tries in order. Each is a URL string, or `{url, probe}` to override the probe for that one. Once one is verified, it becomes `url`.
- **`probe.kind`:**
  - `feed`: RSS or Atom, parsed with feedparser.
  - `links`: an HTML page. Links are matched by `link_pattern` (a regex on absolute URLs) or `selector` (CSS). Feeds the page advertises are also reported.
  - `json`: optional `items` (a dotted path to the list; `''` means top level; omit it to auto-detect), plus `title`, `date`, `count`, and `single: true` for one-object responses.
  - `graphql`: like `json`, plus `query`.
  - `status`: `expect` (a list of acceptable HTTP statuses) and `contains` (a substring of the body).
  - `itunes`: finds podcast feeds by name through the iTunes search API.
- **`auth`.** `{env, header, format}` sends a header when that environment variable is set, e.g. `{env: GITHUB_PAT, header: Authorization, format: 'Bearer {}'}`.
- **`enabled: false`.** Ships the source off. The probe lists it with its notes but doesn't request it.

`src/debrief/config.py` validates every entry when it loads: required fields, known keys, valid roles and tiers, tier 3 never an anchor, and `{each}` only with an `each` list.

## Layout so far

```
src/debrief/     cli.py config.py http.py robots.py urls.py probe.py collect.py
                 canon.py (URLs, story ids) sanitize.py times.py extract.py (HTML helpers)
src/debrief/adapters/   base.py + rss, listing_page, hf_papers, arxiv, github_releases
src/debrief/store/      db.py (state DB), raw.py (raw store), migrations/*.sql
src/debrief/stories/    build.py (items -> seen-set, stories, mentions)
config/          settings.yml (UA, timeouts, rate buckets), sources.yml (every source)
tests/           unit and end-to-end tests (fixtures/ are real responses, trimmed); scripts/dev pytest -q
deploy/          Dockerfile, requirements*.in/.lock (see deploy/README.md)
scripts/dev      the dev container wrapper
docs/v2/         BRIEF.md, PLAN.md, SOURCES.md, this README
```
