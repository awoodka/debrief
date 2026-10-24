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
- **Bands (§8.1).** A mention's percentile is computed against its source's threads in the trailing 7 days (was 14; cut 2026-10-23). A story's per-source percentile is the max over its threads. Then:
  - `none`: no discussion
  - `some`: below the 75th percentile
  - `notable`: ≥75th percentile, or spread ≥2
  - `heavy`: ≥95th percentile, or spread ≥3 with ≥75th

  Thresholds are in `settings.yml`, documented in code and the README. `bootstrap-bands` seeds HN's distribution from `bands.window_days` of Algolia history. A source with fewer than N observations gets `pctl: null`, and its band rests on spread only.
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

## Revision after M1: triage by section (2026-10-22)

The inclusion rules are now in [CRITERIA.md](CRITERIA.md), which supersedes this plan's triage stages 3–5 and the brief's §6.3–6.5. In short:
- **Releases:** keep all real ones.
- **Lab announcements:** keep all.
- **Press and policy:** only high-profile events (several outlets, Techmeme, notable HN discussion, or high stakes).
- **Papers:** the top 10 from the last 7 days by rank fusion over traction signals.

The model's role shrinks to press event grouping and the stakes label.

Dropped from the plan:
- the per-story judge (relevant, novelty, relevance level)
- `config/profile.md`
- the fixed section quotas and the wildcard slots

Still open:
- who writes the one-line `gist` in the index. It could be the summary's first sentence, deterministic, or an optional Haiku pass in M4.
- the rule for Hugging Face org uploads (M5)

The milestones are re-sequenced, because the paper and press rules depend on signals:
- **M2: discussion and traction signals.**
  - Hacker News: every story from Algolia via its cursor, attached to stories by URL or id, with comment trees for the attached threads.
  - Hugging Face paper comments, from the page's embedded JSON, only for papers with `numComments > 0`.
  - Engagement snapshots (migration 002) and the 6-hourly discussion poll.
  - A daily re-read of the last 7 days of Daily Papers, so upvote counts stay current.
  - HF paper-API lookups (repo stars, linked models, datasets and Spaces) for the paper pool.
  - Mentions attached by URL or id: newsletters give `covered_by`, Techmeme gives press coverage.
  - Discussion levels, trend, and `bootstrap-bands`.
- **M3: rules triage and the output contract.**
  - The section rules: the release filters, all announcements, paper rank fusion, and press with deterministic grouping and traction only.
  - The merge step, the blocklist, `config/triage.yml`, and the reported ledger.
  - Full-text fetch for index stories.
  - The output files with JSON Schemas, and `run_report.md`.
- **M4: Haiku.**
  - Press event grouping and the stakes label.
  - The attach pass for unmatched discussion, and discussion-only clusters.
  - Caching, degraded mode (press falls back to deterministic grouping with no stakes label), usage reporting, and the Claude Code version bump.
- **M5:** the remaining sources, as before.
- **M6:** `snapshot-fixtures` plus the weekly newsletter recall check, which replaces a hand-labelled `must_include.yml` as the main measure.

## Revision 2: candidates first, one model call (2026-10-22)

CRITERIA.md has been rewritten:
- **Candidates.** Each section deterministically produces a capped candidate list: papers 15, releases 10, announcements 35, press 30, discussion 10, so at most 100 in all.
- **One Haiku call** scores every candidate: a 1–5 score, `relevant`, `why`, `same_as`.
- **A rule** keeps `score ≥ 4` and relevant. The number to keep is still to be tuned.

This replaces "keep all announcements" and the separate press stakes label.

Other changes:
- Releases now means significant model releases only: the HF org watch (25 labs), HF trending models, OpenRouter, DeepSeek GitHub and LMArena. Tooling releases, PyPI, npm and repo trending are disabled, and so is PR Newswire.
- "Relevant" is defined in `config/relevance.md`, in Alex's own words.
- `debrief sections` prints the source list for each section, and config loading refuses an anchor source whose kind has no section.

Effect on the milestones:
- **M2** adds the model-release adapters (`hf_hub` with derivative filtering, `openrouter`, `github_org`, and `leaderboard` for LMArena) alongside HN and the paper signals.
- **M3** builds the candidate stage.
- **M4** builds the one scoring call.

## M2 results (2026-10-22)

`collect` now also runs:
- Hacker News: every story via a time cursor, plus comment trees with HN's ranked order
- Hugging Face per-paper signals and paper comments
- the model-release sources: the HF org watch (25 labs), HF trending, OpenRouter, DeepSeek's GitHub, LMArena
- attachment of discussion and newsletter/Techmeme coverage to stories, with snapshots of engagement and signals
- discussion levels and trend
- `--cadence 6h`, `bootstrap-bands` and `--rebuild`

