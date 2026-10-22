# Debrief v2: collector and triage

## Context

Debrief's current collector scores items for "traction" with hand-weighted formulas. Its output feeds a nine-agent council. The user is replacing all of that with three layers:

1. **Collector.** Deterministic Python, lossless.
2. **Triage.** Filtering plus Claude judgments.
3. **One Claude Code skill.** It writes the article; the user builds it separately.

This plan covers layers 1 and 2. It follows the user's brief, which another Claude session drafted and which was pasted in this conversation. Step 1 below saves that brief into the repo as the spec. The output contract in brief §8 is the interface to the skill.

The decisions taken in this conversation (2026-10-22) change the brief in several places. Where this plan and the brief disagree, this plan wins.

## Decisions taken in this conversation

| Topic | Decision |
|---|---|
| Code home | Same repo (`awoodka/debrief`), new package `src/debrief/`, on branch `v2`. The old `collector/` and the nightly run stay untouched until the user approves a v2 index. |
| Git | The user creates a fine-grained GitHub token with Contents read/write on this repo only. Claude commits and pushes `v2` from `~/repo-work/debrief`. The deploy checkout (`/srv/apps/debrief/repo`) keeps its read-only token. |
| Triage model | The Claude Max subscription, through Claude Code headless: `claude -p --model claude-haiku-5-5 --tools "" --json-schema … --output-format json`. The model is configurable (Sonnet 5.5 is the alternative). **No local models and no embeddings.** If Claude is unavailable, triage falls back to rules-only and marks the run `degraded`. |
| Polling | Discussion sources every 6 hours. All sources daily. |

Verified on 2026-10-22 inside the server image (Claude Code 2.1.270):
- Haiku 5.5 returned schema-valid `structured_output` for 3 items in about 2 s, with about 1k cached input tokens.
- The CLI warns `unrecognized_model` for `claude-haiku-5-5` but passes the ID through, and the call works. Bump the CLI pin in M4.
- A project skill also runs headless (`claude -p "/name"`).

## Audit of the current collector (brief §9.1)

**Where the traction scoring goes wrong:**
- **Divergence punishes official announcements.** It's computed as in-field minus mainstream, and every lab post gets +12 "mainstream". Yet most non-paper items have zero measured in-field signal. Their "substance" for Reddit posts is a fixed constant per post type (6/3/0), not a measurement.
- **It's circular.** Only papers already on HF Daily or mentioned in discussion get enriched (`enrich.candidates`), so only already-noticed papers can show substance. Everything else scores 0 and is ranked by upvotes.
- **Coverage matching is unreliable.** It's substring matching: titles, plus the last URL segment if it's ≥10 characters, searched inside other items' text (`score.cross_reference`). That gives false hits on generic slugs and misses paraphrased titles.
- **The scales don't add up meaningfully.** Bucketed HN points, capped newsletter counts, log-scaled HF artifacts and Reddit constants are summed with untuned "v1 starting point" weights.
- **Volume drives the non-paper sections.** They're ranked by `in_field + mainstream + insider` (`digest.build`), which is the volume bias the brief forbids.
- **Time windows instead of state.** With a 4-day window for news and 14 for papers, items repeat for days. There is no seen-set. The arXiv cap of 2,500 truncates the 14-day window to about 8 days.
- **Failures look like quiet days.** VentureBeat failed with 429 on 39 of 39 runs, and about 44% of Reddit fetches failed. These show up only as a log line.

**Reuse:**

