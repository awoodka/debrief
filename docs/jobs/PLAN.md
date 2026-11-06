<!-- The approved plan (2026-11-04). Decisions are Alex's; later revisions are appended below, as in docs/PLAN.md. -->
# Jobs: a daily job-listing collector next to Debrief

## Context
Alex wants a second project in the Debrief repo. Every morning it shows a ranked list of new job
listings that fit Alex's profile. From that list Alex picks jobs for a personal "to apply" list.
Two things matter most:
- AI is used as little as possible.
- No major outlet or listing is missed.

This plan covers getting the sources in and the scoring. Help with applications comes later.
Alex directs plan changes through this chat, because editing files over SSH is awkward.

## Decisions (Alex, 2026-11-04)
- **Roles:** new grad / entry-level, plus startup and founding roles.
- **Fields:** SWE, ML/AI engineering, research / research engineering, data / product.
- **Locations:** remote (US), DC/MD/VA, NYC, SF Bay Area.
- **Profile:** all of the above live in a JSON profile, edited in the private site, checked on
  save, with every version kept. It holds:
  - preferences;
  - untailored background: facts plus a plain skills list;
  - the watchlist;
  - a short "what I want" text in Alex's words.

  **No resume**: Alex tailors it to each job, so it would skew the scores.
- **Site:** jobs.alexwoodka.com, private behind Cloudflare Access, with a link to today's
  Debrief. Debrief stays public and unchanged. Alex's own list (saved → applied → interviewing →
  closed) lives in the site.
- **Approach: the same style as Debrief.** The pipeline is fixed code, plus one AI step that
  makes sense of the data. The step is a single structured call, like Debrief's triage scoring
  call. There is no skill session, because nothing needs prose (code builds the list). There are
  no agents and no web search anywhere.
- **One program:** jobs is part of Debrief's daily run (`run.sh daily`, the 03:00 ET timer).
  It has no timer, lock or deploy of its own. The only extra piece always running is the private
  site, which does no collecting and no AI.
- **Usage:** only what's needed (see "Usage budget").
- **Cadence:** a morning list only (no daytime polls, no alerts).
- **Paid APIs:** free tiers only.
- **LinkedIn and the other closed sites:** a new Gmail account used only for saved-search alert
  emails.

## Sources (checked live 2026-11-04)
Each source follows Debrief's robots policy: never fetch a disallowed path, use the official route.
| Tier | Sources | Route |
|---|---|---|
| Lists | SimplifyJobs/New-Grad-Positions (19k items, updated about every 30 min), vanshb03/New-Grad-2027 | `dev/.github/scripts/listings.json` |
| Company job boards (ATS) | Greenhouse, Lever, Ashby (with pay), Workable, Recruitee, SmartRecruiters, Workday (cxs POST) | Public JSON APIs, no key |
| Big tech | Amazon search.json, Microsoft pcsx (robots explicitly allows it), NVIDIA (Workday) | JSON |
| Startups | Every Ashby, Lever and Greenhouse board; HN "Who is Hiring" (monthly) and HN job posts (Firebase) | Debrief's HN client |
| Job APIs | Adzuna (free key; personal job search is a permitted use), USAJobs (DC), Himalayas, RemoteOK, We Work Remotely RSS, Jobicy, The Muse, Remotive (once a day) | Free; show attribution where required |
| Alert emails | LinkedIn, Indeed, Glassdoor, Wellfound, Handshake, Dice, Built In | IMAP on the new Gmail (app password) |
| Miss-check only | Google's job results through SerpAPI's free tier (250 a month, about 8 a day) | Used for coverage, never as a source |
| Out | Scraping ycombinator.com/jobs (YC's terms forbid it), Meta, Google Careers | Reached through LinkedIn alerts instead |

**General search.** Two MIT-licensed lists already map companies to their job boards:
kalil0321/ats-scrapers (Greenhouse 6k, Lever 2.4k, Ashby 3.4k, Workday 3.5k) and
Riley-Dorrington/job-board-aggregator. They seed the set of boards we poll. The set grows on its
own: every job-board URL we see anywhere (Simplify, HN, Google results, Adzuna) adds that board.

**Polling.** Each board is fetched with a cheap list call (title, location, dates). Full
descriptions are fetched only for jobs that pass the filters. Workday boards are paged, so all of
Workday is polled only if M0's timing allows; otherwise Workday covers the watchlist plus every
Workday company that turns up in the lists. A watchlist company with no allowed route gets its
own LinkedIn alert.

## Coverage: how "nothing major missed" is checked
A hard guarantee isn't possible for sites that forbid collection. Instead, coverage is measured
every day:
1. Every major outlet has an allowed route (the table above).
2. **Daily miss-check.** Two independent samples:
   - Google's job results: about 8 queries a day, rotating through field × location.
   - Every job in the alert emails.

   Each sample job either matches a collected job (even one filtered out) or counts as a miss.
   Code finds each miss's cause: a board we don't poll (added automatically), a filter that was
   too strict, a broken source, or a site with no allowed route.
3. **Loud source health.** A source that fails, or returns nothing when it usually doesn't, fails
   the run as in Debrief. This catches an email template that changed under its parser.
4. The site's **Coverage** page shows recall against each sample over time, and lists every miss
   with its cause.

## How it's built
- **Code:** a `src/debrief/jobs/` subpackage. Its commands go in the same CLI (`debrief jobs
  collect | score | coverage | today | calibrate | probe-sources | web`). It uses Debrief's
  pieces and leaves them unchanged:
  - `debrief.http.Client`: polite fetching, rate buckets, ETags, robots;
  - `debrief.robots`;
  - the raw-store and replay pattern (`debrief.store.raw`);
  - the scoring-call helpers in `debrief.triage.score` (`run_claude`, `argv_for`,
    `parse_response`, the answer cache).

  New code for jobs:
  - adapters, one per ATS, list and API;
  - the IMAP alert reader and its parser for each sender;
  - the board universe;
  - merging duplicates;
  - filters, scoring, coverage, the site.
- **Data:** `<data>/jobs/` inside Debrief's data dir (`/srv/data/debrief-v2/jobs`), so
  `run.sh`'s existing mount and lock cover it. It holds a SQLite database (WAL, separate from
  Debrief's), the raw store, and the score cache.
  The tables:
  - boards;
  - postings (one per real job);
  - sightings (source × posting × time);
  - profile versions;
  - scores (per posting and profile version);
  - Alex's list state and history;
  - coverage checks.
- **Merging duplicates:** an ATS job id when a URL resolves to a known job board, so the same job
  from Simplify, the company board, Adzuna and Google becomes one posting with several sightings.
  Otherwise company, title and location, normalized and fuzzy-matched.
- **Filters (code):** level, from the title and the "N+ years" requirement checked against
  `max_years`; field, from title patterns in the profile; location and remote; posting age; the
  company blocklist. Background facts drive the requirement filters: degree, graduation date,
  work authorization or sponsorship, clearance (common in DC).
- **Scoring (AI, once a day):** one `claude -p` call, Sonnet 5.5 (the model Debrief's
  calibration chose), with no tools. It covers only postings that are new today and pass the
  filters. Each gets a score from 1 to 5, a one-line reason, and any requirement it clearly
  misses. The input is the profile (what Alex wants, the skills, the facts) plus each posting's
  title, company, location, pay and the qualifications text that code pulled out of it. Answers
  are cached per posting and profile version, so nothing is scored twice. A changed profile
  rescores the last 14 days. If the call fails, ranking falls back to code only.
- **The daily list:** sorted by score, then by freshness. Sections:
  - **New today**;
  - **Watchlist** (every new job at those companies, with looser filters);
  - **Still open**: saved jobs whose posting has closed are flagged.
- **Site:** a small Python web app (Starlette and Jinja, plain form posts, no JS framework).
  - **Pages:** Today, Watchlist, My list, Profile (a JSON editor checked against a schema, with
    version history), Coverage.
  - **Signals:** save and dismiss from Today. Dismissals are kept for the scoring check.
  - **Hosting:** a long-running container on the `edge` network.
  - **Access:** a Cloudflare Access app (Alex sets it up in the dashboard, plus the tunnel
    hostname). Caddy rejects any request without the Access header, and the app also verifies the
    JWT's signature. A cron check takes the app off `edge` if Access is ever off (the chat
    pattern).
