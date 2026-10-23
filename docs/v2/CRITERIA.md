# What makes it into the index

Decided with Alex on 2026-10-22, and revised on 2026-10-23 (the model call). This supersedes BRIEF.md §6.3–6.5 and PLAN.md's triage stages. Thresholds and patterns live in `config/triage.yml`; what "relevant" means lives in `config/relevance.md`. The code is `src/debrief/triage/`, built in M3.

## The pipeline

```
collect ──> candidates (deterministic, per section, capped; at most 100 in total)
              + the 30 most-discussed Hacker News threads that attached to nothing
        ──> one Haiku call: same_as, why, relevant, score for each
        ──> code merges same_as groups and drops what isn't relevant
        ──> the index: every relevant story, annotated, ordered by score
```

Triage's job is to map the sources for the skill, not to pick the brief. Haiku drops what is clearly not relevant, so nothing downstream spends a token on it, and annotates the rest with a score and a reason. The skill (Opus) decides what to write about.

**Determinism.** The candidate list is a pure function of three things: the state database, the run's recorded timestamp, and the config.
- Every window is measured from the run's timestamp, not the clock.
- Every signal (upvotes, stars, HN points) is a stored snapshot.
- Every sort has an explicit final tie-break on `story_id`.

The model call is the one step that isn't deterministic, so it's pinned instead. Its result is cached by a hash of the candidate list, the prompt version, `relevance.md` and the model name. Re-running triage on the same data replays the cache, so the same index comes back. Both the candidate list and the model's answers are written to `triage_log.jsonl`.

## Sections are fixed lists of sources

A story's section comes only from its source's `kind`, mapped through `settings.yml` `sections`. Nothing is inferred from content. `config.load()` refuses an enabled anchor source whose kind has no section. `debrief sections` prints the current lists. As of 2026-10-22:

| Section | Sources |
|---|---|
| papers | arxiv-rss, hf-daily-papers, hf-trending-papers |
| releases | hf-org-watch (25 labs), hf-trending-models, openrouter-models, deepseek-github, lmarena |
| announcements | 19 lab and company blogs (OpenAI, DeepMind, Google ×3, Anthropic ×2, Meta, Microsoft Research, Mistral, Cohere, Ai2, Apple, Thinking Machines, NVIDIA ×2, Hugging Face, AWS, Together) |
| press_policy | TechCrunch, Ars Technica, MIT Technology Review, MIT News, The Verge, Wired, NIST, UK AISI, Federal Register, SEC EDGAR, OpenAI status, Anthropic status |
| discussion | none. These stories are clusters of discussion posts (M2) |

Discussion sources (HN, Bluesky, LessWrong, the Alignment Forum, Mastodon, podcasts, Simon Willison) only attach to stories. Reference sources (the newsletters and Techmeme) only record coverage and feed the recall check. Neither kind ever becomes a story itself.

Turned off for releases, because they aren't news: GitHub tooling releases, PyPI, npm, trending Spaces and datasets, and GitHub trending and new repos. PR Newswire is off as well.

## Candidates, per section

The windows count back from the run. "Not yet in a brief" uses the reported ledger, `runs/<date>/reported.json`. Until the skill writes that ledger, it means "not in a previous triage run's index". It applies to every section, so yesterday's top paper doesn't fill a slot again today. A reported story with a new thread since (at or above the HN floor) comes back with status `update`.

### Papers: at most 15

- **Pool:** papers first seen in the last 3 days. In practice that's what Hugging Face listed, since arXiv-only papers almost never show a signal that fast (1 of 40 sampled on 2026-10-22).
- **Use bar.** A paper is eligible only with evidence of use: at least one model, dataset or Space on Hugging Face built on it, or an official repo with at least 100 stars. On 2026-10-22, 65 of 193 papers passed. Upvotes alone are attention, not use.
- **Score: rank fusion.** Each signal ranks the eligible papers: upvotes, repo stars, adoption (models + datasets + Spaces), HN points (M2), and discussion spread (M2). A paper's score is the sum over the signals of `1 / (20 + rank)`. HN points count only from the band floor (10) up, since a 2-point submission isn't traction.
  - Tied values share a rank.
  - A missing signal adds nothing.
  - No single list can carry a paper. One org held 4 of the top 12 by upvotes.
