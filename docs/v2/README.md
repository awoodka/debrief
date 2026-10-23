# Debrief v2: collector and triage

v2 replaces the v1 collector and its five-seat council with three layers:

1. **Collector.** Deterministic Python that fetches, stores the raw responses, builds stories, and attaches discussion.
2. **Triage.** Filters, classifies, and caps, then writes the index. Rules first; Claude through Claude Code headless for the judgment calls.
3. **The skill.** One Claude Code skill reads the index and writes the brief. It's built separately.

[PLAN.md](PLAN.md) is the approved design. [CRITERIA.md](CRITERIA.md) holds the rules for what makes it into the index. [BRIEF.md](BRIEF.md) is the original brief; where they disagree, the plan and the criteria win. [SOURCES.md](SOURCES.md) is the latest source probe.

Status: **M0–M2 are done.**
- **M0:** the source probe.
- **M1:** `collect` with the raw store, the seen-set and story building.
- **M2:** discussion attached to stories (Hacker News threads and comment trees, Hugging Face paper comments, newsletter and Techmeme coverage); signal snapshots (paper upvotes, repo stars, models built on a paper); the model-release sources; discussion levels and trend.

Triage and the output contract arrive in M3–M4. The v1 pipeline (`collector/`, `.claude/`, `dashboard/`) still runs the live site from `main` and isn't touched here.

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
| `debrief collect --rebuild` | Rebuilds the whole state from all raw data after a parser change, keeping cursors, ETags and run history. The previous database is kept as a backup. Took 23 s for a day of 7 runs. |
| `debrief collect --cadence 6h` | The discussion poll: only sources whose `options.cadence` includes `6h` (Hacker News and its comment trees). It's what gives trend more than one observation a day. |
| `debrief bootstrap-bands [--days N]` | Reads back Hacker News history (default `bands.window_days`, 7), so discussion levels have a distribution to compare against from day one. 14 days was 14,346 stories in 56 requests on 2026-10-22. |
| `debrief story ID_OR_TITLE` | One story: its sources, signals, the paper use bar, attached threads and comments, coverage, discussion level and trend. |
| `debrief sections` | Which sources feed each section, straight from the config. |
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

Sources run in phases:
1. anchors, which create stories
2. lookups about them (per-paper Hugging Face signals)
3. discussion sources (Hacker News, Simon Willison)
4. comment trees (Hacker News comments, Hugging Face paper comments)
5. reference sources (the newsletters, Techmeme)

At the end of the run, a retro pass re-tries recent unattached posts against the run's new stories.

**Attaching discussion (brief §5.3, `stories/attach.py`).** A discussion post gets one story. The first rule that matches wins:
1. a hint from its adapter
2. its thread's story
3. an exact URL
4. an arXiv, Hugging Face or GitHub id in its URLs or text
5. an identifier-like name in its title (a word with a digit, such as PHP2Shell or Qwen3.8) that exactly one recent non-paper story's title shares
6. a conservative title match (Jaccard ≥ 0.6 and at least 4 shared words)

The method and score are stored. At the end of a run, posts attached by name or title move if a URL or id now points at a story. A reference post gets a coverage row for every story it links to instead. A source with both roles gets both.

**Discussion level and trend (`stories/signals.py`, rules in its docstring).**
- **Percentile:** within each source, a story's best thread's points are compared with that source's threads from the last 7 days (`bands.window_days`). Only threads with at least `bands.floor` points count (10 for HN), because most submissions are never discussed. Without the floor, the 75th percentile was 4 points.
- **Spread:** a comment counts under its thread's source, so an HN comment tree is still HN.
- **Level:**
  - none: nothing mentions it
  - some: below the 75th percentile, with one source
  - notable: at least the 75th percentile, or 2 sources
  - heavy: at least the 95th percentile, or 3 sources with at least the 75th percentile
- **Trend:** points plus comments, compared over the last two intervals between snapshots: rising, flat, falling, or unknown with fewer than three snapshots.

Cursors, by adapter:

| Adapter | New means | Cursor |
|---|---|---|
| `rss` | entry key not in the seen-set | ETag / Last-Modified per feed |
| `listing_page` | post link not in the seen-set | none needed; the page is re-read each run |
| `hf_daily_papers` | paper not in the seen-set | last date; each run re-reads from the day before it to today |
| `arxiv_rss` | announce type `new` or `cross` | last announcement date; missed weekdays are backfilled from OAI-PMH |
| `github_releases` (disabled) | release id not in the seen-set | ETag per repo |
| `hn` | story id not in the seen-set | the last time read, minus an hour of overlap; attached threads are refreshed for 3 days |
| `hn_comments` | — | per thread: the comment count when its tree was read; re-read at 1.5× |
| `hf_paper_api`, `hf_paper_page` | — | the paper pool (the last 3 days of HF-listed papers); comments only for papers with comments that pass the use bar |
| `hf_org_watch`, `hf_trending` | model repo not in the seen-set; derivatives (by tag) only seen; format copies fold into the original's story; trending needs an org account and a model under 14 days old | — |
| `openrouter`, `github_org` | model id or repo not in the seen-set | — |
| `leaderboard` | a model's first time in the top N (the first run only records) | — |

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
src/debrief/adapters/   base.py + rss, listing_page, hf_papers, arxiv, github_releases (M1);
                        hn, hf_paper_pages, hf_hub, model_sources (M2)
src/debrief/store/      db.py (state DB), raw.py (raw store), migrations/*.sql
src/debrief/stories/    build.py (items -> seen-set, stories, mentions, snapshots), attach.py, signals.py
config/          settings.yml (UA, timeouts, rate buckets), sources.yml (every source)
tests/           unit and end-to-end tests (fixtures/ are real responses, trimmed); scripts/dev pytest -q
deploy/          Dockerfile, requirements*.in/.lock (see deploy/README.md)
scripts/dev      the dev container wrapper
docs/v2/         BRIEF.md, PLAN.md, SOURCES.md, this README
```
