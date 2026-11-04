# Jobs source probe (M0)

Generated 2026-11-04T20:50:04Z by `debrief jobs probe-sources`, with User-Agent `debrief-jobs/0.1 (personal job search; awoodka@startupshell.org)`. Each endpoint was requested once (plus one conditional request where it sent validators), through the rate buckets in config/jobs/settings.yml. Full record: `/data/jobs/probe/probe-20261104T205004Z.json`.

Status counts: disabled 5, needs_key 5, ok 22

## Community lists

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| SimplifyJobs New-Grad-Positions | ok | https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json | 19065 | 2026-11-04T15:08Z · 27 in 24 h | Trading Assistant · 1773093246 | company 100%, locations 100%, url 100%, active 100%, date_posted 100%, date_updated 100%, sponsorship 100%, category 100%, degrees 77% | ETag, conditional: 304 | updated about every 30 min; no LICENSE file in the repo |
| vanshb03 New-Grad-2027 | ok | https://raw.githubusercontent.com/vanshb03/New-Grad-2027/dev/.github/scripts/listings.json | 1963 | 2026-11-04T19:40Z · 28 in 24 h | Embedded DSP Software Engineer · 1743415809 | company 100%, locations 100%, url 100%, active 100%, date_posted 100% | ETag, conditional: 304 | urls are go.vansh.nyc short links |
| speedyapply 2027-AI-College-Jobs (US new grad) | ok | https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/NEW_GRAD_USA.md | 512 |  | NVIDIA · Research Scientist - AI for Graphics and Gaming - New College Grad 2026 · Santa Clara, CA | rows_with_board_link: 321, boards_linked: 227 | ETag, conditional: 304 | markdown only (no JSON) |
| speedyapply 2027-SWE-College-Jobs (US new grad) | ok | https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md | 845 |  | Amazon · Software Development Engineer - Embedded Systems Early Career - 2027 - US · Seattle, WA | rows_with_board_link: 566, boards_linked: 339 | ETag, conditional: 304 | markdown only (no JSON) |
| jobright-ai 2026 new-grad lists | ok 3/3 ok | https://raw.githubusercontent.com/jobright-ai/{each}/master/README.md | 1604 |  | Mercado Eletrônico · Forward Deployed Engineer Junior · Denver, CO, United States | rows_with_board_link: 0, boards_linked: 0 | ETag, conditional: 304 | markdown only; README commits hourly; no 2027 repos yet |

