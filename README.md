# Debrief

Debrief is the AI/ML briefing I read every morning. It's published at
**[debrief.alexwoodka.com](https://debrief.alexwoodka.com)** early each morning; the run starts at 3am
Eastern on my home server. Each day a Python collector reads 51 sources: arXiv and Hugging Face papers, the
model releases on the Hugging Face Hub and OpenRouter, 17 lab blogs, the tech press, newsletters, Hacker
News, LessWrong and the Alignment Forum. Triage keeps what matters, and a Claude Code skill writes one article about what
happened and what it means.

This is v2. It replaced the first version, a collector plus a five-seat council of subagents, on
2026-10-25. The first version ran here every day from 2026-09-14, and on my Mac before that; its issues are
still in the archive.

An issue opens with a headline and a short "What happened" list of plain facts. Then comes the article: a
lead and a few themed sections, with a for, against and "my read" where a call is genuinely two-sided,
and quotes from the comment threads where they say it best. Follow-ups on earlier stories get their own
short section. At the bottom, the scan lists every story triage kept, one headline and gist each, so
nothing is lost by being left out of the article. A normal day is about a 10-minute read.

## What it's looking for

Anything that reshapes the field, or helps build an intuition for where AI is heading
([config/relevance.md](config/relevance.md) spells it out with examples). That's a capability, price or
level of access that didn't exist yesterday; a shift in who's ahead; a result that settles or reopens a
debate; a rule that now binds. The discussion matters as much as the news: Debrief exists so I don't have
to read Hacker News, LessWrong and a dozen blogs, so the article says what the comments add, push back on,
or know first-hand.

## How a day works

1. **Collect** (6 to 10 minutes, at 3am, plus discussion polls at 9am, 3pm and 9pm). Deterministic
   Python fetches every source, keeps the raw responses, groups the same story from different outlets, and
   attaches the Hacker News, LessWrong and Hugging Face discussion. Article pages are fetched for their
   full text.
2. **Triage.** Rules pick the day's candidates by section (papers by traction, lab announcements,
   press, model releases and the most-discussed threads; 66 to 122 a day so far). Then one Claude call
   (Sonnet, on my Max plan) merges duplicates, drops what isn't relevant, and scores the rest from 3 to 5
   with a one-line reason. A quiet day keeps 8 or 9 stories, a busy one about 40.
3. **The brief.** A Claude Code skill (`skill/`, on Opus) reads the kept stories in full, with their
   threads, decides the day's through-line and what earns prose, and writes the article as JSON. It can
   fetch at most five pages, only from sites the day's stories link to. A check runs when it finishes:
   every story id has to exist, every quote has to be word for word from a stored comment or text, and no
   link can appear in the prose. Code then adds the links, the attributions and the scan.
4. **The page.** A small deterministic builder turns the brief into a static page in the original
   design, and the site is swapped into place atomically.

The brief takes 1 to 3 minutes. Triage's drops are never shown to the skill, and everything fetched from
the web is treated as data, never as instructions.

## Running it

Everything runs in Docker (the server has no Python venv support), with Claude Code logged in on a Max
plan.

```sh
scripts/dev build                                       # the image: Python 3.13, locked deps, pinned Claude Code
scripts/dev pytest -q                                   # the tests
cp .env.example .env                                    # GITHUB_PAT and DEBRIEF_CONTACT
scripts/dev python -m debrief collect                   # a day's collect
scripts/dev --claude python -m debrief triage           # triage, with the scoring call
scripts/dev --claude python -m debrief brief run        # the brief
scripts/dev python -m debrief site build --out DIR      # the site
```

`DEBRIEF_DATA_DIR` sets the data folder (default `/srv/data/debrief-v2`). Don't set `ANTHROPIC_API_KEY`
anywhere: Claude Code would bill the API per token instead of using the subscription, and the runs refuse
to start if they find one. [docs/README.md](docs/README.md) has every command and how each layer works.

## How it runs on the server

systemd timers run `deploy/run.sh`: the daily run at 3am and the discussion polls. Each step runs in a
throwaway container with a read-only root. The collector gets the source keys and no Claude login. Triage
and the brief get the Claude token and no source keys, and the brief's session has no shell. The page build
has no network. Caddy serves the site through a Cloudflare Tunnel. `deploy/update.sh` deploys a pushed
commit of `main`, after running the tests. [deploy/README.md](deploy/README.md) has the details.

## Layout

```
src/debrief/     the collector (adapters/, stories/, store/), triage/, brief/ and site/
skill/           the brief skill, a Claude Code plugin: SKILL.md and its references
config/          sources.yml (every source), triage.yml, relevance.md, brief.yml, settings.yml
prompts/         the scoring call's instructions
schemas/         the JSON Schemas for everything triage and the brief write
templates/       the page and the archive list
tests/           pytest, on recorded responses and frozen days
deploy/          Dockerfile, locked requirements, run.sh, update.sh, the systemd units
docs/            how it works, the design record, the inclusion criteria, the source probe
```

## Not built yet

- **The weekly recall check.** Every link the seven newsletters I follow carried that week, checked against
  where the pipeline stopped it. It needs a week of daily runs.
- **Bluesky,** until I have a list of accounts worth following.
- The scan's one-line gists are each source's own blurb for now, and some read badly.

## Docs

- [docs/README.md](docs/README.md): how each layer works, the commands, and the data layout.
- [docs/PLAN.md](docs/PLAN.md): the design record, with each milestone's results.
- [docs/CRITERIA.md](docs/CRITERIA.md): the rules for what makes it into triage's index.
- [docs/SOURCES.md](docs/SOURCES.md): the latest probe of every source.
- [docs/BRIEF.md](docs/BRIEF.md): the brief I wrote before building v2. Where it disagrees with the plan,
  the plan wins.