There are 113 tests.

**Live, on 2026-10-22:**
- **Runs:** `bootstrap-bands` read 14 days of HN, 14,346 stories in 56 requests. The full daily run took 11 min 53 s; the 6-hour poll takes about 10 s.
- **Releases:** DeepSeek-V4.1-Flash-1017 is one story from three sources (org watch, trending, OpenRouter), with five HN threads (959 points), 14 captured comments and Interconnects coverage.
- **Most discussed stories:**
  - DeepSeek's DeepInfer: 2,331 points
  - DeepSeek-V4.1-Flash-1017: 959
  - Mistral's Voxtral 3: 767
  - PHP2Shell: 446, across 38 threads
  - Meta Muse leaking users' secrets: 402
  - OpenAI on Google Cloud: 322
  - Patch the Planet: 262
- **Determinism:** a replay of the day's 7 runs matched the live state exactly, in all seven tables.

Findings that changed the code:

**Discussion levels needed a floor.** Over all HN submissions the 75th percentile is 4 points, because most submissions are never discussed. Counting only threads with at least 10 points (about 138 a day) puts the 75th percentile at 140 and the 95th at 525.

**Entity attachment works only for identifiers, and only outside papers.** Two versions failed first:
- With plain brand words like SpaceX, DeepSeek and GeForce, every thread about a company landed on one unrelated story: 44 SpaceX threads on one opinion piece, and DeepSeek's model threads on its funding article.
- Paper titles name the models they study. Threads about running Qwen3.8-Flash-Next landed on an RL paper about that model.

The rule now needs a word with a digit (PHP2Shell, V4.1-Flash-1017), shared by exactly one non-paper story. It attached 38 PHP2Shell threads to the Ars story. Posts attached by name or title move later if a URL or id points elsewhere.

**Substack newsletters keep their links in `content`**, not in the summary, which is an 18–66 character teaser. Coverage only appeared once the links came from the full issue.

**HN comment trees follow the story's recency, not the thread's.** DeepSeek's 959-point thread was five days old when the model became a story here.

**Two of the three HF paper comments on a typical paper are bots** (librarian-bot, researchstudio-bot), so bots are skipped.

**Trend on tiny numbers is noise.** A thread going 3 → 5 → 5 points read as "falling", so changes under 10 points plus comments now read as flat.

For M3 and M4:
- **The PHP2Shell event's biggest threads still attach to nothing.** "PHP 8.2.35" (1,075 points) and the PHP Foundation's post (794) name no identifier and link nothing we collect. The M4 attach pass has to catch these, so its pre-filter must not be "AI-related": security events like this one matter. A better pre-filter is threads that share a word with a candidate story.
- **Hugging Face comments are the slowest part of the daily run:** 99 pages at HF's 100-per-5-minute page limit, about 6 minutes. Comments only matter for paper candidates, so once M3 ranks papers, they should be fetched for the top ~30 only.
- **Triage must only consider stories from enabled sources.** Replay and rebuild reproduce everything that was collected, including from sources since disabled (PR Newswire, GitHub releases).

## M3 results (2026-10-23)

What M3 adds:
- **Article pages** (`adapters/article_pages.py`, migration 003). After the anchors, `collect` fetches the page of each new announcement and press story, and the model card or README of each new release. It keeps the main text and the in-article links in `story_pages`. Alex chose collector-side fetching over "full text only after selection", because 15 of the 24 lab and press feeds carry no article body (CRITERIA.md, "Full text").
- **`debrief triage`** (`src/debrief/triage/`): pools, hard rules, merges and press events, the ledger, each section's order and cap, and the rules-only keep. Every threshold is in `config/triage.yml`. The paper use bar moved there from `settings.yml`.
- **The output contract**: `stories_index.jsonl`, `stories/<id>.json`, `triage_log.jsonl` and `run_report.md`, each validated against `schemas/` before anything is written.
- **Hugging Face comments** are now read only for the 30 best-ranked papers of the pool, in triage's ranking.
- Paper abstracts keep 2,000 characters.
- There are 132 tests.