- **Order:** score, then upvotes, then `story_id`. At most 2 per organization. The first 15 go to the model.

### Releases: at most 10

**Significant model releases only:**
- new original repos from the 25 watched labs, created in the last 48 hours
- models first seen in HF trending in the last 48 hours that were created within the last 14 days
- OpenRouter model IDs first seen in the last 48 hours
- new DeepSeek GitHub repos first seen in the last 48 hours
- a model that newly enters LMArena's top 5

**What doesn't count as a release:**
- **Derivatives.** HF tags them `base_model:quantized`, `:finetune`, `:adapter` or `:merge`.
- **Format copies.** A name ending in GGUF, NVFP4, FP8, AWQ, GPTQ or MLX is folded into the original's story.
- **Personal accounts.** Only organization accounts count.

**Order:** tier, then likes, then `story_id`.

### Lab announcements: at most 35

- **Pool:** everything published in the last 48 hours that isn't yet in a brief.
- **Over the cap,** sources take turns: one source at a time, tier 1 first and then in `sources.yml` order, each giving its newest post. So no single vendor can fill the section on a conference day; on 2026-10-21, NVIDIA's GTC Berlin keynote alone produced 14 posts. Final tie-break: `story_id`.

### Press and policy: at most 30

- **Pool:** everything published in the last 48 hours that isn't yet in a brief.
- **Grouping:** articles become one event, deterministically, when they have the same title, when one links the other from another site (an outlet's links to its own earlier coverage are context, not evidence), or when both link the same URL off their own sites (profile, home and index pages don't count). The links come from each article's page. The model handles the rest of the grouping (below). An event also records the stories in other sections its articles link (`about`), and those stories record the event (`press`).
- **Order:**
  1. `outlets`, the distinct press sources in the event (wires never count)
  2. Techmeme carried it
  3. the HN discussion level (M2)
  4. newest
  5. `story_id`
- **Status pages:** only incidents marked major (M5).

### Discussion: the 30 most-discussed unattached threads

Hacker News threads from the last 48 hours, with at least the band floor in points (10), that attached to no story, ordered by points, then id. On 2026-10-23, only 4 of 202 such threads attached. Among the rest were Kimi K3.1 Pro (451 points), Sweden banning ChatGPT and Claude in government offices (309), and follow-ups on PHP2Shell; most of the rest weren't about AI.

Each goes into the model call with its title, link, points and comments, and the start of the page it links to (or its own text, for an Ask or Tell HN): `collect` reads those pages and the threads' comment trees on daily runs, for the top 40. A thread about another candidate is folded into it by `same_as`, as discussion. A thread about nothing we collected is scored like any story, so it can enter the index on its own, with the linked page as its full text and its best comments as discussion.

The rule from the brief, a topic with at least 3 independent sources, comes back once M5 adds more discussion sources. With Hacker News the only one built, no topic can reach 3.

The caps add up to 100, plus the 30 threads. A normal day runs about 80–90 candidates: about 30 announcements, 30 press, 15 papers, and a few releases.

## The model call (Sonnet 5.5, one call per run)

The model is `scoring.model` in `triage.yml`. It was planned as Haiku 5.5; the calibration below moved it to Sonnet 5.5.

**Input**, delimited as data, in the deterministic order above:
- every candidate: its id, section, source and organization; its title; its summary, plus the start of its page when the summary is short, about 1,000 characters in all; its signals
- the 30 threads, title-only
- the titles of stories already in a brief in the last 7 days, as a reference list. A follow-up thread can then point at the story it continues, which comes back with status `update`, rather than standing alone.
- `config/relevance.md` whole: the definition, the worked examples, the score

Fetched text is untrusted, and the prompt says so. The model has no tools.

