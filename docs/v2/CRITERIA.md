# What makes it into the index

Decided with Alex on 2026-10-22. Where this file disagrees with BRIEF.md §6.3–6.5 or with PLAN.md's triage stages, this file wins. The exact thresholds and patterns will live in `config/triage.yml` (M3), so tuning them doesn't need a code change.

## Principles

- **Each section has its own rule.** Releases and lab announcements are kept whole. Press is kept when it's high-profile. Papers are a traction ranking.
- **Rules decide, the model only labels.** Haiku 5.5 does two narrow jobs, both for press: grouping articles into events, and a high-stakes label. Everything else is deterministic, so every drop has a stated reason in `triage_log.jsonl`.
- **Traction is the ranking key for papers and press.** This deliberately changes brief principle 3 ("volume is never a ranking key"). Spotting hidden gems is out of scope here: a light model or the collector can't do it reliably. The skill can still notice one among what it's given.
- **Full text is fetched after selection**, for the index stories only (see the end of this file). No rule below needs more than a title and teaser.

## Steps every section shares

1. **Merge duplicates.** Stories that link the same artifact (a blog post and the arXiv paper it announces), and the same announcement cross-posted under different URLs (OlmoEarth on both the HF and Ai2 blogs, same title within 3 days), become one story.
2. **New since the last brief.** Releases, announcements and press must have been first seen after the last published brief, per the reported ledger, `runs/<date>/reported.json`. Until the skill writes that ledger, it's "after the last triage run". Papers use their own 7-day window (below).
3. **Blocklist.** Domains and title patterns in `config/triage.yml`.

## Releases: keep every real release

Sources: GitHub releases for the tracked repos. Hugging Face org uploads, PyPI and npm arrive in M5.

- **Keep:** every stable release.
- **Drop:**
  - drafts
  - prereleases (GitHub's `prerelease` flag, or a tag matching rc, alpha, beta, dev or pre)
  - build and nightly tags matching the patterns in config (`^b\d+$`, `nightly`, `^viable/`, …)
- **Collapse:** a repo marked `collapse: daily` (llama.cpp, which releases on every build) keeps only its newest release of the day, with a count of the rest.
- **Order:** newest first. No model.
- **Open (M5):** Hugging Face org uploads can't be "keep all", since NVIDIA alone uploads dozens of checkpoints. Their rule gets settled when that adapter lands.

## Lab announcements: keep all

Sources: every anchor source of kind `announcement` (the lab and company blogs).

- **Keep:** every new announcement, from every tier. Only the blocklist drops anything.
- **Order:** by organization, then newest first. No model.
- **Expected volume:** about 30 a day. AWS, OpenAI, NVIDIA and Hugging Face write about 75% of them.
- **Revisit** if the volume crowds the skill. The fix would be a "what happened" label that moves tutorials and customer stories to a one-line list.

## Press and policy: high-profile events only

Sources: the press feeds (TechCrunch, Ars Technica, MIT Technology Review, MIT News, The Verge, Wired), PR Newswire, and the policy sources (NIST, UK AISI, Federal Register; SEC EDGAR in M5).

1. **Group articles into events.** First deterministically, where articles link the same artifact or URL. Then Haiku sorts the day's remaining press stories into events in one batched call. It sees their titles and teasers, plus the events from the previous 3 days so a story can join an earlier event. Example: the PHP vulnerability's six articles (TechCrunch, Ars, The Verge, two in Wired) become one event.
2. **Traction per event:**
   - `outlets`: distinct press sources in the event. A wire service (PR Newswire) never counts as an independent outlet.
   - `techmeme`: Techmeme carried it, matched by link or title.
   - `hn`: the discussion level (M2) of Hacker News threads linking any of the event's articles.
3. **Stakes label** (Haiku, per event): `high` or `normal`. High means something a member of the AI community should know even when only one outlet has it:
   - a major security incident or vulnerability in widely used software or models
   - binding regulation, a lawsuit or a government action involving a major AI organization
   - an outage or failure of a major AI service
   - a big claim with stakes: a funding round of $1B or more, the acquisition of a known AI company, or a frontier-capability claim from a major lab
4. **Keep an event when any of these holds:** `outlets ≥ 2`, `techmeme`, `hn ≥ notable`, or `stakes = high`. A wire-only item is kept only on `stakes = high`.
5. **Order:** `outlets` descending, then `hn`, then newest.

## Papers: the top 10 by traction from the last 7 days

- **Pool:** papers first seen in the last 7 days (arXiv and Hugging Face Daily Papers).
- **Signals,** each read at its latest snapshot. Each is a ranking of the pool, counting only papers where the value is above 0:
  1. Hugging Face upvotes. The last 7 days of Daily Papers are re-read every day, because upvotes keep growing.
  2. GitHub stars of the official repo (`githubStars` from the HF paper API).
  3. Adoption: models, datasets and Spaces on Hugging Face that link the paper.
  4. Hacker News points: the best thread linking the paper (M2).
  5. Discussion spread: distinct discussion threads or posts mentioning it (HN, Bluesky, Simon Willison) (M2/M5).
- **Score: rank fusion.** A paper's score is the sum, over the signals, of `1 / (k + rank)`. Tied values share a rank, and k starts at 20.
  - Signals on different scales combine without weights.
  - A missing signal simply adds nothing.
  - No single list, such as an org's coordinated upvotes, can carry a paper on its own. On 2026-10-22, one org held 4 of the top 12 by upvotes.
  - k sets how much topping one list counts against ranking well on several.
- **Select:** the top 10 by score, at most 2 per organization, skipping papers already in a published brief. A paper with no signal scores 0 and is never picked. Ties go to the higher upvote count.
- **Deliberately not in the score:**
  - newsletter coverage, which is the evaluation set below and would otherwise grade itself
  - citations, too slow to move within a week
- No model.

## Discussion-only stories (M2)

A topic discussed in at least 3 independent sources, with no shared artifact. The rule gets settled once M2 produces real data.

## How we check it (M6, weekly)

- **The reference set.** The newsletters (Import AI, Interconnects, Ahead of AI, Latent Space, DWATV, Last Week in AI, The Batch) are a human-curated record of what mattered. Every paper and press event they covered in the week forms the reference set.
- **What gets reported:**
  - paper recall: the share of their papers that made our top 10 on some day
  - event recall: the share of their press events we kept
  - what we missed, and why
- **Tuning:** k, the press thresholds and the stakes definition are tuned against these numbers. Newsletters never enter a score.

## Full text (after selection)

- **For every index story except papers:** fetch its page and extract the main text with trafilatura, capped at about 8,000 characters. Robots.txt is checked and paywalled domains are skipped. It's stored raw and in the detail file the skill reads. That's about 40–60 fetches a day.
- **Papers** keep their abstract. The arXiv cap rises to 2,000 characters so abstracts stop being cut off.

## Index size

The index is no longer a fixed total. Papers are fixed at 10; the other sections follow their rules. A typical day:
- 5–10 releases after collapsing
- about 30 announcements
- 3–8 press events
- 10 papers
- plus discussion-only stories

That's roughly 50–60 in all. The brief's per-section quotas and wildcard slots are dropped. The wildcards were a safety net against the model wrongly dropping tier-1 stories, and nothing tier 1 goes through the model now.