- **Runs: one program.**
  - `run.sh daily` (03:00 ET) runs Debrief's steps unchanged: collect, triage, brief, site.
  - Then it runs the jobs steps: `jobs collect` (every source plus the alert emails), `jobs
    score` (filters, then the one call), `jobs coverage` (the miss-check).
  - Debrief goes first, so it's never delayed. A jobs failure is logged and fails the unit, but it
    never stops Debrief's publish. The same `claude_ready` check and lock cover both.
  - `run.sh jobs` reruns just the jobs steps.
  - One image, one `deploy/update.sh` and one test run cover both. Only the web container's
    restart and its dependencies are added.
  - The daily unit's 4h timeout must hold both. M0 times the jobs collect; if it doesn't fit,
    Alex reinstalls the unit with a longer timeout (that needs sudo).

## Usage budget (only what's needed)
- **Claude:**
  - At most ONE call a night, skipped when no job is new and past the filters.
  - It sees only those jobs, in compact form: title, company, location, pay, and a qualifications
    excerpt of up to about 600 characters.
  - The batch is capped (start at 150, ordered by code's own ranking; the rest are ranked by
    code). No tools, no thinking beyond the effort setting, no skill session, no agent.
  - Cached per job and profile version, so a job is never scored twice.
  - A profile change rescores only jobs still on the list from the last 14 days, in that night's
    single call.
  - The log records the call's tokens and list price, as in Debrief.
- **Network:**
  - Boards get a cheap list call. Full descriptions are fetched only for jobs that pass the
    filters, with conditional requests where the API supports them.
  - Workday is polled in full only if it fits.
  - SerpAPI: about 8 searches a day, inside the free 250 a month.
  - IMAP reads only unread alert mail.
- **Nothing runs during the day.** The site serves stored data and records save, dismiss and
  profile edits.

### Profile sketch
```json
{
  "levels": ["new_grad", "entry", "founding"], "max_years": 2,
  "fields": {"ml": ["machine learning engineer", "ai engineer", "applied scientist"], "swe": ["software engineer", "backend"], "research": ["research engineer"], "data": ["data scientist", "product"]},
  "exclude_titles": ["senior", "staff", "principal", "manager", "director", "intern"],
  "locations": {"remote_us": true, "metros": ["dc", "nyc", "sf_bay"]},
  "facts": {"degree": "…", "grad_date": "…", "work_authorization": "…", "clearance": "…"},
  "skills": ["…"],
  "watchlist": [{"company": "…", "board": "greenhouse:…"}],
  "blocklist": [], "max_age_days": 14,
  "wants": "In my own words: what I'm looking for and what I'm not."
}
```

## Milestones (Debrief style: each ends at a checkpoint with Alex)
- **M0: probe.**
  - A live probe for every source: `jobs probe-sources`, written up in `docs/jobs/SOURCES.md`.
  - Time a full poll of the board universe (does all of Workday fit overnight?) and check which
    APIs support conditional requests.
  - **Alex:** creates the Gmail account (2-step verification plus an app password), sets up
    saved-search alerts on each closed site, gets the free Adzuna, USAJobs and SerpAPI keys, and
    gives a starting watchlist if wanted.
- **M1: collect.**
  - Every tier-1 adapter, the board universe and its discovery, the raw store and replay,
    postings with merged duplicates.
  - Check: two runs with no duplicates, an exact replay.
- **M2: filter and score.**
  - The profile schema, the filters, the scoring call, a terminal preview (`jobs today`).
  - Alex labels about 100 postings as a calibration set; `jobs calibrate` scores the model
    against it.
- **M3: alert emails and coverage.**
  - The IMAP reader and its parsers, the SerpAPI miss-check, the cause of each miss, the coverage
    report.
- **M4: site and the daily run.**
  - The private app, and the Access and Caddy setup (Alex applies the edge change).
  - The jobs steps go into `run.sh daily`; deploy.
- **Later:** help with applications, and tuning the scoring from save and dismiss history.

## Verification
- `pytest -q` covers:
  - parsers on saved fixtures for every adapter and email sender;
  - merging duplicates;
  - the filters, including the years-of-experience parser;
  - scoring-answer checks with a cached replay;
  - profile schema checks;
  - the miss-check's cause logic.
- **Live (M1):** two collects with no new duplicates; replay rebuilds the same postings.
- **Live (M2):** calibration agreement on Alex's labeled set; one real day's list reviewed with
  Alex.
- **Live (M3):** a miss-check report with each cause traced by hand for one day.
- **Live (M4):**
  - from outside, jobs.alexwoodka.com without Access returns 403 / an Access login;
  - with Access it returns 200;
  - save, dismiss and profile edits persist across a nightly run;
  - Debrief's site and runs are unchanged (its tests pass, and its next 03:00 run is OK).

---

## M0 results (2026-11-04)

The probe is `debrief jobs probe-sources`, with results in `docs/jobs/SOURCES.md`. Two live runs:
- 20:39Z;
- 20:50Z, rerun after a date-parsing fix, with Jobicy carried over to respect its once-an-hour
  rule.

Each run made about 550 requests and took about 6 minutes.

**Works (22):**
- **Community lists:** Simplify and vanshb03 (JSON), speedyapply (AI and SWE), and three jobright
  lists (markdown).
- **Job boards:** Greenhouse, Lever, Ashby, Workable, Recruitee, Workday.
- **Companies:** Microsoft, Amazon (pending the decision below).
- **Job APIs and HN:** The Muse, Himalayas, RemoteOK, We Work Remotely, Jobicy, HN "Who is
  hiring?" and HN job posts.
- **Board universe:** both company-board lists.

Conditional requests return a real 304 on GitHub, Greenhouse, Lever and Ashby, so an unchanged
list or board costs a few bytes. The Muse and We Work Remotely send an ETag but ignore it.

**Off, each with its reason in `config/jobs/sources.yml`:**
- SmartRecruiters' API: robots.txt allows it only to LinkedInBot.
- Remotive: robots.txt disallows `/api/*`.
- Apple: the undocumented API returned nothing with the site's own filters.
- Google Careers and Meta: kept off from the start.