**Live, on 2026-10-23:**
- **Collect:** the daily run took 10 min 19 s. The per-paper Hugging Face lookups took 5 min of that (308 papers). Article pages took 3 min 18 s for 125 pages; this first run covered two days of stories, so a normal day is about half that. 123 pages came back fine. The other two were gated model repos, which refuse the README (401) but show the card on the model page, so the model page is now the fallback.
- **Triage** takes 1.5 s:

  | Section | Pool | Dropped by | Candidates | Kept |
  |---|---|---|---|---|
  | papers | 1,301 | use bar 1,185 | 15 | 10 |
  | releases | 23 | ledger 10 | 10 | 10 |
  | announcements | 52 | ledger 11 | 35 | 15 |
  | press_policy | 50 | ledger 13, merge 6 | 30 | 15 |

  That's 90 candidates and 50 kept, and a re-run gave byte-identical files.
- **The ledger** read the 2026-10-22 index (a development run) as the previous day's, so 44 of yesterday's stories with no new discussion stayed out, DeepSeek-V4.1-Flash and PHP2Shell among them.
- **Merges:**
  - two cross-posts (LitPivot and OlmoEarth, each on the Ai2 and Hugging Face blogs)
  - four press events: Microsoft's diagnosis claim (The Verge and TechCrunch), PHP2Shell (Wired twice), Meta and news scraping (The Verge and TechCrunch), and the TechCrunch Founder Summit pair
- **No announcement merged with an artifact.** 30 announcements link artifacts, but none of them are stories we collect: older arXiv papers, sample repos, other orgs' datasets.

Findings that changed the code:
- **A same-site link isn't evidence of one event.** TechCrunch links its own earlier coverage inside the article, so its Claude Duet piece was grouped with its Meta Muse piece. Now one article linking another groups them only across sites.
- **One page linked `http://localhost:8080**`.** `canonical_url` raised on the malformed port and crashed the article-pages build, so the pages were rebuilt from raw. It now returns "" for such URLs.
- **Every paper is tier 1**, through Hugging Face Daily Papers. So the report's tier-1 drop list leaves papers out; otherwise it would list hundreds.
- **HN points count toward paper fusion only from the band floor (10).** A 2-point submission was lifting a paper.
- **The run report merges source status across the day's collect runs.** Re-running one source made it the only source in the table.

For M4:
- The announcements the fallback keeps are mostly routine, because the turn order is the only cut. AWS how-tos and NVIDIA customer stories got in, while "OpenAI and Poland's NASK partner to advance AI safety" was candidate #17 and dropped. The scoring call does the real cut.
- Almost every press event has one outlet, so press order rests on Techmeme and discussion. The model's `same_as` has to do most of the grouping.
- The TechCrunch Founder Summit (an event promo) passed the blocklist. A pattern for it would also catch real policy summits, so it's left to the model.

## Revision 3: the shape of M4 (2026-10-23)

Decided with Alex after reviewing the M3 index. CRITERIA.md ("The model call") has the details.

- **Triage maps the sources; the skill picks.** Haiku drops only what isn't relevant, so nothing downstream spends tokens on it. Every relevant story goes into the index with a score and a `why`, so Opus can decide where to look without building its own map. This replaces "keep `score ≥ 4`".
- **Relevance is a yes/no gate; the score grades what passes it.** 5 reshapes the field and 4 clearly moves the landscape: candidates for the brief's main body. 3 is worth knowing, one line in the brief's list of everything kept.
- **The attach pass and discussion clusters fold into the one call.** The 30 most-discussed unattached HN threads join it title-only. A thread about a candidate merges into it by `same_as`; any other thread is scored like a story. On 2026-10-23 only 4 of 202 threads with at least 10 points had attached. The 3-source cluster rule returns once M5 adds discussion sources.
- **A reference list of recently reported titles** lets a follow-up thread bring its story back as an `update`.
- **`relevance.md` was rewritten from Alex's rulings** on 27 borderline cases. Informed opinion (lab leaders, investors, prominent technologists) is now relevant, as a read on consensus. The 120 labeled items in `tests/fixtures/relevance/2026-10-23.jsonl` are the calibration set.
- **Ordering:** by score, then newest, within each section.
- **Dropped stories** go only to `triage_log.jsonl`.

M4 is done when:
- the call agrees with the calibration labels well enough that Alex accepts the misses
- a live day triages and reports its token use
- a re-run is all cache hits with byte-identical output
- a forced failure produces a degraded index

## M4 results (2026-10-23)

What M4 adds:
- **The scoring call** (`triage/score.py`, `prompts/score_v1.md`): the payload, the call through Claude Code (2.1.309, now pinned in the image), validation, the cache, one retry, and applying the answers (same_as merges, reported stories back as updates, drops).
- **The unattached threads** join the discussion section.
- **`debrief calibrate`**, the labeled set, and the model comparison (CRITERIA.md "Calibration"). It moved the default from Haiku 5.5 to Sonnet 5.5: 119 of 120 twice, no flips, and faster.
- **Degraded mode, usage reporting, and the API-key guard.**
- There are 142 tests. The call is a stub in tests.