| From | Becomes |
|---|---|
| `collector/http_util.py` (session, retry/backoff, sqlite cache) | Starting point for `debrief/http.py`. It adds per-host rate limits, Retry-After handling, ETag/Last-Modified, and a contact UA. |
| `collector/schema.py` (`ARXIV_ID_RE`, `canonical_key`) | Seeds `debrief/canon.py` |
| `collector/sources/rss.py` (feedparser + tag strip) | `adapters/rss.py` |
| `collector/sources/hf_daily.py` (endpoint, date loop) | `adapters/hf_papers.py` |
| `collector/sources/hackernews.py` (Algolia calls, `AI_RE`) | `adapters/hn.py`, plus the AI pre-filter for the attach pass |
| `collector/sources/github_trending.py` | `adapters/github_trending.py` (M5) |
| `collector/fulltext.py` (trafilatura + BS4 fallback, GitHub README, HN item) | Post-body extraction for `listing_page`, and HN comment trees |
| `collector/entities.py` `BRANDS` | Seeds `config/aliases.yml` |
| `collector/noise.py` regexes | Seeds `config/blocklist.yml` |
| `collector/config.py` feed URLs | Seeds `config/sources.yml` |

**Delete at cutover, not before:** `score.py`, `store.py`, `digest.py`, `entities.py` resolution, `enrich.py`, `sources/reddit.py`, `sources/producthunt.py`, and the old `bluesky.py`. Conference deadlines (`events.py`) aren't in the brief, so they're dropped; re-adding them later is one config entry.

## Changes to the brief

1. **Triage uses Claude, and there are no embeddings.** Each embedding job is replaced:
   - **§5.3 step 3** (attach by similarity) becomes a conservative word-overlap fallback in the collector, plus a Claude **attach pass** in triage.
   - **§5.4** (discussion-native clusters) becomes the same attach pass, which also groups unmatched AI-related mentions into topics. A topic becomes a story at ≥3 independent sources, as in the brief.
   - **§6.3** (seed similarity) becomes the rules prefilter plus Claude relevance inside §6.4.
   - The profile seeds become `config/profile.md`, a rubric of wanted and unwanted topics placed in the triage prompt. The user supplies it, and an example ships.
   - The collector still never calls an LLM. Attachments that triage makes are stored with `match_method: "llm"`.
2. **Repeatability comes from caching, not temperature.** Claude Code exposes no temperature control. Triage results are cached by `(story_id, content_hash, prompt_version, model)`, so a re-run is identical. Tests use a stub backend.
3. **Raw storage is batched and compressed.** It's `raw/<date>/<source_id>/<fetch_ts>.jsonl.gz`, one record per line, with a retention setting (default 60 days). Per-item files would mean thousands of files a day, and `/srv/data` has 11 GB free. A `raw_ref` is `raw/<date>/<source>/<fetch_ts>.jsonl.gz#<item_key>`. `snapshot-fixtures` copies a day out to keep permanently.
4. **arXiv gaps get backfilled.** The category RSS feeds show only the latest announcement, so a missed day is lost from RSS. When the cursor shows a gap, backfill through OAI-PMH (`ListRecords from=<cursor>`, with resumption tokens). M0 confirms the current arXiv policy (tightened 2026-10-01) and the endpoints.
5. **The contact address in the User-Agent comes from config** (`DEBRIEF_CONTACT`). It's never hardcoded, and the default is the repo URL.
6. **Development runs in Docker.** The host has no venv or pip (`ensurepip` is missing). A repo `deploy/Dockerfile` builds `debrief-v2:dev` from `python:3.13-slim`, with the same checksum-pinned Claude Code as today and locked deps. `scripts/dev <cmd>` runs commands in it with the repo and the data dir mounted. The same image later becomes production.
7. **The `reported` ledger contract.** The skill (or publish step) writes `runs/<date>/reported.json` as `{"date", "story_ids": [...]}`. Triage ingests every such file into the `reported` table. Neither layer writes the other's state.
8. **Dropping Reddit loses r/LocalLLaMA model drops.** The HF Hub org watch and trending models (M5) replace them. The old nightly's Reddit feeds will start failing on 2026-11-13; that's fail-soft and needs no action.

## Code layout

