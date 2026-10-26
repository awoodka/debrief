# Debrief: how it works

Debrief v2, this code, replaced the v1 collector and its five-seat council on 2026-10-25. It has three layers:

1. **Collector.** Deterministic Python that fetches, stores the raw responses, builds stories, and attaches discussion.
2. **Triage.** Filters, classifies, and caps, then writes the index. Rules first; Claude through Claude Code headless for the judgment calls.
3. **The skill.** One Claude Code skill reads the index and writes the brief.

Then a small deterministic builder turns each day's brief into the page at debrief.alexwoodka.com.

[PLAN.md](PLAN.md) is the approved design. [CRITERIA.md](CRITERIA.md) holds the rules for what makes it into the index. [BRIEF.md](BRIEF.md) is the original brief; where they disagree, the plan and the criteria win. [SOURCES.md](SOURCES.md) is the latest source probe.

Status: **M0–M5 are done.**
- **M0:** the source probe.
- **M1:** `collect` with the raw store, the seen-set and story building.
- **M2:** discussion attached to stories (Hacker News threads and comment trees, Hugging Face paper comments, newsletter and Techmeme coverage); signal snapshots (paper upvotes, repo stars, models built on a paper); the model-release sources; discussion levels and trend.
- **M3:** article pages (main text and in-article links) for new stories; `triage` with the deterministic candidate stage, merges and press events, the reported ledger, and the output contract (`schemas/`).
- **M4:** the scoring call: one Claude call (Sonnet 5.5, on the Max plan) that merges same-story candidates, drops what isn't relevant, and gives the rest a score and a one-line why; the most-discussed unattached HN threads join it; `calibrate` checks it against a labeled day.
- **M5:** LessWrong and the Alignment Forum (posts and their best comments, through the GraphQL API): posts attach to stories like any discussion, and the most-discussed unattached ones join the scoring call with a quota per source; Hugging Face's trending papers as a signal (`hf_trending_rank`).

Then the brief (the skill, `skill/`) and the site (`src/debrief/site/`), which took over debrief.alexwoodka.com from the v1 pipeline on 2026-10-25. v1's code was removed when v2 merged into `main` (it's in the history before that merge); its built pages are kept in the archive.

## Scheduled runs

The daily run is at 03:00 ET: collect every source, triage with the scoring call, write the brief, and publish the site. The 6-hour discussion polls run at 09:00, 15:00 and 21:00. Only the daily run uses the Claude plan. `deploy/README.md` has the details: the pinned checkout, `deploy/update.sh` to deploy, the shared lock, logs, and how a failure shows.

## Development setup

Everything runs in a Docker image, because the server has no Python venv support:

```sh
scripts/dev build                                  # build debrief-v2:dev (Python 3.13, locked deps, pinned Claude Code)
scripts/dev pytest -q                              # run the tests
scripts/dev python -m debrief probe-sources        # probe every enabled source once
```

`scripts/dev` mounts the repo at `/app` and the data dir at `/data`.

- **Data dir.** `DEBRIEF_DATA_DIR` sets it; the default is `/srv/data/debrief-v2`.
- **Source keys.** By default the container gets source keys from `./.env` (gitignored).
- **Claude.** `scripts/dev --claude …` swaps that for the Claude token file instead. The two are never passed together.
- **The lock.** It takes the data dir's `.run.lock`, the one the scheduled runs take, so a dev command waits for a running collect (up to 15 minutes), and a poll that fires during a dev command is skipped. The live data dir is the scheduled runs' data; to experiment, copy it and point `DEBRIEF_DATA_DIR` at the copy.

## Commands