**Live, on 2026-10-23, with Sonnet 5.5:**
- **The call:** one call, 42 s, 42,847 input and 7,233 output tokens ($0.23 at list price; quota on the Max plan).
- **Result:** 122 candidates (90, the 30 threads, and 2 reported stories brought back), 38 kept: 7 papers, 6 releases, 7 announcements, 13 press, 5 discussion. The model dropped 80.
- **A re-run** replayed the cache and wrote byte-identical files.
- **A forced failure** (an unknown model) retried once, then fell back to the rules-only keep, marked degraded with the reason.
- **What the model merged:**
  - TechCrunch's "OpenAI makes its Jev competitor widely available" into the Decisions API announcement from the 2026-10-22 index, which came back as an update
  - two PHP2Shell threads (608 and 313 points) into the Ars article from that index, also back as an update
  - inDrive's robotaxi tests into NVIDIA's Kazakhstan post, which does mention them
- **New in the index from the threads:** Kimi K3.1 Pro (4), Sweden's government ban on ChatGPT and Claude (4), NVIDIA OpenShell (4), Google's Finnish data center (3), WorldTUI (3).

Done after review, the same day:
- **Pages and comments for unattached threads.** On daily runs, the collector fetches the linked page and the comment tree of the 40 most-discussed unattached threads. Triage's model reads the page instead of guessing from the title, and the index gets the page as the thread's gist and full text.
- **Comment selection.** Comments under 150 characters without their links are left out before picking. The detail file shows 4 top-ranked plus 2 most-replied per thread, labeled. Before, the most-replied were selected but never shown.

Left for later:
- **M5** (more sources, which also bring back the 3-source topic rule) and **M6** (the weekly newsletter recall check).
- **The skill and the timers.**

## Revision 4: the scope of M5 (2026-10-23)

Decided with Alex. Every dropped source is now `enabled: false` in `sources.yml`, with the reason in its notes.

- **Dropped: every lookup source.** Semantic Scholar, OpenAlex, OpenReview, Epoch AI, `arxiv-oai`, `github-api`. Papers are in the pool for 3 days, and citations take weeks to appear. arXiv's gap backfill doesn't need `arxiv-oai`; `arxiv-rss` has its own `oai_base`.
- **Dropped: the new anchors except one.** The Federal Register, SEC EDGAR, and the OpenAI and Anthropic status pages are out: "anything major that would be covered would be captured by other sources."
- **Kept: HF trending papers**, a second traction list for the paper pool.
- **Dropped: podcasts and Mastodon.**
- **Deferred: Bluesky,** until there's a list of accounts worth following. Its value is trusted voices, and the list is the source.
- **Dropped: alphaXiv.** Its overview duplicates the abstract and Hugging Face's AI summary, and its comments have no documented endpoint.

### M5: the Alignment Forum, LessWrong, and HF trending papers

- **The Alignment Forum and LessWrong** (one `lesswrong` adapter, two sources):
  - **What it reads:** the official GraphQL API, for posts from the last 48 hours with their body, karma and comment count, plus the top comments by karma. LessWrong posts need the AI tag and a karma floor. The Alignment Forum needs only a floor, since everything there is about AI.
  - **Attachment:** posts attach to stories like any discussion, by URL and id, and their karma counts toward discussion levels. A post cross-posted on both counts once.
  - **What attaches to nothing still counts:** most posts are original work that links to none of our stories. So, like the HN threads, the top unattached posts join the scoring call as discussion candidates, each source with its own quota (about 5 each), because karma and HN points aren't on one scale. Their body is their full text, and no page fetch is needed.
- **HF trending papers:** the Daily Papers API sorted by trending, as a second anchor source for the paper pool. It's nearly free, since the adapter already exists.
- **A pruning decision** on the AWS ML and NVIDIA developer blogs, from a few more days of runs (on 2026-10-23 they gave 0 of 15 kept).
- **The 3-source rule stays dormant.** HN plus the Alignment Forum and LessWrong rarely make 3 independent sources for one topic, since a cross-post counts once. It comes back with Bluesky.
- **Done when:**
  - a live day shows Alignment Forum and LessWrong posts attached to stories or scored as candidates
  - calibration still agrees
  - each new piece has fixture tests

### M6: the weekly recall check, and frozen days