```
pyproject.toml                  # package "debrief" (src layout), console script "debrief"
src/debrief/
  cli.py                        # collect | triage | probe-sources | snapshot-fixtures | bootstrap-bands | recall
  config.py                     # load + validate the YAML configs and env (DEBRIEF_DATA_DIR, DEBRIEF_CONTACT, ...)
  http.py                       # polite client: per-host token bucket, UA, conditional GET, Retry-After, backoff
  sanitize.py                   # strip HTML, control and zero-width chars, length caps
  canon.py                      # URL canonicalization + story_id derivation (brief 5.1/5.2)
  store/db.py, store/schema.sql, store/raw.py
  adapters/base.py              # fetch(ctx, cursor) -> FetchResult;  parse(record) -> [Anchor|Mention|Coverage|Engagement]  (parse is pure)
  adapters/rss.py listing_page.py arxiv.py hf_papers.py hn.py ...   (one module per adapter TYPE; sources are config)
  stories/build.py attach.py signals.py
  triage/rules.py claude.py attach_pass.py judge.py quotas.py output.py report.py
  probe.py fixtures.py
config/   sources.yml settings.yml aliases.yml blocklist.yml trusted_voices.yml profile.example.md
prompts/  triage_v1.md attach_v1.md
schemas/  index_line.schema.json story.schema.json report_header.schema.json triage.schema.json attach.schema.json reported.schema.json
tests/    unit + integration;  tests/fixtures/<adapter>/*.{xml,json,html};  fixtures/days/<date>/ (frozen raw)
deploy/   Dockerfile, requirements.lock;  scripts/dev
docs/v2/  BRIEF.md (the brief, verbatim), SOURCES.md (M0 probe table), README.md
```

## Storage: `state/debrief.sqlite` (WAL)

| Table | Holds |
|---|---|
| `source_state` | source_id, cursor JSON, last_ok_at, last_status, last_error, consecutive_failures |
| `http_validators` | url → etag, last_modified, checked_at (for conditional requests) |
| `fetches` | per run and source: url, status, http_status, n_items, error, raw_ref |
| `items` | the seen-set: (source_id, item_key) → first_seen, last_seen, content_hash, raw_ref |
| `stories` | story_id, kind, section, title, org, authors, published_at, first_seen, last_changed_at, tier, summary, content_hash, artifact/code urls, merged_into |
| `story_urls` | canonical url → story_id |
| `mentions` | mention_id, source_id, item_key, story_id (nullable), url, title, text, author, posted_at, parent, kind (thread, post, comment), match_method, match_score, trusted |
| `engagement` | (mention_id, observed_at) → points, comments, likes, reposts, upvotes |
| `coverage` | story_id, source_id, url, date, is_mainstream |
| `reported` | story_id, reported_on |
| `triage_cache` | (story_id or mention batch key, content_hash, prompt_version, model) → result JSON, usage |
| `runs` | run_id, kind (collect, poll, triage), date, status (ok, degraded, failed), report JSON |

## Collector behaviour

- **Cursors per source.** HN uses `created_at_i`. HF Daily Papers uses its last date. arXiv uses its last announcement date, with OAI-PMH backfill. RSS, Atom and listing pages use the seen-set plus ETag. A late run fills the gap; an early run adds nothing new.
- **Raw first.** Every response is written to the raw store before parsing. `collect --replay <date>` re-parses raw without network. It's the dev loop and the determinism test.
- **Story building (§5).** Canonical IDs come from `canon.py`. Mentions attach in order:
  1. exact URL or ID match
  2. entities: arXiv, HF and GitHub IDs found in text, plus `aliases.yml`
  3. conservative title word-overlap against stories from the last N days (threshold in config)

  Each attachment records its method and score. Unmatched mentions stay unattached until triage.
- **Discussion substance (§5.5).** Thread refs, top comments by score, a few high-reply or dissenting comments, and trusted voices, each with per-thread and per-story caps. Full trees stay in raw.
- **6-hourly poll** (`collect --cadence 6h`). Fetches discussion sources only, and re-observes engagement for threads attached to stories from the last 3 days. That gives trend at least two points a day.
- **Bands (§8.1).** A mention's percentile is computed against its source's threads in the trailing 14 days. A story's per-source percentile is the max over its threads. Then:
  - `none`: no discussion
  - `some`: below the 75th percentile
  - `notable`: ≥75th percentile, or spread ≥2
  - `heavy`: ≥95th percentile, or spread ≥3 with ≥75th

  Thresholds are in `settings.yml`, documented in code and the README. `bootstrap-bands` seeds HN's distribution from 14 days of Algolia history. A source with fewer than N observations gets `pctl: null`, and its band rests on spread only.