| Command | What it does |
|---|---|
| `debrief probe-sources` | Requests every enabled source once, through the same rate limits the collector uses. Writes `docs/SOURCES.md` and `$DEBRIEF_DATA_DIR/probe/probe-<time>.json`. |
| `debrief probe-sources --source ID [--source ID]` | Re-probes just those sources (disabled ones included) and merges them into the latest full probe. |
| `debrief probe-sources --include-disabled` | Also requests disabled sources. |
| `debrief collect` | Fetches every collectable source, keeps the raw responses, and builds stories. Writes `runs/<date>/collect-<run>.md` and `.json`. Exit code 3 if any source failed. |
| `debrief collect --source ID [--source ID]` | Just those sources. |
| `debrief collect --replay YYYY-MM-DD [--into PATH]` | Rebuilds that day from its raw files into a new database, with no network. Default target: `<data>/replay/`. |
| `debrief collect --rebuild` | Rebuilds the whole state from all raw data after a parser change, keeping cursors, ETags and run history. The previous database is kept as a backup. Took 23 s for a day of 7 runs. |
| `debrief collect --cadence 6h` | The discussion poll: only sources whose `options.cadence` includes `6h` (Hacker News, LessWrong and the Alignment Forum, and their comments). It's what gives trend more than one observation a day. |
| `debrief bootstrap-bands [--days N]` | Reads back Hacker News history (default `bands.window_days`, 7), so discussion levels have a distribution to compare against from day one. 14 days was 14,346 stories in 56 requests on 2026-10-22. |
| `debrief story ID_OR_TITLE` | One story: its sources, signals, the paper use bar, attached threads and comments, coverage, discussion level and trend. |
| `debrief sections` | Which sources feed each section, straight from the config. |
| `scripts/dev --claude python -m debrief triage [--date YYYY-MM-DD]` | The day's candidates, the scoring call and the index, written to `runs/<date>/` (below). The data's time is the last fetch of that day's collect runs and the answer is cached, so re-running gives byte-identical files; `--at` sets the time instead. `--claude` gives the container the Claude token and no source keys. Exit code 3 when the run is degraded. |
| `debrief triage --no-model` | Rules only: each section's first `fallback_keep` candidates, marked degraded. `--model M` scores with another model; `--fresh` ignores the cache. |
| `scripts/dev --claude python -m debrief calibrate [--model M] [--fresh]` | Scores the labeled calibration set (`tests/fixtures/relevance/2026-10-23.jsonl`) and writes the agreement, every disagreement and the model's reasons to `calibration/<set>-<model>.md`. |
| `scripts/dev --claude python -m debrief brief run [--date D]` | The day's brief (below): `brief.json`, `brief.md` and `reported.json` in `runs/<date>/`. Exit code 3 when it's scan only. `debrief brief context\|check\|finalize` run its steps one at a time. |
| `debrief site build --out DIR` | The whole site as static files, into an empty folder (below). `deploy/run.sh site` builds and publishes it. |
| `debrief snapshot-fixtures` | Not built yet (M6). |

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
2. lookups about them (per-paper Hugging Face signals and trending ranks; article pages)
3. discussion sources (Hacker News, LessWrong, the Alignment Forum, Simon Willison)
4. comment trees (Hacker News comments, LessWrong comments, Hugging Face paper comments)
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
- **Percentile:** within each source, a story's best thread's points are compared with that source's threads from the last 7 days (`bands.window_days`). Only threads with at least `bands.floor` points count (10 for HN; 20 karma for LessWrong, 10 for the Alignment Forum), because most submissions are never discussed. Without the floor, HN's 75th percentile was 4 points. A source needs 200 threads in the window (`bands.min_observations`) before it has percentiles; LessWrong has about 100 AI posts a week and the Alignment Forum about 4, so they count through spread only.
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
| `lesswrong` (LessWrong, the Alignment Forum) | post id not in the seen-set | none: each run re-reads the last 72 hours (one request, about 60 posts) |
| `lesswrong_comments` | — | per post: the comment count when its comments were read; re-read at 1.5× |
| `hf_trending_papers` | — | none: one request for the top 100, daily |
| `hf_paper_api`, `hf_paper_page` | — | the paper pool (the last 3 days of HF-listed papers); comments only for the 30 best-ranked papers of the pool (triage's ranking), when they have comments |
| `article_pages` | — | none: a story gets its page once (a 4xx isn't retried; other failures are, while it's in the window) |
| `thread_pages` | — | none: daily, the page linked by each of the 40 most-discussed unattached HN threads, once |
| `hf_org_watch`, `hf_trending` | model repo not in the seen-set; derivatives (by tag) only seen; format copies fold into the original's story; trending needs an org account and a model under 14 days old | — |
| `openrouter`, `github_org` | model id or repo not in the seen-set | — |
| `leaderboard` | a model's first time in the top N (the first run only records) | — |

**The age guard.** On a first run, a feed hands over its whole history; OpenAI's has 1,315 entries. So an anchor whose publish date is more than `collect.window_days` (7) old when first seen is recorded as seen, not made a story.
- For feeds and APIs, whose dates are reliable, the guard always applies.
- For scraped pages, it applies only on the source's first run, because a scraped date can be wrong. After that, a link missing from the seen-set is itself the evidence that the post is new.
- Listing pages also fetch only their 5 newest links on a first run.

**Threads that attach to nothing.** Most of Hacker News's busiest threads link pages none of our sources collect: on 2026-10-23, 198 of 202 with 10 points or more. On daily runs, the 40 most-discussed of the last 48 hours (`triage.yml` `discussion.sources.hn.prefetch`) get the same treatment as stories: `hn-thread-pages` fetches the page each links to (a GitHub repo gives its README; posts on X, PDFs, videos and Ask/Tell HN posts are skipped), stored under the thread's id (`hn:<item>`), and `hn-items` reads their comment trees. Triage scores 30 of them, reading those pages. Most LessWrong posts are original writing that links to none of our stories, so the same goes for them, with their own quota: 5 from LessWrong and 5 from the Alignment Forum (by karma), with the post's body as its text, so there's no page to fetch; `lesswrong-comments` reads their comments.

**LessWrong and the Alignment Forum (`adapters/lesswrong.py`).** One site behind two hosts. Every Alignment Forum post is also on LessWrong with the same karma, so the LessWrong source leaves those to the Alignment Forum source and a cross-post counts once. LessWrong posts are left out only when their tags say they're about something else: another core tag (Rationality, World Modeling, Practical...) and not AI. Many AI posts carry no tags at all, or only a subtopic like AI Control. A post can attach to a story by its link-post target and its first 3 body links; a numbered roundup ("AI #191") attaches by none of its links, since they're the week's news, not its subject. Comments come from LessWrong (the Alignment Forum shows only its members'): the 8 best top-level comments by karma, plus the 3 most-replied others.