- **`debrief recall --week`.** The reference set is every link the 7 newsletters carried that week: Import AI, Interconnects, Ahead of AI, Latent Space, DWATV, Last Week in AI, The Batch. They're already collected, so this needs no new fetching. For each item, it finds where the pipeline stopped, across the week's triage logs:
  - kept in an index
  - dropped by the model, with its `why`
  - dropped at a deterministic stage (window, use bar, cap, ledger)
  - collected but never a candidate
  - never collected (a source gap)

  The report gives the share kept, broken down by newsletter, and lists every miss with its stage and reason. Newsletters never feed a score, so it's an independent measure. It's the honest version of the calibration number.
- **Misses you agree with become labels:** they're added to the calibration set and `relevance.md`'s worked examples, so the labeled set grows past one day.
- **`debrief snapshot-fixtures --date`** freezes a day's raw data permanently. Raw is pruned after 60 days. A change to the prompt, `relevance.md` or a rule can then be replayed against known days before it ships.
- **Needs a week of daily runs,** so the timers have to come first.

## M5 results (2026-10-24)

Built: the `lesswrong` adapter (sources `lesswrong` and `alignment-forum`), `lesswrong_comments` (source `lesswrong-comments`), and `hf_trending_papers`. All three are polled through the GraphQL API or the HF API, with one request per run for posts. 160 tests.

What the live API showed, and what was decided with Alex:
- **The Alignment Forum is a view of LessWrong.** Every AF post is on LessWrong too, flagged `af`, with the same karma from either host; the AF host shows only AF members' comments. About 4 AF posts a week. It stays a source of its own, so it has its own quota, and the LessWrong source leaves `af` posts to it (recorded at fetch time, so a replay decides the same way). Comments always come from LessWrong.
- **The AI tag misses many AI posts** (38 of the week's 157 posts had no tags, most about AI). Alex chose "AI tag or no tags". LessWrong tags aren't a hierarchy, so that would still drop posts tagged only AI Control or Redwood Research (5 that week, among them "Simulated Control Evaluations", 57 karma). So the rule is: left out only when the post has another core tag (Rationality, World Modeling, World Optimization, Practical, Community) and not AI. Over the week it kept 146 of 158; the 12 it left out were all about something else.
- **HF trending papers is a signal only** (Alex): its top 100 were mostly old papers with popular repos (vLLM 2023, Transformers 2019), and the recent ones were already in the pool through Daily Papers. `hf_trending_rank` (0 = off the list, for pool papers) shows in paper signals, the payload and the detail files. It's not in the fusion; it would need inverting first (lower is better).
- **Attachment by a post's first links only.** On the first live run, two roundups ("AI #191", "Against Moloch #48") attached to whatever their 57th and 3rd links named. The two posts about one thing matched their 1st and 2nd. So a post keeps its link-post target (or itself) plus its first 3 body links (`lesswrong.lead_links`), and a numbered roundup keeps none (`roundup_title`).
- **Fetch-time selections now count from the run's last stored fetch** (`FetchContext.stored_until`), not the run's start. Before, the daily prefetch of unattached threads (HN pages and comment trees, LessWrong comments) and the HF paper-comment ranking couldn't see threads and signals stored earlier in the same run, so they waited a run.

Live 2026-10-24 (a Saturday): 53 LessWrong posts and 2 Alignment Forum posts in 72 hours. Two posts attached: "Simulated Control Evaluations" to its paper (which then failed the use bar: 0 artifacts, 0 stars), and "The Hackening" to the "Patch the Planet" announcement. 22 comments on 5 posts. Triage had 87 candidates, including 5 LessWrong posts (no AF post was in the 48-hour window), and kept 8. The model kept "Gradient Routing Generalizes to Unrouted Hack Types" at 3 and dropped the other four LessWrong posts, among them the week's most-discussed post ("Wah wah wah wah do more AI R&D", 221 karma, 40 comments), as argument without new evidence. Alex reviewed these decisions and the edge cases on 2026-10-24 and kept everything as built: the core-tag rule, the quotas and floors, the model's calls on the essays, attachment by lead links, the use bar (discussion doesn't count toward it), and trending as a signal outside the fusion.

Calibration with the revised prompt (it now describes LessWrong posts): 118 and 118 of 120, 38 of 38 relevant kept, no flips. The two disagreements are the ones Alex reviewed on 2026-10-23 (inDrive, Anthropic's life-science call).

Regression: rules-only triage on a frozen copy of the state for 10-22, 10-23 and 10-24 is byte-identical to the code before M5, and so is the scoring call's user message.

The pruning decision on the AWS ML and NVIDIA developer blogs: on the two scored days, aws-ml-blog gave 21 candidates and 0 kept, nvidia-dev-blog 7 and 0. (NVIDIA's main blog: 1 of 7.)
