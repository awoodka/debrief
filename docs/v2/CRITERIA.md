# What makes it into the index

Decided with Alex on 2026-10-22. This supersedes BRIEF.md §6.3–6.5 and PLAN.md's triage stages. Thresholds and patterns will live in `config/triage.yml` (M3); what "relevant" means lives in `config/relevance.md`.

## The pipeline

```
collect ──> candidates (deterministic, per section, capped; at most 100 in total)
        ──> one Haiku call scores every candidate
        ──> a rule keeps the best
        ──> full text for what's kept ──> the index
```

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

The windows count back from the run. "Not yet in a brief" uses the reported ledger, `runs/<date>/reported.json`. Until the skill writes that ledger, it means "not in a previous triage run's index".

### Papers: at most 15

- **Pool:** papers first seen in the last 3 days. In practice that's what Hugging Face listed, since arXiv-only papers almost never show a signal that fast (1 of 40 sampled on 2026-10-22).
- **Use bar.** A paper is eligible only with evidence of use: at least one model, dataset or Space on Hugging Face built on it, or an official repo with at least 100 stars. On 2026-10-22, 65 of 193 papers passed. Upvotes alone are attention, not use.
- **Score: rank fusion.** Each signal ranks the eligible papers: upvotes, repo stars, adoption (models + datasets + Spaces), HN points (M2), and discussion spread (M2). A paper's score is the sum over the signals of `1 / (20 + rank)`.
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
- **Grouping:** articles that link the same artifact or URL become one event, deterministically. The model handles the rest of the grouping (below).
- **Order:**
  1. `outlets`, the distinct press sources in the event (wires never count)
  2. Techmeme carried it
  3. the HN discussion level (M2)
  4. newest
  5. `story_id`
- **Status pages:** only incidents marked major (M5).

### Discussion-only stories: at most 10 (M2)

A topic with at least 3 independent sources and no shared artifact. Ordered by spread, then discussion level, then `story_id`.

The caps add up to 100. A normal day runs about 80–90: about 30 announcements, 30 press, 15 papers, and a few releases.

## The model call (Haiku 5.5, one call per run)

**Input.** Every candidate, in the deterministic order above, delimited as data. Each one carries:
- its id, section, source and organization
- its title
- its teaser or abstract, about 600 characters
- its signals

Plus the rubric from `config/relevance.md`. Fetched text is untrusted, and the prompt says so.

**Output**, schema-enforced, one entry per candidate:
- `score`, 1 to 5:
  - **5, reshapes the field:** a frontier model or major open release with a new capability; a tool that changes how developers build; a finding that changes how models are trained, evaluated or deployed; policy or geopolitics with broad effect.
  - **4, clearly moves the landscape:** a notable model, tool or result in an important area; a major funding round or acquisition; an incident many people feel (the PHP2Shell vulnerability).
  - **3, useful but narrower context:** a real but incremental advance; regional or sector policy; a notable product update.
  - **2, routine:** minor updates, customer and partner stories.
  - **1, not relevant:** tutorials, marketing, events, off-topic.
- `relevant`: yes or no, against `relevance.md`
- `why`: one line, at most 20 words
- `same_as`: the id of another candidate covering the same event, or null. This is how the six PHP articles and OpenAI's "Patch the Planet" post become one story.

**Then the rule.** Keep candidates with `score ≥ 4` and `relevant`, after merging by `same_as`. How many to keep isn't settled yet; it gets tuned with the recall check below once there's real output.

**Degraded mode.** If Haiku is unavailable, keep each section's first few candidates in the deterministic order, and mark the run `degraded`.

## How we check it (M6, weekly)

- **The reference set:** every paper and event the newsletters covered that week (Import AI, Interconnects, Ahead of AI, Latent Space, DWATV, Last Week in AI, The Batch).
- **What gets reported:** what share of that reference set we kept, and what we missed and why.
- **Tuning:** the caps, the use bar, k, the score threshold and the rubric are tuned against those numbers.
- **Newsletters never enter a score or a candidate list.**

## Full text (after the rule)

- **For every kept story except papers:** fetch its page and extract the main text with trafilatura, capped at about 8,000 characters. Robots.txt is checked and paywalled domains are skipped. It's stored raw and in the story's detail file.
- **Papers** keep their abstract, with the cap raised to 2,000 characters.