All of these are reached through the community lists and LinkedIn alerts instead.

**Waiting on Alex's keys:** Adzuna, USAJobs, SerpAPI (account check and one test search), and the
Gmail inbox.

**Amazon: OFF (Alex's decision, 2026-11-04).** robots.txt allows amazon.jobs's search.json, but the site's footer
links to Amazon's Conditions of Use, which forbid robots and data-gathering tools. Simplify has 37
active Amazon new-grad listings, and LinkedIn alerts cover the rest.

**The board universe.** The two lists merge to 9,868 Greenhouse, 4,831 Lever, 4,318 Ashby, 4,752
Workable, 2,747 SmartRecruiters (blocked), 1,164 Recruitee and 14,118 Workday boards (3,895
companies). A 50-board sample per system found:
- **Alive:** Greenhouse 64%, Lever 46%, Ashby 80%, Workable 98%, Recruitee 96%, Workday 57%.
- **Responses:** median 0.4–0.7 s. Lever is 118 KB median because its lists include
  descriptions.
- **Workday:** 77 jobs per board, so 4.5 pages of 20.

As configured, a full poll is about 64,000 requests a night, 8.7 hours even with every system in
parallel. Workday alone is 45,000 requests and 8.7 hours. That doesn't fit, so M1 changes how
boards are polled:
1. One polite client per system, run in parallel, so the night takes as long as the slowest system.
2. A board that returns 404 two nights in a row is dropped and rechecked monthly. 36–54% of the
   listed boards are dead.
3. **Workday:** read only each board's newest page, and page on only while the jobs are unseen
   (M1 first checks that lists come newest-first). robots.txt is cached for a week, and the rate
   limit applies per Workday cluster (wd1, wd5, …) instead of across all of Workday. That's about
   8–9k requests.
4. Workable's interval drops from 1.0 s to 0.5 s.

Estimate: about 1 to 1.75 hours a night, inside the 4-hour daily unit alongside Debrief's
roughly 30 minutes. M1 measures the real figure.

**Volumes:**
- **Simplify:** 3,078 active new-grad listings, 25 posted in the last day, 278 in the last 7 days.
- **The universe:** about 37,000 new jobs a night of every kind, mostly retail, healthcare and
  Europe. About 1,300 pass a rough tech-title filter, before the location and level filters. The
  Workday share counts up to 48 h.

So the 150-job scoring cap may bind. M2 measures the real number after the profile's filters.

**Discovery:**
- 42% of the lists' 6,834 active listings link to a known job-board system.
- Those links add about 380 boards the universe lacks (Workday 194, Ashby 65, Greenhouse 37,
  Workable 20, Lever 12).
- The biggest company-hosted sources the lists point at:
  - TikTok/ByteDance's own sites: 315 listings;
  - Oracle Cloud Recruiting: 103;
  - Apple: 57;
  - JHU APL: 46;
  - Amazon: 37;
  - Tesla: 29;
  - Eightfold (Qualcomm, Microsoft): 35;
  - Rippling: 22;
  - Jobvite: 14.

  Those jobs are already collected through the lists. Adding a system only adds the company's
  roles that aren't labeled new-grad. M1 candidates, each only after its robots.txt and terms
  check: TikTok/ByteDance, Oracle Cloud Recruiting, Eightfold, Rippling, Jobvite.

**Fixed during M0:**
- Greenhouse embed links (`/embed/job_app?for=`) and `myworkdaysite.com` URLs are recognized
  as boards.
- Recruitee's `... UTC` dates now parse. Before the fix, Recruitee counted 0 new jobs.
- `--skip ID` carries a source's last result over, for rate-limited and quota sources.

Tests: 255 (Debrief's 198 + 57 jobs).

---

## Revision 1 (Alex, 2026-11-04): no universe sweep; company boards only for the watchlist

Alex: "For company boards that would not be shown in the community lists, I will generate a list
that I would like to also be able to alter through the page as well. So if a company or startup
catches my interest, I will add it to the list, otherwise I will rely on community resources and
email notifications."

This replaces "General search" and "Polling" above:
- **Nightly sources:**
  - the community lists;
  - the alert emails;
  - the job APIs and HN (kept, Alex 2026-11-04: about 20 requests a night);
  - Alex's **watchlist** of companies.

  No sweep of the 42,000-board universe.
- **Watchlist:**
  - Alex adds and removes companies on the page (M4). Until then, through the CLI or by asking
    Claude.
  - Each company's board is read in full every night, all pages, Workday included, because a
    watchlist is small.
  - Its jobs get the Watchlist section, with looser filters.
- **Finding a company's board, with no agent:**
  - Code looks the name up in the board directory: the two universe lists plus every board the
    community lists link to.
  - If that fails, Alex pastes the careers URL and `boards.board_from_url` reads it.
  - A company with no public board (a custom careers site) gets "no board found: set a LinkedIn
    alert for it".
- **Coverage stays measured.** A miss-check job from a company that is on no list, no alert and
  not on the watchlist is reported with the cause "not watched", and the company is offered as a
  watchlist suggestion. Nothing is added automatically.
- **What this removes from M1:**
  - the parallel per-system polling;
  - the dead-board bookkeeping;
  - Workday's newest-page shortcut;
  - most of the night's time and requests (now minutes).

  The M0 universe numbers stay as the record of why.
- **New in M1:** the board directory (a lookup table, refreshed weekly from the two lists with
  conditional requests), and `debrief jobs watch add|remove|list`.

---

## Revision 2 (Alex, 2026-11-04): the pipeline's shape, and the profile's fields

Alex: "gather everything that fits → filter based on properties in JSON profile → then anything
that is not filtered out and deemed good enough to pass a threshold (to limit tokens wasted) will be
passed into a single skill call that will then create the relevance score (also on profile) and
output the top N (not decided yet) ordered."

This replaces "Filters", "Scoring" and "The daily list" above, and the batch cap in "Usage
budget":
1. **Gather (code):** every source becomes postings, with duplicates merged.
2. **Filter (code, from the profile):**
   - position types, by title patterns;
   - level and `max_years`;
   - location;
   - posting age;
   - work authorization or sponsorship;
   - degree;
   - clearance;
   - the blocklist.
3. **Pre-score and threshold (code, from the profile):**
   - **Points:** title-match strength against the position types; profile skills, coursework and
     interests found in the posting text; a company of interest; entry-level wording; freshness.
   - **Cut-off:** below `ranking.threshold`, a job is logged with its points and not sent.
   - **Cap:** above it, the highest points go first, up to `ranking.max_candidates`.
   - **Companies of interest** skip the threshold (still after the filters).
   - **Posting text:** fetched only for jobs that pass the filters, and only through allowed routes
     (job-board APIs). Otherwise the points come from the title.
4. **The scoring call (AI, one call a night, in the form of Debrief's triage call):**
   - **Sees:** the profile (degree, coursework, skills, position types, projects, interests,
     companies, wants) plus each candidate (title, company, location, pay, posted, qualifications
     excerpt).
   - **Returns per job:** a relevance score (1–10 with a rubric), keep (yes/no), a one-line why,
     and clear mismatches.
   - **It makes the final decision.**
   - The answer is cached per job and profile version.
5. **Output (code):** the kept jobs ordered by the model's score (ties: pre-score, then newest),
   top `ranking.top_n`. Every cut is logged with its stage and reason: filtered, below threshold,
   over the cap, not kept, beyond N.

**Profile v1 fields:**
- **About Alex:** `degree` {level, field, school, graduation}, `coursework`, `skills`,
  `position_types` [{name, titles}], `projects` [{name, summary, skills}] (untailored), `interests`,
  `companies` [{name, board}] (the watchlist), `wants`.
- **`filters`:** locations, max_years, max_age_days, work_authorization, clearance,
  exclude_titles, blocklist.
  - exclude_titles are checked against what is left of a job title after its matched
    position-type title is taken out. So "Member of Technical Staff" (an entry-level title at AI
    labs) isn't dropped by "staff", but "Staff Software Engineer" and "Senior Member of Technical
    Staff" are.
- **`ranking`:** threshold, max_candidates, top_n. The values are placeholders until M2; Alex
  hasn't decided N.

---

## M1 results (2026-11-04): the collector

`debrief jobs collect` runs every source in family order (watchlist, lists, APIs, HN, directory).
It keeps every response in `<data>/jobs/raw/` and builds postings in `<data>/jobs/state/jobs.sqlite`.

The code:
- `jobs/adapters/`: lists, apis, hn, boards_watch.
- `jobs/build.py`, `jobs/collect.py`, `jobs/store.py`, `jobs/items.py`, `jobs/watch.py`.
- Debrief's `Store` gained a `migrations` class attribute, the only change to Debrief's code
  (behavior unchanged).

**First live run (22:10Z), 1.5 minutes, about 80 requests:**

| Source | Listings | Notes |
|---|---|---|
| Watchlist | 1,665 | Anthropic 682, OpenAI 811, Hugging Face 6, Scale AI 166; descriptions included |
| Simplify | 3,068 | active, of 19,036; 17,136 closed before we started, skipped |
| vanshb03 | 796 | active |
| speedyapply AI + SWE | 1,357 | |
| jobright (3 lists) | 1,606 | |
| The Muse | 40 | |
| Himalayas | 300 | |
| RemoteOK | 99 | |
| We Work Remotely | 100 | |
| Jobicy | 50 | second run |
| HN "Who is hiring?" | 163 | |
| HN job posts | 31 | |
| Board directory | 42,183 | boards, from both lists plus every board a source links to; weekly |

**Skipped:**
- Adzuna and USAJobs: keys not set, so nothing was requested.
- The alert inbox and the miss-check: M3.
- Microsoft: not a board system and not on the watchlist; Simplify lists its new-grad roles.

**Checks:**
- **No duplicates:** a second run made 0 new postings for anything already stored. The lists
  answered 304, and the boards and APIs re-read every job and found nothing new.
- **Exact replay:** replaying the day from raw files gave identical rows in all 5 tables (8,553
  postings, 9,274 listings, 11,698 keys, 2,407 texts, 54,246 directory rows).
- **Merging:** 721 listings joined an existing posting. Every posting with 4 or more listings was
  inspected, and each was the same job:
  - the same job across Simplify, vanshb03, speedyapply and jobright;
  - a company reposting the same job under new ids.

  Near-identical pairs left unmerged across sources: 68 of 8,515 (<1%). Two safe rules then
  merged 12 more and left 54, mostly genuinely different roles:
  - "Engineer 1" equals "Engineer I";
  - a trailing country ("– US") is dropped.
- **`watch add` lookup**, read-only test:
  - Stripe: greenhouse:stripe (758 open).
  - Ramp: ashby:ramp (169), not a stale Greenhouse board with 1 job.
  - Palantir: lever:palantir (309); SmartRecruiters refused by robots.txt.
  - "Notion Labs": not found; suggests Notion (ashby:notion).

**Rules decided while building (for Alex to review):**
- Two listings are one posting when they share a job-board job id (Greenhouse, Lever, Ashby,
  Workable, Recruitee, Workday, SmartRecruiters), or, failing that, the same normalized company
  and title with overlapping locations.
- Different Greenhouse, Lever or Ashby ids with the same title stay separate (two teams). Ids from
  systems we can't read (Apple, iCIMS, short links) don't block a merge, so identical reposts
  show once.
- A posting closes when every listing of it is gone:
  - Simplify's `active` flag;
  - missing from a complete markdown list or watchlist board (a 304 says nothing);
  - a complete response that parses to nothing while more than 5 were open closes nothing and
    warns.
- Every request checks robots.txt first. An API key in a URL is replaced by `<redacted>`
  before anything is stored.

Alex approved the merging and closing rules (2026-11-04) and added one for links: when a job
has several listings, its link is the company's own listing of it. A posting's link is chosen
again from all its listings whenever one changes:
1. the company's board, read through its API (the watchlist);
2. a job-board page for the job (Greenhouse, Lever, Ashby, Workday, ...);
3. another page on the company's own site (amazon.jobs, careers.microsoft.com, ...);
4. an aggregator's page or a short link (jobright, go.vansh.nyc, Himalayas, RemoteOK, HN, and
   the alert-email senders), only when nothing better exists.

An open listing beats one that's gone. Within a step the newest listing wins, so a company that
posts the same role under several ids (TikTok, JP Morgan) gets its latest one.

**Fixes from the M1 review (Alex, 2026-11-04):**
- **vanshb03's short links.** Every vanshb03 link is a go.vansh.nyc redirect; the real URLs live
  only with the shortener (its repo's `golinks.py` says so). go.vansh.nyc's robots.txt is
  `Disallow: /` for every crawler, so they are never followed.
- **Instead, an early-career second chance when matching (`build.early_match`).** vanshb03 puts
  its own "New Grad:" / "New Grad 2027:" prefix on titles, so its jobs missed the same job from other
  sources. A listing that matches nothing by job id or exact title now joins the one open posting
  that has:
  - the same company;
  - the same core title (new-grad words, class years and a trailing "I" ignored; word order too);
  - an overlapping place.

  It also needs all of these:
  - both sides are early-career (the title says so, or a new-grad list lists it);
  - another source lists the posting;
  - its job id doesn't conflict;
  - exactly one posting qualifies. A company with several similar new-grad roles in one place is
    left alone.
- **Application-form links.** Ashby `/application[?embed=true]`, Lever `/apply` and Workable `/apply`
  links become the job's page. Amazon's `/apply` stays (it can't be checked, and it's Amazon's own
  site); JazzHR's `/apply/...` URLs already are the job page.

Result after a rebuild:
- **Merges:** 8,553 postings became 8,303. 237 vanshb03 and jobright listings joined another
  source's posting, for example OpenAI's Applied Emerging Talent role. A random sample of 15, and
  both that joined a watchlist posting, were checked by hand: all were the same job.
- **vanshb03 only:** 480 jobs are left, down from 704. The rest are companies no other source
  lists, different titles, or ambiguous.
- **Form links:** 491 open postings linked to an application form, now 0.
- **Links of open postings:**
  - the watchlist board 1,665;
  - a job-board page 2,760;
  - the company's own site 1,260;
  - an aggregator 2,614 (jobs only jobright, vanshb03 or a job API lists);
  - no link 4.

Tests: 299.

**Commands:**
- `debrief jobs collect [--source ID]`, `--replay DATE`, `--rebuild`;
- `debrief jobs watch add NAME [--url URL] | remove NAME | list`;
- `debrief jobs posting QUERY`.

Tests: 293.

---

## Revision 3 (Alex, 2026-11-04): M2's decisions

Alex, answering the M2 questions:
1. **Night one:** the whole backlog (about 400 jobs from the last 14 days) is scored once.
2. **Clearance:** jobs that need an *active* clearance are dropped; "able to obtain" stays.
3. **Caps:** "keep the stuff in the morning capped to 20 but I am less concerned about before the
   model (max 50)". So `ranking.top_n` = 20 and `ranking.max_candidates` = 50. Night one's backlog
   is the one exception.
4. **Keep:** "anything that I can apply to (that is full time and above 25/hr) should be kept".

This changes Revision 2's step 4: **keep** is no longer a matter of taste. A job is kept when all
of these hold:
- Alex could apply: level, years, degree, location, work authorization and clearance all fit;
- it's full-time;
- it pays more than $25 an hour (about $52,000 a year), or its pay isn't stated.

The 1–10 **score** is relevance to Alex's wants, skills and projects. It orders the kept jobs, and
the top 20 make the morning list. The rest are kept and stay visible (`jobs today --all`, later
the site).

## M2 design

The nightly path is: collect → descriptions → filters → points and cutoff → the scoring call →
the list. Every job cut on the way is logged with its stage and reason.

- **Filters (code, `jobs/filters.py`).** The profile drives every check:
  - position type, by title;
  - excluded title words, checked on what's left of the title after the matched type is removed;
  - place: the profile's areas, or remote in the US;
  - posting age: `max_age_days`;
  - PhD only;
  - the blocklist;
  - **full-time:** a source's employment type, or title words such as part-time, contract or
    temporary;
  - **pay:** dropped only when clearly below `min_hourly_pay`. Sources mislabel periods, so a
    "yearly" figure under $15,000 counts as unclear and stays;
  - **active clearance required:** from the title, and the description when we have one;
  - **more years than `max_years`:** "N+ years of experience", only when we have the description.
- **Descriptions (a collect step, `describe`).** Some open jobs pass every check that doesn't need a
  description but have no description yet. Their text is fetched through the job board's public
  API:
  - Greenhouse and Lever: a per-job request;
  - Workday: the job's cxs page;
  - Ashby, Workable and Recruitee: the board's list.

  Every request is robots-checked and saved raw, so replay rebuilds it. Company sites and
  aggregators are never fetched. About 110 jobs on night one, then about 10–20 a night.
- **Points (code, `jobs/prescore.py`), 0–100.** They're built from:
  - title fit;
  - early-career evidence (the title, or a new-grad list);
  - the profile's skills, coursework and interests found in the description (a job without one
    gets neutral points, not zero);
  - freshness.

  Below `ranking.threshold` a job isn't sent. Watchlist companies skip the cutoff and go first. At
  most `max_candidates` are sent; the rest are logged as "over the cap".
- **The scoring call (`jobs/score.py`, `prompts/jobs_score_v1.md`).** It's Debrief's triage call
  pattern: Sonnet 5.5 through `claude -p`, no tools, an empty directory, retried once, and code
  ranks the list if it fails. It sees the profile and each job: title, company, places, pay,
  employment type, posted date, and about 600 characters of qualifications. For each job it
  returns:
  - `score` 1–10, on a written rubric;
  - `keep`, by the rule above;
  - `why`, one line;
  - `mismatches`.

  Answers are stored per job and profile version (a `scores` table), so nothing is scored twice.
  Only jobs first seen since the last scoring run are candidates, and a profile change applies
  from the next night. A large batch (night one) is split into calls of at most 150.
- **The list.** `jobs today` shows the kept jobs that are new since the last list, ordered by score
  (ties: points, then newest), top 20, plus a Watchlist section. Each run writes
  `runs/<date>/score-<run>.json` and `.md` with every cut.
- **Calibration.** Alex labels a sample (yes or no under the keep rule) in chat. `jobs calibrate`
  compares the model's keep with the labels.
- **Profile v3:** `top_n` 20, `max_candidates` 50, and two new filters: `full_time_only` and
  `min_hourly_pay` (25).

## M2 results (2026-11-04)

Built: `jobs/filters.py`, `jobs/prescore.py`, `jobs/score.py`, `jobs/rank.py`, `jobs/calibrate.py`,
`jobs/adapters/describe.py`, `prompts/jobs_score_v1.md`, migration `002_m2.sql` (`scores`,
`daily_list`). Commands: `jobs score [--no-cap] [--dry-run]`, `jobs today [--all]`, `jobs calibrate`.
Profile v3 saved: top_n 20, max_candidates 50, full_time_only, min_hourly_pay 25. Debrief's
`triage.score.argv_for` gained an optional `schema` argument (its behavior is unchanged).

**Descriptions (first live run):** 70 requests, 103 descriptions (Ashby board lists cover several
jobs per request). Four Workday job pages answered 403 with either spelling of the site name.
Those count as normal answers (`tolerate`) and aren't asked again.

**Filters on the live data (8,211 open postings):**

| Stage | Cut |
|---|---|
| title | 5,404 |
| place | 1,443 |
| age | 995 |
| degree | 22 |
| clearance | 16 |
| years | 7 |
| employment | 5 |
| **Passed** | **319** |

Every cut made from description text was checked by hand. That check led to these fixes:
- an upper bound ("up to 5 years", "between 0 and 3 years") isn't a minimum;
- a preference ("Preferred qualifications: 5+ years", "Active Public Trust … preferred") isn't a
  requirement;
- a "Post-Start" polygraph is obtainable;
- "Staff+" is the word "staff";
- a remote job named by a US city ("Remote - Cleveland, OH", or a source's remote flag) is remote
  US.

**Night one (`--no-cap`):** 343 jobs in three calls of at most 150. That's 66k tokens in and 34k out,
about 3.5 minutes, and $0.59 at list price (on the Max plan). 306 kept.

Review of the answers led to more fixes:
- titles that ask for a Master's are now cut in code (15 of the model's 37 "not kept");
- the model now sees a source's remote flag. 14 answers were re-asked, and 9 became kept;
- "(2027 Start)" is ignored when matching titles (92 more listings merged, all checked: speedyapply's
  TikTok titles);
- the list shows repeats of the same job (same company and core title) once, with the other
  links.

**After the fixes:** 319 passed the filters, 314 were sent, 302 kept, 272 once repeats are
collapsed. The top 20 are new-grad roles at AI-agent, inference and data startups, plus Databricks,
Palantir and ByteDance AI infrastructure.

**A normal night:** 50 jobs or fewer in one call, about 10k tokens in and 5k out, about $0.10 at
list price.

Answers from before the prompt fix still list "only title known" as a mismatch. New answers don't.

Tests: 361.

## Revision 4 (Alex, 2026-11-04): new-grad roles only; pay is not a scoring factor

Alex: "I would like it to require New grad, something with more than 2 years of experience I cannot
compete for. So like these founding engineer roles would be really hard for me to compete for I
think. Unless you think something else. I would not like the pay to be included in the scoring
factor."

- **New grad only (code, profile `filters.new_grad_only`, profile v4).** A job needs a sign it's
  for new grads, or the `level` filter cuts it before the model:
  - the title: new grad, entry, junior, associate, "I", early career, a class year;
  - a new-grad list carries it;
  - the source's own level field ("Entry Level");
  - the description: "new grads", "recent graduates", "0–2 years", "entry-level", "new graduate
    level".

  `describe` skips this check when choosing what to fetch, since the description may be what shows
  it. On the live data it cut 45 jobs, all for experienced people: OpenAI and Anthropic mid-level
  roles, HN startup posts, founding roles that don't mention new grads.
- **Founding roles.** Claude agreed they're long shots: most expect engineers who have shipped
  products. One is kept only when the listing itself welcomes new graduates (the title, or the
  qualifications); being on a new-grad list isn't enough for these. The keep rule also refuses
  more than 2 years required, even inside a range ("2–4 years").
- **The score** weighs fit with what Alex wants *and* how realistically a new graduate could get the
  job, so a strong-fit long shot scores 5–6.
- **Pay** is never shown to the model, so it can't move the score or the reason. The $25/hr floor
  stays a code filter, and the list still shows pay for information.

**Revision 4 results (2026-11-04):** with profile v4, 278 passed the filters (the `level` check cut
45), and the re-score kept 259. Two prompt fixes came out of the review:
- years are judged by the *minimum* a listing requires ("0–3", "1–3" are fine and only lower the
  score);
- the model now sees each area's city list (it had rejected Jersey City as "outside New York").

Money amounts in titles and descriptions now reach the model as "[pay]" (HN titles carry pay). 21
answers were re-asked; final: 267 kept, and the top 20 are all new-grad roles. The re-score cost
about $0.55 at list price, plus $0.05 for the re-asks. One question is open: a minimum of exactly 2
years ("2–3 years"). The model refused some of these, and the profile's `max_years` of 2 allows them.

## The daily run (Alex, 2026-11-04: "lets just get it working"; calibration set aside for now)
- `deploy/run.sh daily` runs the jobs steps after Debrief's (collect, triage, brief, site), so a
  jobs failure can't hold up the brief: `jobs collect`, `jobs score`, then `jobs today` written to
  `$DATA/jobs/today.txt`. A jobs failure marks the run failed (exit 1) without stopping anything.
  `run.sh jobs` reruns just these steps.
- Secrets split as for Debrief: collect gets `.env` (source keys, contact), score gets the Claude
  token, and `today` runs with no network.
- Tried by hand 2026-11-04 with the run image and sandbox: collect 68 s (14 sources ok), score 8 s
  (6 new jobs sent, list price $0.03), list of 20 written.
- Profile limits unchanged: `max_years` stays 2 ("more than 2 years I cannot compete for").
  Watchlist jobs keep their own section.
- Calibration labels (M2) and the site (M4) come later.

## The board finder (Alex, 2026-11-04: "build that out")
- The gap: 184 of 284 scored jobs had no description and no job-board id (jobright blocks ClaudeBot
  from /jobs/; go.vansh.nyc disallows everything), so the model kept nearly all of them unseen.
- `board-finder` (family detail, runs just before describe) looks up the company in the board
  directory only (guessing board names found 2 of 103 jobs), lists that board (Workday: searched by
  title), and matches by the merge rule: same core title, overlapping place, exactly one job. Ties:
  case-variant Workday sites count once; the new-grad twin of a role wins. Never SmartRecruiters.
- A finder listing never keeps a posting open by itself. Also: "SF"/"NYC" now overlap their city
  names, "careers" is an ignored core word, and jobright titles with [brackets] or a pipe in the
  company no longer come through as markdown links. No-description jobs are marked in the list.
- Tried on a copy: of 196 jobs scored without a description, 11 gained one and 24 were revealed as
  old reposts (cut by the 14-day age filter). Top 20 with descriptions: 7 -> 9.
- Going live needs one `jobs collect --rebuild` on live data so the new merges apply.
- On the copy, 6 of the 11-04 top 20 (Whatnot, Ambral, Human Interest, Strada, SuperApp, Hyphenate)
  dropped out: each merged with its July–September Simplify twin and the age filter cut it. (An
  earlier version of this note had them as still in the list; that was a misreading.)
- Open then: Raydar twice in the top 20; SambaNova scored on its title alone. Both fixed below.

## Two list fixes (Alex, 2026-11-05)
- **Raydar is blocklisted** (profile v5, `filters.blocklist`). It's a recruiting agency: its
  listings say "Raydar is recruiting for this role on behalf of our client" and don't name the
  company. It had 38 open roles, 31 from Simplify's new-grad list, mostly scored 7–9.
- **At most 2 jobs per company in the list** (profile v5, `ranking.per_company: 2`; optional, no
  limit when missing). A company's groups past the first 2 are cut as "company limit" wherever they
  rank, so they never take a slot; its last entry shows "also kept at <company>: N more", and
  `jobs today --all` lists them. The watchlist section is unchanged. (SambaNova alone has 8
  new-grad roles open.)
- **Greenhouse boards for "?gh_jid=" jobs** (describe): a company page's Greenhouse id names the
  job but not its board, so describe couldn't ask for the description. Now it tries up to 4 board
  names (`boards.greenhouse_guesses`: the name's words joined, without a generic last word,
  "technology" as "tech", the page's domain name) against `boards-api.greenhouse.io/v1/boards/<name>/
  jobs/<id>`. An answer proves the board, because Greenhouse job ids are global; a 404 is never
  asked again. A found board is reused that night and goes in the board directory. Checked live:
  SambaNova Systems is `sambanovasystems` and Blue River Technology is `bluerivertech`; Precisely
  isn't found. `boards.greenhouse.io/embed/` stays off (robots.txt). On 11-05 four jobs that pass
  the filters were in this state.

## Revision 5 (Alex, 2026-11-05): keep up with new postings; one catch-up; no repeats
The funnel audit (preview copy, profile v5, whole backlog): 8,531 open → title −5,722 → place
−1,472 → age −999 → level/degree/clearance/blocklist/full-time −118 → 220 → cap −170 → 50 asked →
46 kept → 20 shown. The hard filters and the model's drops checked out. The losses: 625 open jobs
passed every filter but the 14-day age limit (504 posted July–October, among them Scale AI's
"Software Engineer - New Grad", on the watchlist), and none was ever scored; kept jobs below #20
were never shown again; title wording missed ~29 jobs (81 with no age limit) and the town lists
~18 (42), plus two place-reading bugs.

Alex's vision: new postings first ("a greater chance they are still looking", and applying early);
catch the old listings once; eventually each day has no repeats and the system just keeps up.
Alex's answers:
- A kept job that isn't highlighted stays an option for later days ("not mandatory"); new
  postings are still prioritized.
- Fit orders the list; newness adds a bonus: `ranking.fresh_bonus` {"3": 2, "7": 1}.
- The catch-up is one separate list, gone through once; it reaches back 30 days, plus watchlist
  companies at any age.
- A repost under a new job id counts as new: the job id alone says two listings are the same job.
  (Already true: no posting has more than one job id, and a listing with another id never merges.)
- The daily age limit is 30 days. All four audit fixes go in (profile v6): title variants
  (Software Development Engineer, "Engineer, Software", Software Engineering, Full Stack / Web /
  Application Developer, Frontend Engineer; "manager" excluded), a DevOps / SRE type, 29 nearby
  towns, and "Military Spouse Jobs" blocklisted (it reposts other employers' jobs).

Built:
- **The pool** (`rank.py`, migration 003 `highlighted`): each night looks at every open posting
  not yet highlighted, not only the new ones. Filters run on all of it; only jobs with no answer
  for the profile are asked, at most `max_candidates` answers a night, watchlist first, then new
  tonight, then carried over (one over the cap waits for another night). The list is ordered by
  score + `fresh_bonus` (ties: score, points, newest). Every job shown, and each copy shown under
  it, goes in `highlighted` and is never listed again. `--rebuild` carries the table.
- **The catch-up** (`debrief jobs catchup`): the same pass with no cap, watchlist companies exempt
  from the age limit (`filters.check(skip=("age",))`, which now honors "age"), and every kept job
  in one "catchup" section with no top N and no company limit; written to `catchup.txt`;
  `jobs today --catchup` prints it. It doesn't move the cursor. `save_list` now replaces only its
  own sections, so the catch-up and a morning list on the same date keep each other's rows.
- **Places**: every part of a place may be its city ("Arlington County, Arlington, VA"); "greater"
  and a trailing "metropolitan area" are dropped ("New York City Metropolitan Area"); a place
  ending in a foreign country never matches a US town ("Vienna, Austria").
- **The list** shows each job's posted date, and the header says how many are new tonight and how
  many were carried over.

Tried on a copy with profile v6 and fake answers (no model cost): the catch-up's pool passed 390
jobs (350 entries once copies fold; Scale AI's Sep 4 role in, Raydar and Military Spouse Jobs out),
about $0.65 to score. Then a real collect brought 650 new postings, 29 passing the filters; the
next night asked those 29, showed 20 (all new) and carried 7, with no repeat of the catch-up.

## Revision 6 (Alex, 2026-11-05): older roles once; coverage (M3)
Two gaps from Alex's review of the live system: 579 open roles that fit every filter but the
30-day limit had never been shown (445 posted July 1–Oct 6), and the closed sites (LinkedIn,
Indeed, Handshake, Wellfound, Glassdoor, Dice, Built In) plus Adzuna, USAJobs and the miss-check
were still unbuilt or without keys. Alex's answers:
- Older roles: "Everything from October 1st and on, that's it." The first catch-up reached Oct 6,
  so this is Oct 1–5; July–September stays unshown by choice.
- Alerts on all seven sites; Handshake's go to the school address and are forwarded.
- USAJobs: include (DC, remote, and the Recent Graduates hiring path).
- A site's own listing id (LinkedIn, Indeed) is not the employer's job id: copies merge by
  company, title and place; employer job ids stay the rule for "the exact same job".

### Part 1 (built)
- **Gone on its board.** describe's answers now also say whether a job is still open (adapter
  hook `gone()`): a per-job request (Greenhouse, Lever, Workday) answering 404 or 410, or a
  complete board list (Ashby, Workable, Recruitee: one response, ok and not empty) without the job.
  A request made while guessing a Greenhouse board name never counts. `build.apply` keeps the mark
  in the posting's data (`gone`: at, status, url) and clears it when the board shows the job
  again; the filters' new stage "gone" (right after the title) cuts it, since the board outranks a
  list that still says active. It works every night, not only for older roles.
- **`jobs collect --since D`**: for that run only, the board finder and describe target jobs
  posted back to D (the profile is widened for the detail steps only).
- **Named catch-ups**: `jobs catchup --since D --name older` lists every kept job posted on or
  after D (watchlist companies at any age) in its own section, `older.txt` and
  `jobs today --catchup older`; catch-ups skip the points threshold as well as the cap. A
  different name keeps a second catch-up on the same date from replacing the first one's rows.
- `scripts/set-env NAME` now saves the key in both the dev `.env` and the server's
  `/srv/apps/debrief-v2/.env`.
- Tried on a copy of the live data: the detail steps back to Oct 1 made 37 requests, gave 19
  descriptions and found 3 roles gone on their boards (two CoStar roles on Workday, one Twilio
  role on Greenhouse); the older catch-up would list 29 roles from 30 scored.

Part 1 ran on live data on 2026-11-05: the same 3 roles marked gone, and `older.txt` lists 29
roles (30 scored in one call, $0.08 list price), led by Fireworks AI's Member of Technical Staff New
Grad (10) and Brain Co.'s AI Platform Engineer New Grad (9). Seen there: a Blackstone London role
also carries "Berkeley, CA" (from speedyapply's row); fixed below (F1).

### Part 2 (built; waiting on Alex's accounts and keys)
- **The alert inbox** (`adapters/alerts.py`, source `alert-inbox`): IMAP to the jobs-only Gmail,
  INBOX opened read-only, messages fetched with BODY.PEEK[] (nothing marked read or moved), the
  last 4 days, each message once (its UID URL is in the fetch history), at most 300 a night, kept
  raw with no username in its URL; the sender's site is recognized from the From domain (a
  forwarded Handshake alert keeps it). Parsers per site come from real sample emails; until a site
  has one, its emails are kept and counted, not parsed. Items never carry a site's listing id as a
  job key, so a LinkedIn copy merges into the job the lists have (tested). The source counts as
  new-grad (`options.early`), since every alert uses the site's Entry-level filter.
- **The Google sample** (`adapters/serpapi.py`, source `serpapi-google-jobs`): reads the account
  first (free), spends at most 8 searches a night and never more than an even share of the month's
  remainder, rotating through role types x New York, Washington DC, San Francisco and remote.
  Never a source of jobs; the key is redacted from the raw records.
- **`jobs coverage`** (`coverage.py`, migration 004 `coverage_samples`): samples from the raw
  store; eligibility by the profile's filters (ineligible ones keep their stage and stay out of
  the recall); matching by job id, then same company + core title + place, then "near" (a similar
  company name and 60% of the core title words: found, but listed as a possible dedupe gap); an
  unmatched sample waits for three runs, then is a miss with its cause (not watched, company site
  only, closed site only, broken source) and a suggestion (`jobs watch add ... --url ...`, an alert
  search). The alert sample counts the alert jobs no other source had. Report in
  `runs/<date>/coverage-*.md`; its summary is the morning list's second line. `run.sh` runs it
  after `jobs score` (no network, no Claude; a failure only warns).
- **Adzuna**: the role in the title (`title_only`) and any of "graduate grad junior entry
  associate" (`what_or`); "remote" for the remote search. **USAJobs**: the role searches plus the
  Recent Graduates hiring path (`HiringPath=graduates`) for DC and remote. Both start with their
  keys; their queries get tuned on the first real responses.
- Waiting on Alex: the Gmail account and app password, the alerts on the seven sites, and the
  Adzuna, USAJobs and SerpAPI keys (`scripts/set-env`, which writes both env files).

## Fixes before Part 2 (Alex, 2026-11-05: "make the fixes … ensure … the proposed fixes do not break the system or alter the end product behavior")
- **F1, the board decides the country.** speedyapply listed Blackstone's London analyst role as
  "Berkeley, CA" (Simplify and the Workday board say London), and places are a union, so it passed.
  describe now keeps the board's country where it names one (Workday `country`, Lever `country`,
  Ashby `address.postalAddress.addressCountry`) as `board_country` / `board_more_places`; the place
  stage cuts a job its own board puts outside the US with no other place ("its own job board places
  it in United Kingdom"). Of the 112 described jobs with a board answer, it changes only Blackstone.
- **F2, copies find their job when nothing else matched.** A posting whose company/title key an
  earlier posting had taken (a same-title role elsewhere, or a repost under a new job id) was
  invisible to the new-grad merge rule; it now gets an `fa:` alias key. `early_match` tries, only
  when its normal lookup finds no candidate at all: those hidden postings, then company names
  that differ only by a generic last word ("Palantir Technologies"). The fallbacks refuse a listing
  its own source has twice for the same company, core title and city (jobright's two Amazon
  "Software Development Engineer, Newark" are two jobs) and a posting that already has a listing
  from the same source. Every merge made before is made the same way.
- **F2b, rebuilds keep what was shown.** `collect.remap_carried`: after a rebuild, rows of the
  carried tables that name a posting the rebuild no longer has move to the posting their listings
  now belong to; a merged posting counts as shown if any copy was; a score moves only where the
  posting has none.
- **F3, HN titles print their role** (`rank.display_title`): display only.
- **F4, a Workday 422 is a gone board** (`finder.DEAD`): Salesforce moved its sites from wd1 to wd12;
  the finder now rests them and reports ok.
- **Not changed (Alex):** the catch-up list's company crowding stays as is.
- **Verified** on two frozen copies of the live data, rebuilt with the pushed code (ad0eceb) and
  with the fixes: 0 merges lost; 30 postings now hold copies that were apart (Palantir's Query
  Engines role is one posting instead of three; the rest TikTok, SpaceX, Citadel, Voleon, PayPal,
  L3Harris…), none holding two employer job ids; filter results changed only for Blackstone (now
  cut at place) and for 3 merged copies that inherited their job's earlier date or place (none on
  any list); every listing shown before is still shown (no repeats); tomorrow's dry-run list is
  identical. A requirements audit passed 13 of 13 (profile v6 values, new-grad only, the bonus,
  2 per company, the blocklist, one call a night, pay never sent to the model, the job-id rule, the
  catch-up lists, no repeats). 420 tests pass.

## Setup and two decisions (Alex, 2026-11-05 evening)
- **The jobs Gmail works**: login checked from the saved app password; the reader ran once on
  live data and kept the inbox's messages without marking any read. The first saved password held
  two up-arrow key codes typed at the hidden prompt; `scripts/set-env` now drops terminal key codes
  and other control characters before saving.
- **Alert sites for now: LinkedIn, Indeed, Handshake and Wellfound.** Glassdoor, Dice and Built In
  wait until the coverage check shows misses only they would catch. LinkedIn: one boolean search
  per area, Entry level, daily. Indeed rejects OR queries, so one plain search per role and city
  ("entry level software engineer" …). Handshake offers no search alerts here, so its
  recommended-jobs emails come instead, with the jobs Gmail as Handshake's primary address.
  Wellfound signs in with the jobs Google account. LinkedIn's alerts are forwarded from
  alexwoodka@startupshell.org by a Gmail filter.
- **Profile v7: Boston and Austin added** ("Boston area": Boston, Cambridge, Somerville, Waltham,
  Lexington, Burlington…; "Austin area": Austin, Round Rock, Cedar Park…), since Alex set alerts
  there. 29 unshown jobs from the last 30 days pass with them (15 Boston, 14 Austin); they enter
  the daily pool and are asked within the nightly cap of 50, new postings first. The Google sample
  rotates through Boston and Austin too.

## Revision 7 (Alex, 2026-11-05): one exception to the robots rule, for official keyed APIs
The three keyed services all disallow their API paths in robots.txt: api.adzuna.com and
data.usajobs.gov (`Disallow: /`), and serpapi.com (`/search.json`; `account.json` is allowed). This
was missed at M0, which skipped both APIs for lack of keys. The rule stays: never fetch a
disallowed path. Alex chose one narrow exception (option 2): an **official API that issued us a key
for this use, whose terms allow it**, on that API's own hosts only, and only while its key is set.
- `robots_exception` in a source's entry needs `hosts`, `terms` and `decided`, and is refused for a
  source with no key (`config._source`). `JobSource.robots_waived(url, env)` decides per request;
  `Ctx` skips the check only then, and each collect report counts the requests made under it. The
  probe marks them `robots_exception` instead of blocked.
- Granted: `adzuna` (api.adzuna.com), `usajobs` (data.usajobs.gov), `serpapi-google-jobs`
  (serpapi.com). Everything public and keyless keeps the rule: SmartRecruiters, Remotive, LinkedIn,
  Indeed and every page fetch stay as they were.
- Before the exception, three test requests (1 Adzuna, 2 USAJobs) went out without the robots check;
  nothing else did.

First real responses:
- **Adzuna** (25 a minute, 250 a day; the collector already spaces requests 2.5 s): 44 requests a
  night (7 role types x 5 areas + remote), about 300 postings, nearly all found nowhere else, about 12
  past the filters (many from staffing firms; the model's keep rule and the blocklist handle those).
  Its place is now read from its structured area ("Jefferson Manor, VA", "Fairfax County, VA"), so
  the state is always known: "Oakland, Allegheny County" is Pittsburgh's, not California's. An
  occasional Adzuna 503 leaves the source "partial" for the night.
- **USAJobs**: the first saved key lacked its final "=" (a double-click selection stops before it);
  with it, the API answers (28 software jobs in DC).
- **SerpAPI**: Free Plan, 250 searches a month, all left; the Google sample starts with the nightly run.
- **USAJobs fixed** (Alex re-saved the key with its "="): the API answers.
- **Profile v8: counties** (Alex: "make inferences based on the city list"). Each area gains the
  counties that contain its listed cities, and only those: DC (Arlington, Fairfax, Loudoun, Prince
  William, Montgomery, Prince George's, Howard, Anne Arundel), New York (New York, Kings, Queens,
  Bronx, Richmond, Hudson NJ, Essex NJ), the Bay Area (San Francisco, San Mateo, Alameda, Santa
  Clara), Boston (Suffolk, Middlesex, Norfolk), Austin (Travis, Williamson). It lets Adzuna's
  neighborhood-named jobs match ("Rosslyn, VA" with "Arlington County, VA"); no open posting from
  another source changed place result.
