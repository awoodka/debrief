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
- Open: Raydar (966 near-identical Workable postings) twice in the top 20; 6 old reposts left there
  (no board known); SambaNova has a gh_jid but no description.