**Which comments are kept.** From each comment tree, comments that say too little are left out first: under 150 characters once links are taken out ("This.", a joke, a bare link). Of the rest, the database keeps HN's 8 top-ranked top-level comments (LessWrong's 8 best by karma) and the 3 most-replied others. A story's detail file shows 4 top-ranked and 2 most-replied per thread, each labeled with why it was picked. Replies aren't kept, and the full tree stays in the raw store.

**Article pages (`adapters/article_pages.py`).** Most lab and press feeds carry a title and a teaser only, so after the anchors, `collect` fetches the page of each new announcement and press story (published in the last 2 days), and the model card or README of each new release. It keeps the main text (trafilatura, up to 8,000 characters) and the links inside it, in `story_pages`. Triage groups and merges by those links, and the index's full text comes from there. Listing-page sources fetch their posts anyway, so those carry their page and aren't requested twice. robots.txt is checked first, and paywalled domains (`article_pages.skip_domains`) are never requested.

## How `triage` works

The rules are in [CRITERIA.md](CRITERIA.md) and every threshold is in `config/triage.yml`. The code is `src/debrief/triage/`. Each stage only drops, and every drop goes to `triage_log.jsonl` with its stage and reason:

1. **pool.** Per section, the stories in its window that have an enabled source of that section: papers first seen in the last 3 days; releases first seen in the last 48 hours (org-watch repos created in them); announcements and press published in the last 48 hours.
2. **rules.** No title, or a blocklisted domain or pattern (promotions, gaming, event promos, listicles; papers are exempt).
3. **merge** (`triage/merge.py`, by links and titles only):
   - An announcement that links exactly one artifact published within 7 days of it is one story with that artifact. A lab post about its model is filed as a release; a lab post about its paper stays an announcement and takes the paper out of the paper pool.
   - Announcements with the same title are one story (a cross-post).
   - Press articles with the same title, or where one links the other from another site, or that both link the same URL off their own sites, are one event. Profile, home and index pages don't count.
4. **ledger** (`triage/ledger.py`). A story already in a brief stays out, unless it has a new thread at or above its source's floor since; then it's back as `update`. The ledger is the skill's `runs/<date>/reported.json` files, or, until the first one exists, earlier days' indexes.
5. **order and cap**, per section: papers by the use bar, then rank fusion (at most 2 per organization, 15 in all); releases by tier, then likes (10); announcements with sources taking turns, tier 1 first (35); press events by outlets, then Techmeme, then discussion level, then newest (30).
6. **threads.** The most-discussed threads of the last 48 hours that attached to no story join the discussion section, with a quota per source (`discussion.sources`): 30 from Hacker News, 5 from LessWrong, 5 from the Alignment Forum, each at least its source's floor and not already in a brief. Their ids are `<source>:<item>` (`hn:50212065`, `lesswrong:ie7bSp5ZDwYkCQxuv`).
7. **score** (`triage/score.py`, CRITERIA.md "The model call"). One call over every candidate, with `config/relevance.md` as the definition and the titles of recently reported stories as a reference list. For each candidate, the model answers `same_as`, `why`, `relevant` and `score`. Code then merges each `same_as` group (one that reaches a reported story brings it back as an `update`), drops every group the model found not relevant, and keeps the rest with their score and why. The call runs through Claude Code with no tools, an explicit system prompt, no settings, MCP servers or slash commands, in an empty directory; fetched text goes in as data. Triage refuses to run with `ANTHROPIC_API_KEY` set, so it never bills the API.
8. **keep.** Only when the call fails (after one retry) or with `--no-model`: each section's first `fallback_keep` candidates (10, 10, 15, 15, none of the threads), and the run is `degraded`.

