# Debrief — Source Catalog

The information diet of an advisor briefing an **AI/ML startup founder** — organized by what a founder
needs to know. This is a **living doc**: when you learn of a new source, drop it under the right heading.
If it has a feed it's usually a one-line add to `collector/config.py`; I'll wire anything you flag.

## Status legend
- ✅ **wired** — active in `collector/config.py` now
- 🟢 **ready** — feed verified working (URL given), easy to add
- ➕ **candidate** — likely has a feed; URL still needs confirming
- 🔧 **scrape/watch** — no feed; needs a small scraper / change-detector (verified reachable)
- 💲 **paid** — violates the free-sources rule; listed for awareness only
- 🐦 **manual** — no free programmatic access; check by hand

## How to add one
Most sources are RSS → append `(label, url)` to the matching list in `config.py`
(`LAB_FEEDS` / `NEWSLETTER_FEEDS` / `NEWS_FEEDS` / `STARTUP_FEEDS` / `REDDIT_RSS` / `LOBSTERS_RSS`).
It then flows through dedupe + scoring automatically. Scrape/watch sources need a small fetcher in
`collector/sources/`. Verify a feed first with the pattern in `collector/check_keys.py` or a quick
`feedparser.parse`.

---

## 1 · Research & papers (substance)
- ✅ arXiv — cs.AI, cs.LG, cs.CL, cs.CV, cs.NE, stat.ML
- ✅ Hugging Face Daily Papers (upvotes, linked artifacts)
- ✅ Semantic Scholar (citations, tldr)
- 🔧 Papers with Code — `paperswithcode.com` (trending; reachable)
- ➕ alphaXiv — `alphaxiv.org` (community paper discussion)
- ➕ HF trending models / datasets / spaces — `huggingface.co/api/models` (confirm sort param)

## 2 · Model & lab releases (don't-miss announcements)
- ✅ OpenAI · Anthropic (news + engineering) · Google Gemini · DeepMind · Google Research · Meta AI ·
  Mistral · xAI · **NVIDIA** · Hugging Face blog
- ➕ Most have blogs (confirm feeds): Cohere, AI21, Stability, Together, Groq, Perplexity,
  Microsoft Research, Apple ML, AI2/OLMo, Qwen (Alibaba), DeepSeek, Reka, EleutherAI
- ➕ Ollama new models · Replicate trending (builder-facing model drops)

## 3 · Startup ecosystem — incubators, accelerators, "what to build"  ← your focus
- ✅ YC blog · Launch HN · Show HN · r/ycombinator · r/startups
- 🔧 **YC Requests for Startups** — `ycombinator.com/rfs`  *(the ideas YC wants funded — verified
  reachable; worth a dedicated watcher)*
- 🔧 **YC Launches** — `ycombinator.com/launches`
- 🔧 **AI Grant** (Nat Friedman + Daniel Gross) — `aigrant.com`  *(AI-specific accelerator + RFS — verified reachable)*
- ➕ a16z — `a16z.com` ("Big Ideas" / RFS lists; confirm feed)
- ➕ First Round Review — `review.firstround.com` (founder playbooks; confirm feed)
- ➕ NFX — `nfx.com` (essays + "RFS"; confirm feed)
- ➕ Bessemer "Roadmaps" / State-of reports · Sequoia · Greylock · Lightspeed (firm blogs)
- ➕ Techstars · Antler · Entrepreneur First · South Park Commons (accelerator blogs)
- ➕ Indie Hackers — `indiehackers.com`

## 4 · VC & funding intelligence (where the money flows)
- ✅ Crunchbase News · TechCrunch Fundraising · TechCrunch Startups
- ➕ Strictly VC · Axios Pro Rata · Fortune Term Sheet (newsletters)
- 💲 The Information · PitchBook · CB Insights (paid — awareness only)

## 5 · Analysis & newsletters (the digesters — also the mainstream-axis signal)
- ✅ Import AI · Interconnects · Ahead of AI · Latent Space · Last Week in AI · **Simon Willison**
- ✅ **MIT Tech Review · AI** · **Stratechery** (free posts)
- ➕ The Batch (DeepLearning.AI / Andrew Ng) · The Rundown AI · Ben's Bites · The Neuron ·
  Exponential View (confirm feeds)
- ➕ Practitioner blogs: Chip Huyen · Lilian Weng · Eugene Yan · Hamel Husain · Jason Liu
- 💲 SemiAnalysis (chips/infra; mostly paid)
- 📧 Email-only (TLDR AI · AlphaSignal): web-archive scrape (planned)

## 6 · Practitioner discourse & community
- ✅ Hacker News (keyword + front page + Show HN + Launch HN)
- ✅ Reddit — LocalLLaMA, MachineLearning, artificial, startups, ycombinator
- ✅ Lobsters (ai) · Bluesky (optional, app password)
- ➕ More subreddits: r/OpenAI · r/SaaS · r/Entrepreneur · r/StableDiffusion · r/aivideo
- 🐦 X/Twitter — no free read API; manual only

## 7 · Dev-tool & OSS momentum (what builders adopt)
- ✅ GitHub Trending · GitHub Search (impl counts) · Product Hunt
- ➕ HF trending models/datasets/spaces · Replicate · Ollama · PyPI / npm trending
- ➕ GitHub Releases for key repos (vllm, transformers, langchain, llama.cpp, ollama…) via per-repo `.atom` feeds

## 8 · Compute / infra / chips
- ✅ NVIDIA blog (see §2)
- ➕ The Next Platform · Google Cloud / AWS / Azure AI blogs
- 💲 SemiAnalysis

## 9 · Policy & regulation (lighter touch)
- ➕ Stanford HAI · GovAI · EU AI Act trackers · MIT Tech Review (policy)

---

## Backlog — sources you're learning about
_Jot new sources here; I'll verify + wire them._
-
-