- **Trend.** The engagement rate between the last two observations is compared with the previous interval. `rising` means at least 1.5×, or a new source joined. `falling` means at most 0.5×. Otherwise `flat`. Fewer than 3 observations and no new source gives `unknown`.

## Triage (`debrief triage --date`)

0. Ingest new `reported.json` files.
1. **Merge (§6.1).** Join an announcement or blog story that links exactly one artifact ID published within ±7 days into that artifact's story. Record `merged_into`.
2. **Hard rules (§6.2).** Set status to `new`, `update` or `continuing` (the "meaningful change" rule is in config). Apply the blocklist. Drop items with no title or content. Exclude unattached discussion.
3. **Attach pass (Claude).** AI-related unattached mentions (pre-filtered with `AI_RE`) go to Claude in batches of about 50, with the candidate story titles as a cached prefix. Claude maps each one to a `story_id` or to a topic label. Topics spanning ≥3 independent sources become `cluster:<hash>` stories.
4. **Judge (§6.4, Claude).** Stories are batched about 25 per call. The output schema is the brief's (relevant, novelty, kind, gist, discussion_gist, reason) plus `relevance: high|medium|low`. The prompt is `prompts/triage_v1.md` plus `config/profile.md`, with story data in delimited fields. Stories marked `relevant:false` or `rehash` are dropped, except tier 1, which is flagged instead. Results are cached. Token usage goes in the report.
5. **Quotas and wildcards (§6.5).** Section budgets (15, 12, 10, 8, 8) plus about 5 wildcards, all in config. Ranking happens only within a bucket: tier, then relevance, then novelty, then band, with volume as the last tiebreaker.
6. **Outputs (§8).** `stories_index.jsonl`, `stories/<id>.json`, `triage_log.jsonl`, and `run_report.md` with a JSON header. Every file is validated against `schemas/` before it's written.
- **Degraded mode.** On a Claude failure (auth, quota, timeout, or invalid JSON after one retry), stages 3–4 are skipped and the run uses rules-only ranking. The report header shows `"status":"degraded"` and the reason. Triage refuses to start if `ANTHROPIC_API_KEY` is set.
- **Isolation.** In production, triage runs in its own container: the Claude token, the data mount, no source keys. The collector container gets the source keys and never the token.

## Milestones (each ends with tests, a demo run and a check-in with the user)

- **M0: setup and source probe.**
  - Save the brief to `docs/v2/BRIEF.md` and create the `v2` branch.
  - The user creates the token. Claude scopes it to this checkout's credential helper only.
  - Build the `deploy/Dockerfile` and `scripts/dev`.
  - Build the config loader, `http.py`, and `probe-sources`. It hits every source in the brief once, checks robots.txt for listing pages, records the current arXiv policy, measures HN daily volume, and confirms the Claude CLI probe.
  - *Done when:* `docs/v2/SOURCES.md` has a table (endpoint, auth, rate-limit notes, sample item, status) with every "(verify)" resolved. **Stop and review it with the user.**
- **M1: storage and primary anchors.**
  - Schema, raw store, cursors, `canon.py`.
  - Adapters: rss (which also covers GitHub releases Atom), listing_page, hf_papers, arxiv (RSS plus OAI backfill).
  - Story building, `--replay`, and a basic run report.
  - *Done when:* two consecutive runs create no duplicate stories, and replaying a day reproduces identical story rows.
- **M2: discussion.**
  - HN adapter with comment trees, and HF paper comments.
  - The three-step attach, engagement snapshots, the 6h poll, bands, trend, and `bootstrap-bands`.
  - *Done when:* about 10 hand-picked HN-discussed papers and releases show attached threads with plausible bands, and trend is known after two polls.