The index is grouped by section, then by score, then newest first, never by volume. Stories the model dropped are written only to `triage_log.jsonl`; the run report gives their count. Each line's `gist` is Hugging Face's AI summary for a paper, and otherwise the first sentence of the story's summary. A Hugging Face model's stored summary is repo metadata ("text-generation · transformers · license mit"), so its summary in the index is its model card's first prose paragraph instead, and the metadata moves to `model_info`. Every file is validated against its schema before anything is written, and the files are swapped in together.

## How the brief works

The brief is layer 3: the day's triage output becomes an article-style brief, written in the voice of a senior advisor keeping an informed peer current on the field. The skill decides what goes in the article; code does everything around it. The brief is data, not a page: `brief.json` is what a page renders, and styling lives with the page.

`debrief brief run --date D` (`src/debrief/brief/run.py`) runs four steps:

1. **context.** Writes `brief_context.json`, the skill's first read. It holds triage's status for the day, failed sources, the follow-ups (`status: update`), what the last 3 briefs covered, and the limits.
2. **The skill.** It runs as a Claude Code session in the run folder: `claude -p "/debrief:brief D"`, on Opus 5.5, on the Max plan. The skill is a plugin folder, `skill/` (`SKILL.md`, plus `references/voice.md` and `references/format.md`), loaded with `--plugin-dir` and `--setting-sources ""`, so no user or project settings, commands or agents load. The session's limits:
   - **Tools:** Read, Write, Edit and WebFetch, with no shell.
   - **Allowed:** reading the skill folder, writing `brief.draft.json`, and WebFetch.
   - **Denied:** reading `triage_log.jsonl` (triage's drops are never shown to the skill), the collect reports, the session's own logs and old drafts.
   - **WebFetch:** a hook allows at most 5 per run (the 6th is refused), and only to sites today's stories link to, so a link planted in a comment can't be fetched. Every call is logged in `brief_fetches.jsonl`.
   - **A Stop hook** runs the check (`debrief brief check`). While the draft fails it, the session can't end, and the errors go back to the model.
3. **The draft.** The model writes only `brief.draft.json` (`schemas/brief_draft.schema.json`): headline, `what_happened` (3–6 events tied to story ids), lead, sections (heading, body, stories, an optional for/against/read split, verbatim quotes), updates, notes, and why it fetched each page. The check rejects:
   - ids that aren't in today's index;
   - quotes that aren't word for word in the detail files;
   - links or ids inside prose;
   - item counts out of range;
   - long headings;
   - an article far over the length aim (1,500–2,500 words).
4. **finalize.** Writes three files:
   - **`brief.json`** (`schemas/brief.schema.json`): the draft, plus the **scan** (every story triage kept, headline and gist, marked when the article covers it), each story's links and threads, quote attributions, the fetch log, and the session's model, tokens and time. Every link comes from the data, never from the model.
   - **`brief.md`**, the same as plain text.
   - **`reported.json`**, the ledger, written last: every story in the scan.

   If the session fails or its draft never passes the check, the brief is **scan only** and no `reported.json` is written, so those stories come back the next day.

The session's stream is kept in `brief_session.jsonl` and summarized in `brief_session.json`. `debrief brief context|check|finalize` run the steps one at a time.

## How the page works

`debrief site build --out DIR` (`src/debrief/site/`) is deterministic and stdlib only. It needs no network and no Claude, and the same briefs always give the same bytes:

- **`<date>/index.html`:** each day's page. `page.py` fills `templates/day.html`, which carries v1's look (its CSS, light and dark), with the brief:
  - the headline, then `what_happened` as the arrow list;
  - the lead with its drop cap;
  - the sections, each with its for/against/my-read panes, its quotes (attributed and linked to the thread), and a Sources line of links with discussion chips;
  - Updates and Notes;
  - the scan of every story kept, grouped by section. A covered story links to where the brief covers it.

  A scan-only brief gets a note in place of the article.
- **Safety:** every string is escaped. Prose gets only `**bold**` and `*italic*`. Links come only from the data's url fields, and only http(s). The page has no script, and a Content-Security-Policy says so.
- **For later features:** each section has `id="s-N"` and `data-stories` (its story ids), and each story row has `data-story`.
- **`<date>/brief.json`:** the day as data, the brief minus what's only for us (the session, the check's warnings, refused fetches).
- **`index.html`:** the archive list, newest first, from `templates/index.html`.
- **The old briefs:** v1's built pages (2026-09-14 to 2026-10-25), copied once into `<data>/site/v1/` when v2 took over the site, are listed alongside unchanged. A v2 brief wins a date that both have.