**Output**, schema-enforced, one entry per candidate, in this order:
1. `same_as`: the id of another candidate (or a reported story) about the same event or thing, or null
2. `why`: one line on what it changes, or why it doesn't matter, at most 25 words
3. `relevant`: yes or no, against `relevance.md`
4. `score`: only when relevant. 5 reshapes the field, 4 clearly moves the landscape, 3 is worth knowing. The brief takes 4 and 5 as candidates for its main body, and gives 3 a line in the list of everything kept.

Writing `why` before deciding makes the decision a reasoned one. Code checks the answers are consistent: a score from 3 to 5 exactly when relevant.

**Then code, not the model:**
- merges each `same_as` group into one story, by the same rules as the deterministic merges
- drops everything not relevant. Dropped stories go only to `triage_log.jsonl`, with their `why`, for auditing; the skill never reads it.
- writes every relevant story to the index with its `score` and `why`, ordered within each section by score, then newest. Volume stays out of the order: points, upvotes and outlets are facts on the line, never sort keys.

**Repeatability.** The answer is cached by a hash of the input, the prompt version, `relevance.md` and the model, so re-running triage on the same data replays it and writes the same index.

**Calibration.** The 120 items of 2026-10-23 (90 candidates and the 30 threads), labeled with Alex, are in `tests/fixtures/relevance/2026-10-23.jsonl`: 38 relevant, 82 not. `debrief calibrate` scores them and compares. Each model ran twice:

| | Agreement | Relevant kept | Not relevant let through | Answers that flipped between runs | Time |
|---|---|---|---|---|---|
| Haiku 5.5 | 118, then 115 of 120 | 38, then 37 of 38 | 2, then 4 | 5 (Spotify's agents, Linux kernel leaks, WeVisOne, TB screening, the PHP embargo thread) | 134 s, 107 s |
| Sonnet 5.5 | 119, then 119 | 38, 38 | 1, 1 (inDrive's robotaxi tests) | 0 | 44 s, 45 s |

Haiku thinks before answering (15k tokens of it), which is why it was slower. So the default is Sonnet. The worked examples in `relevance.md` come from those same items, so these numbers are optimistic; the next days' runs are the real check.

**Degraded mode.** If the call fails (auth, quota, a timeout, or invalid output after one retry), each section keeps its first `fallback_keep` candidates in the deterministic order, and the run is marked `degraded`.

## How we check it (M6, weekly)

- **The reference set:** every paper and event the newsletters covered that week (Import AI, Interconnects, Ahead of AI, Latent Space, DWATV, Last Week in AI, The Batch).
- **What gets reported:** what share of that reference set we kept, and what we missed and why.
- **Tuning:** the caps, the use bar, k, the score threshold and the rubric are tuned against those numbers.
- **Newsletters never enter a score or a candidate list.**

## Full text, and the links grouping needs

Changed on 2026-10-23: pages are fetched by the collector for every new story, not only for kept ones. Alex chose this because 15 of the 24 lab and press feeds carry no article body, so the links that press grouping and the merge step need are only on the page. It's about 60 requests a day.

- **Announcements and press:** `collect` fetches the page of each story published in the last 2 days and extracts the main text with trafilatura, keeping up to 8,000 characters and the links inside it. Robots.txt is checked and paywalled domains are skipped. It's stored raw, in `story_pages`, and in the story's detail file.
- **Releases:** the model card (a Hugging Face repo's README), or a GitHub repo's README. OpenRouter's model page otherwise; nothing for a leaderboard entry.
- **Papers** keep their abstract, with the cap raised to 2,000 characters.
- **Triage stays offline.** It reads only the state database, so a re-run gives the same index.

## Merging announcements with their artifacts (brief §6.1)

An announcement that links exactly one artifact (an arXiv paper, a Hugging Face model, dataset or Space, a GitHub repo) published within 7 days of it is one story with that artifact. The story goes to whichever section comes first in `releases, announcements, papers`, among those whose pool holds a member:
- a lab post about its new model is filed as the release
- a lab post about its paper stays an announcement, and the paper leaves the paper pool

Announcements with the same title (a cross-post, like OlmoEarth on the Ai2 and Hugging Face blogs) are also one story.