## Job-board systems (one example board each)

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| Greenhouse (Anthropic's board) | ok | https://boards-api.greenhouse.io/v1/boards/anthropic/jobs | 680 (total 680) | 2026-11-04T19:44Z · 14 in 24 h | 3P Revenue Accounting & Reporting · 2026-10-19T11:17:24-04:00 | location 100%, updated_at 100%, first_published 100%, url 100% | ETag, conditional: 304 |  |
| Lever (Palantir's board) | ok | https://api.lever.co/v0/postings/palantir?mode=json | 309 | 2026-11-04T15:53Z · 2 in 24 h | Administrative Business Partner · 1786469891368 | location 100%, created 100%, workplace 100%, pay 0%, commitment 100%, url 100% | ETag, conditional: 304 |  |
| Ashby (OpenAI's board) | ok | https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true | 814 | 2026-11-04T20:36Z · 23 in 24 h | Research Engineer · 2025-04-05T00:03:20.653+0 | location 100%, remote 71%, workplace 71%, pay 80%, published 100%, url 100% | ETag, conditional: 304 |  |
| Workable (Hugging Face's board) | ok | https://apply.workable.com/api/v1/widget/accounts/huggingface | 6 | 2026-09-21T00:00Z · 0 in 24 h | Open-Source Machine Learning Engineer - EMEA Remote · 2026-05-29 | country 100%, city 50%, remote 100%, published 100%, url 100% |  |  |
| SmartRecruiters (Visa's board) | disabled |  |  |  |  |  |  | OFF: api.smartrecruiters.com robots.txt allows /v1/companies/ only to LinkedInBot (User-agent: * Disallow: /), checked 2026-11-04. SmartRecruiters jobs reach us through the community lists and LinkedIn alerts. |
| Recruitee (Hatchtech's board) | ok | https://hatchtech.recruitee.com/api/offers/ | 6 | 2026-10-29T08:16Z · 0 in 24 h | Lead Engineer - Productgroep Control Panel · 2026-10-29 08:16:53 UTC | country 100%, city 100%, remote 100%, published 100%, pay 83%, url 100% |  |  |
| Workday (NVIDIA's board) | ok | https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs | 20 (total 2000) | 2026-11-04T20:50Z · 20 in 24 h | Senior ASIC Design Engineer – Clocks IP · Posted Today | location 100%, posted 100%, path 100% |  | list dates are relative ("Posted Today"); the job's own page has the ISO startDate |

## Big companies' own careers APIs

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| Amazon (amazon.jobs) | ok | https://www.amazon.jobs/en/search.json?result_limit=100&offset=0&sort=recent&country=USA | 100 (total 10000) |  | Sr. Implementation Engineer, Amazon Leo · November  4, 2026 | location 100%, city 100%, posted 100%, path 100% | ETag, conditional: 304 | robots.txt allows it, but amazon.jobs links to Amazon's Conditions of Use, which forbid robots and data-gathering tools: Alex decides at the M0 checkpoint |
| Microsoft (careers pcsx API) | ok | https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query=&location=United%20States&start=0 | 10 |  | ['atsJobId', 'creationTs', 'department', 'displayJobId', 'id', 'isHot', 'locationFlexibility', 'locations', '… | items at `data.positions` |  | robots.txt explicitly allows /api/pcsx |
| Apple (jobs.apple.com search API) | disabled |  |  |  |  |  |  | OFF: undocumented, and with the site's filters it returned no results (2026-11-04). Apple's new-grad roles come through the community lists (57 active on Simplify) and LinkedIn alerts. |
| Google Careers | disabled |  |  |  |  |  |  | no API; results pages are disallowed by robots.txt. Covered by LinkedIn alerts and the miss-check. |
| Meta Careers | disabled |  |  |  |  |  |  | robots.txt asks for express written permission for automated collection. Covered by LinkedIn alerts. |

## Job APIs

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| Adzuna (US) | needs_key set ADZUNA_APP_ID, ADZUNA_APP_KEY in .env | https://api.adzuna.com/v1/api/jobs/us/search/1?results_per_page=50&what=software%20engineer&max_days_old=1&content-type=application/json |  |  |  |  |  | personal job search is a permitted use; attribution ("Jobs by Adzuna") required |
| USAJobs (federal) | needs_key set USAJOBS_API_KEY in .env | https://data.usajobs.gov/api/search?Keyword=software&DatePosted=1&ResultsPerPage=100 |  |  |  |  |  | User-Agent must be the email the key was registered with (DEBRIEF_CONTACT) |
| The Muse | ok | https://www.themuse.com/api/public/jobs?page=0&level=Entry%20Level&category=Software%20Engineering | 20 (total 30355) | 2026-11-03T18:37Z · 0 in 24 h | New Graduate Engineer, GNC- '26/'27 (Starlink) · 2026-11-03T18:37:12Z | company 100%, locations 100%, levels 100%, published 100% | ETag, conditional: 200 (validators ignored), x-ratelimit-limit: 500, x-ratelimit-remaining: 499, x-ratelimit-reset: 3600 | 500 requests an hour without a key |
| Himalayas (remote) | ok | https://himalayas.app/jobs/api?limit=20&offset=0 | 20 | 2026-11-04T18:48Z · 20 in 24 h | Federal Regulatory Policy Analyst · 1793818106 | company 100%, location 90%, pay_min 30%, published 100%, url 100% | Last-Modified, conditional: 304 | attribution and link back; apply through applicationLink |
| RemoteOK | ok | https://remoteok.com/api | 100 | 2026-11-03T16:00Z · 0 in 24 h |  | company 99%, location 43%, pay_min 99%, date 99%, url 99% |  | link back to RemoteOK and mention it as the source; the first item is a legal notice |
| We Work Remotely (RSS) | ok | https://weworkremotely.com/remote-jobs.rss | 100 | ? · 6 in 24 h | VXI Global Solutions: US Remote Patient Care Advocate (Customer Service) · Wed, 04 Nov 2026 18:30:56 |  | ETag, conditional: 200 (validators ignored) |  |
| Jobicy (remote) | ok  (carried from the probe at 2026-11-04T20:39:58Z) | https://jobicy.com/api/v2/remote-jobs?count=50&geo=usa | 50 | 2026-11-04T17:26Z · 50 in 24 h | Federal Enterprise Solution Architect · 2026-11-04T17:26:28+00:00 | company 100%, geo 100%, level 100%, pay_min 44%, published 100% |  | poll at most once an hour; credit Jobicy with a link |
| Remotive (remote) | disabled |  |  |  |  |  |  | OFF: remotive.com robots.txt has Disallow: /api/* for every User-Agent (checked 2026-11-04) · jobs appear 24 h late; at most 4 requests a day; attribution; don't resubmit to other boards |

## Hacker News

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| HN "Who is hiring?" (monthly) | ok | https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=6 | 6 | 2026-11-02T16:01Z · 0 in 24 h | Ask HN: Who is hiring? (November 2026) · 2026-11-02T16:01:37Z | id 100%, comments 100% |  | free-text comments, one per company; the November thread is the newest |
| HN job posts (YC companies) | ok | https://hacker-news.firebaseio.com/v0/jobstories.json | 31 |  | 50354582 |  |  |  |

## The alert inbox

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| Job-alert inbox (Gmail IMAP) | needs_key set JOBS_IMAP_USER, JOBS_IMAP_PASSWORD in .env | imaps://imap.gmail.com:993 |  |  |  |  |  | logs in and counts alert emails per sender; reads no message bodies |

## The miss-check sample (Google's job results)

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| SerpAPI account (free; doesn't use a search) | needs_key set SERPAPI_KEY in .env | https://serpapi.com/account.json |  |  |  |  |  |  |
| SerpAPI google_jobs (uses 1 of the 250 free monthly searches) | needs_key set SERPAPI_KEY in .env | https://serpapi.com/search.json?engine=google_jobs&q=software%20engineer%20new%20grad&location=New%20York%2C%20New%20York%2C%20United%20States&hl=en&gl=us |  |  |  |  |  |  |

## Board universe lists

| Source | Status | Endpoint | Items | Newest · last 24 h | Sample | Fields filled | Caching / limits | Notes |
|---|---|---|---|---|---|---|---|---|
| kalil0321/ats-scrapers company lists (MIT) | ok 7/7 ok | https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/{each}.csv | 24074 |  | {'name': '10Beauty', 'slug': '10beauty', 'url': 'https://job-boards.greenhouse.io/10beauty'} |  | ETag, conditional: 304 |  |
| Riley-Dorrington/job-board-aggregator company lists (MIT) | ok 4/4 ok | https://raw.githubusercontent.com/Riley-Dorrington/job-board-aggregator/main/data/{each}_companies.json | 28746 |  | 0x |  | ETag, conditional: 304 |  |

## Waiting on keys

- **Adzuna (US)**: set ADZUNA_APP_ID, ADZUNA_APP_KEY in .env
- **USAJobs (federal)**: set USAJOBS_API_KEY in .env
- **Job-alert inbox (Gmail IMAP)**: set JOBS_IMAP_USER, JOBS_IMAP_PASSWORD in .env
- **SerpAPI account (free; doesn't use a search)**: set SERPAPI_KEY in .env
- **SerpAPI google_jobs (uses 1 of the 250 free monthly searches)**: set SERPAPI_KEY in .env

## The board universe

Boards per system, from each list (after merging both lists, duplicates removed):

| System | universe-kalil | universe-riley |
|---|---|---|
| greenhouse | 6031 | 8333 |
| lever | 2402 | 4368 |
| ashby | 3448 | 3161 |
| workable | 4752 |  |
| smartrecruiters | 2747 |  |
| recruitee | 1164 |  |
| workday | 3530 | 12884 |

### A full nightly poll, extrapolated from a sample

Each system's sample was listed once. Minutes assume one request at a time per system, at the larger of the bucket's interval and the median response time. Robots.txt is one extra request per tenant host on Workday and Recruitee.

| System | Universe | Sampled | Alive | Dead | Errors | Blocked | Median s | p90 s | Median KB | Jobs/board | Pages/board | New 24 h/board | 304s | Est. requests | Est. min | Est. MB | Est. new jobs/night | …rough title match |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| greenhouse | 9868 | 50 | 32 (64%) | 18 | 0 | 0 | 0.5 | 0.51 | 3.4 | 26.9 | 1 | 0.75 | 5/5 returned 304 | 6317 | 52.6 | 21.7 | 4737 | 0 |
| lever | 4831 | 50 | 23 (46%) | 27 | 0 | 0 | 0.51 | 3.06 | 118.1 | 14.9 | 1 | 0.09 | 5/5 returned 304 | 2223 | 18.9 | 268.7 | 193 | 0 |
| ashby | 4318 | 50 | 40 (80%) | 10 | 0 | 0 | 0.5 | 0.56 | 70.4 | 12.9 | 1 | 0.2 | 5/5 returned 304 | 3455 | 28.8 | 249.0 | 691 | 173 |
| workable | 4752 | 50 | 49 (98%) | 1 | 0 | 0 | 1.0 | 1.01 | 1.6 | 6.1 | 1 | 0.12 | — | 4658 | 77.6 | 7.8 | 570 | 0 |
| smartrecruiters | 2747 | 50 | 0 (0%) | 0 | 0 | 50 | None | None | None | None | 1 | None | — | 1 | 0.0 | 0.0 | 0 | 0 |
| recruitee | 1164 | 50 | 48 (96%) | 2 | 0 | 0 | 0.39 | 0.47 | 60.5 | 16.0 | 1 | 0.42 | — | 2234 | 18.6 | 69.2 | 465 | 0 |
| workday | 14118 | 50 | 29 (57%) | 19 | 2 | 0 | 0.7 | 1.13 | 4.3 | 77 | 4.48 | 3.72 | — | 44893 | 523.8 | 160.3 | 30493 | 1129 |

Totals: about 63781 requests and 777 MB a night; 720 min one system after another, or 524 min with the systems in parallel (one request at a time each). About 37149 new jobs a night, 1302 of them past the rough title filter (before location and level).

- workday: Workday's dates are relative, so "new" here is Posted Today or Yesterday (up to 48 h), counted on each board's first page only: a rough figure either way

### What the community lists link to

6834 active listings across the lists. 2882 of them (42%) link to a known job-board system; the rest are company-hosted pages (big tech, custom sites) or the list's own short links.

| System | Boards linked | Not in the universe yet |
|---|---|---|
| ashby | 205 | 65 |
| greenhouse | 242 | 37 |
| lever | 78 | 12 |
| recruitee | 1 | 0 |
| smartrecruiters | 61 | 52 |
| workable | 73 | 20 |
| workday | 487 | 194 |