`deploy/run.sh` publishes the site as a new release in `/srv/www/debrief`, which Caddy serves (deploy/README.md, "The site").

## Data layout

```
$DEBRIEF_DATA_DIR/
  raw/<date>/<source>/<run>.jsonl.gz   every response, byte-exact, pruned after raw_retention_days (60)
  state/debrief.sqlite                 cursors, ETags, the seen-set (items), stories, mentions, runs
  runs/<date>/collect-<run>.{md,json}  the per-run report
  runs/<date>/stories_index.jsonl      triage: one line per kept story (schemas/index_line.schema.json)
  runs/<date>/stories/<id>.json        triage: everything about one kept story (schemas/story.schema.json)
  runs/<date>/triage_log.jsonl         triage: every story that entered a pool, and where it stopped
  runs/<date>/run_report.md            triage: a JSON header (schemas/report_header.schema.json), then tables
  runs/<date>/brief.json               the brief: what a page renders (schemas/brief.schema.json); also brief.md
  runs/<date>/brief.draft.json         the skill's draft; brief_context.json, brief_fetches.jsonl, brief_session.*
  runs/<date>/reported.json            written by brief finalize: every story the brief showed (the ledger)
  site/v1/<date>/index.html            the v1 pipeline's built pages, copied once at the takeover
  cache/score/<key>.json               the scoring call's answers, keyed by a hash of everything the call saw
  calibration/                         calibrate reports
  probe/                               probe-sources results
  replay/                              databases built by --replay
```

## Being a polite client

Every request goes through `src/debrief/http.py`:

- **Rate buckets.** Requests go out one at a time. Requests in the same bucket are spaced by its `interval_s` (`config/settings.yml`). A bucket entry is a host, which covers its subdomains, optionally with a path prefix.
- **arXiv has one budget.** The terms allow one request every 3 s across all our machines, so nothing else on this server should call arXiv while a collect runs.
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
                        hn, hf_paper_pages, hf_hub, model_sources (M2); article_pages, thread pages (M3, M4);
                        lesswrong, hf trending papers (M5)
src/debrief/store/      db.py (state DB), raw.py (raw store), migrations/*.sql
src/debrief/stories/    build.py (items -> seen-set, stories, mentions, snapshots, pages), attach.py, signals.py
src/debrief/triage/     candidates.py, merge.py, ledger.py, score.py, output.py, run.py, calibrate.py
src/debrief/brief/      day.py, context.py, check.py, hooks.py, finalize.py, render.py, run.py
src/debrief/site/       page.py (a day's page, the archive list), build.py (the site)
skill/           the brief skill, a Claude Code plugin: skills/brief/SKILL.md and references/
templates/       the site's pages: day.html and index.html (v1's look)
config/          settings.yml (UA, timeouts, rate buckets, collect), sources.yml (every source),
                 triage.yml (windows, caps, merge and blocklist rules, scoring), relevance.md (what's relevant)
prompts/         score_v1.md: the scoring call's instructions (relevance.md is appended)
schemas/         the output contract: index line, story detail, triage log line, report header, reported.json,
                 the brief's draft and brief.json
tests/           unit and end-to-end tests (fixtures/ are real responses, trimmed); scripts/dev pytest -q
deploy/          Dockerfile, requirements*.in/.lock (see deploy/README.md)
scripts/dev      the dev container wrapper
docs/           BRIEF.md, PLAN.md, CRITERIA.md, SOURCES.md, this README
```