- **M3: rules triage and the output contract.**
  - Steps 0–2, 5 and 6, rules-only.
  - JSON Schemas and `reported.json` ingestion.
  - *Done when:* the index validates, has 40–60 lines, and the report lists drops by stage and any tier-1 drops or flags.
- **M4: Claude triage.**
  - Attach pass, judge, cache, degraded mode, usage reporting.
  - Bump the Claude Code pin so it recognizes the 5.5 model IDs.
  - *Done when:* a full live day triages within the reported token use; a re-run is 100% cache hits with byte-identical output; and a forced failure produces a `degraded` index.
- **M5: remaining sources,** in value order:
  - HF Hub org watch and trending; Bluesky curated accounts; Lobsters; LessWrong/AF
  - references (newsletters → `covered_by` and `mainstream_at`); press; GitHub trending and stars
  - PyPI/npm; YouTube and podcast RSS; status pages; leaderboards; policy; Mastodon
  - Discord, IMAP, Jetstream and bioRxiv ship disabled.

  Each is an adapter (only if it's a new type), a config entry, and a fixture test.
- **M6: fixtures and recall.** Build `snapshot-fixtures` and `recall` against a hand-labeled `must_include.yml`. *Done when:* the report names each missed story and the stage that dropped it.
- **Not in this plan:** systemd units for the 6h, daily and triage runs; connecting the skill; deleting the old pipeline. All three come after the user reviews a v2 index.

## Safety while developing

- **Separate data.** v2 data lives in `DEBRIEF_DATA_DIR=/srv/data/debrief-v2`. The CLI refuses a data dir that contains the old pipeline's `debriefs/` folder.
- **The nightly run is untouched.** `/srv/apps/debrief` stays as it is. Don't run `update.sh`, and `main` is unchanged until cutover.
- **Polite during development.** Iterate with `--replay` and fixtures, not live refetches. Probe each source once.
- **Untrusted text.** Fetched text is never treated as instructions. Triage has no tools and schema-locked output. All text is sanitized and length-capped in its own data fields.

## Verification

- **Unit tests (`pytest`, in `scripts/dev`):**
  - a URL canonicalization and story_id table
  - adapter `parse()` against recorded fixtures
  - cursor advance and gap backfill
  - the attach ladder
  - band and trend math on synthetic engagement series
  - quota ranking never ordering by volume first
- **Integration tests:**
  - a frozen fixture day through `collect --replay`, then `triage` with the stub backend, compared byte-for-byte with a golden `stories_index.jsonl`
  - two-run no-duplicates
  - schema validation of every output
  - degraded mode with the stub set to fail
- **Live demos per milestone** against `/srv/data/debrief-v2`, plus the user's review of the M0 table, the M2 attachment examples and the M3/M4 index.
- **Production stays healthy.** `systemctl status debrief.service` and the site keep showing the old pipeline publishing every day.

## M0 results (2026-10-22)

The full table is in [SOURCES.md](SOURCES.md): 90 sources, 71 ok, 18 disabled with a reason, and 1 failed (Semantic Scholar, which needs a key). The findings below change the plan.

- **arXiv's 2026-10-01 change is about submissions, not access.** Submitters are capped at 2 papers a month ([arXiv blog](https://blog.arxiv.org/2026/10/01/updated-rate-limit-policy/)). The access terms are unchanged ([terms of use](https://info.arxiv.org/help/api/tou.html)): one request every 3 s, on a single connection, across all our machines. That budget is shared with v1's collector.
- **arXiv lookups use OAI-PMH, not the query API.** `export.arxiv.org` robots.txt disallows everything. `oaipmh.arxiv.org` is allowed and does both jobs: `ListRecords` for backfill and `GetRecord` for lookups by id.
- **robots.txt is parsed per RFC 9309.** The standard library's parser got GitHub's rules wrong. The policy and the switches it caused are in README.md ("Being a polite client"):
  - GitHub releases come from the REST API.
  - PyPI uses per-project RSS.
  - GitHub trending is scraped from plain `/trending` only.
  - Lobsters and YouTube feeds are disabled.
- **Advertised feeds replace scraping.** Mistral, Ai2, Thinking Machines and Together publish RSS. Scraping is left only for Anthropic news and research, Meta AI, Cohere, The Batch and the EU AI Office; the EU AI Office is blocked anyway.
- **Anthropic engineering can't be discovered without running JavaScript.** Its posts now appear under `/research/`, so it's disabled.
- **Hugging Face paper comments have no API.** They're embedded in each paper page. `daily_papers` gives `numComments`, so only pages with comments get fetched, in a separate rate bucket. HF advertises 100 page requests and 500 API requests per 5 minutes.
- **LMArena** blocks plain clients (403), but its official `lmarena-ai/leaderboard-dataset` on HF works through the datasets-server rows API.
- **Inactive or blocked:**
  - AINews: no issue since 2026-09-10.
  - Qwen blog: no post since 2025-09-22; Qwen now ships through its HF org.
  - VentureBeat: 429 on every request.
  - GlobeNewswire (Akamai), EU AI Office (CloudFront) and Business Wire (timeout): unavailable to a plain client.
- **New source:** `github-new-ai-repos`, a GitHub search for repos created in the last week with `topic:llm` and more than 50 stars.
- **SEC EDGAR uses the official API.** That's `data.sec.gov/submissions/CIK<id>.json`, for an allowlist of 8 public AI companies, all verified by name. The `cgi-bin/browse-edgar` feed returned 200 once the User-Agent carried an email, but robots.txt disallows it.
- **HN volume** is about 1,200 stories a day, small enough to store every story losslessly.

Status of the user's items (2026-10-22):
- **Done:** the push token (in a repo-local credential store), the collector `GITHUB_PAT`, and `DEBRIEF_CONTACT`. The last two are in the gitignored `.env`.
- **Still open:**
  - a free Semantic Scholar key
  - optionally Artificial Analysis and YouTube Data API keys
  - lists to curate: GitHub repos, HF orgs, PyPI/npm packages, SEC companies, Bluesky/Mastodon accounts, podcasts, trusted voices
  - the user's review of SOURCES.md and the code before M1 starts

## M1 results (2026-10-22)

`collect` runs the five primary adapters: `rss`, `listing_page`, `hf_daily_papers`, `arxiv_rss` with OAI-PMH backfill, and `github_releases`. That covers 40 sources.

On live data, starting from empty:

| Run | Time | Requests | New stories |
|---|---|---|---|
| First | 1 min 47 s | 81 | 923 |
| Second, two minutes later | 52 s | 55, of which 34 answered 304 | 4, all published between the runs |

The first run's 923 stories are 704 papers, 99 announcements, 94 press/policy and 26 releases. The age guard kept 2,877 old feed entries out.

The M1 acceptance checks:
- **No duplicates across the two runs.** No two stories share an artifact URL.
- **Replay is exact.** `collect --replay 2026-10-22` rebuilt identical stories, items, mentions and story-URL rows.

Findings that changed the code:

**arXiv OAI-PMH.** In the `arXiv` metadata format every record carries `<updated>`, so new papers can't be told apart. In `arXivRaw` they can: a new paper has only a v1, dated within a week of the datestamp. Measured on 2026-10-20, most new papers lag 1–3 days (median 3), with a tail of edits up to years old.

**Meta's blog posts** have neither date tags nor article markup. So:
- A fetched post only needs a title.
- Dates fall back to trafilatura's text finder. It misdated 1 in 5 Meta posts, which is why the age guard for scraped pages runs only on a source's first run.

**Anthropic's listing** mixes posts with category pages. Seven category slugs are excluded in `sources.yml`.

**Hugging Face** caps a day at 50 papers unless asked for `limit=100`; Oct 21 had 64.

Left for later milestones:
- The same announcement cross-posted under different URLs (OlmoEarth on both the HF and Ai2 blogs) is two stories until the triage merge (§6.1, M3).
- PR Newswire's general feed is mostly off-topic press releases, which triage filters out.
