# deploy/

- `Dockerfile` builds the image: Python 3.13, the locked dependencies, and Claude Code pinned by version and checked against the release's SHA-256. Only the `dev` target exists so far. It holds no code; `scripts/dev` and `run.sh` mount a checkout at run time (tagged `debrief-v2:dev` for development, `debrief-v2:run` for the timers).
- `run.sh`, `update.sh` and `systemd/` are the scheduled runs (below).
- `requirements.in` / `requirements-dev.in` are the direct dependencies. `requirements.lock` / `requirements-dev.lock` are exact pins from `pip freeze` in `python:3.13-slim`.
- `../.dockerignore` limits the build context to this folder, so `.env`, data and other secrets can never reach an image.

## Scheduled runs

| Timer | When (ET) | What | Uses Claude |
|---|---|---|---|
| `debrief-v2-daily` | 03:00 | `run.sh daily`: collect every source, `triage` with the scoring call, the brief, then publish the site | yes: one Sonnet call (about 40k tokens in), the brief's Opus session (1–3 min), and a one-word auth check |
| `debrief-v2-poll` | 09:00, 15:00, 21:00 | `run.sh poll`: `collect --cadence 6h` (Hacker News, LessWrong, the Alignment Forum, their comments) | no |

The daily run is at 03:00 because the scoring call and the brief are the steps that draw on the Claude plan, and that's when the plan is least likely to be in use. The polls fill in the other three 6-hour slots; the daily run collects the discussion sources too. None of the polls touches arXiv.

Other modes, for reruns by hand: `run.sh triage` (triage again, without collecting), `run.sh brief` (the brief again, then the site), and `run.sh site` (rebuild and publish the site).

- **What runs:** the checkout at `/srv/apps/debrief-v2/repo`, a git worktree of the dev repo, detached at a pushed commit of `main`. The dev working copy never runs on a timer. `deploy/update.sh` moves the checkout to the latest `origin/main` (or a given commit), rebuilds `debrief-v2:run`, runs the tests there, and rolls back if they fail.
- **Secrets:** `/srv/apps/debrief-v2/.env` (mode 600) has the collector's `GITHUB_PAT` and `DEBRIEF_CONTACT`. Triage and the brief get only the Claude token (`/srv/apps/debrief-v2/claude.env`, mode 600).
- **Containers:** read-only root and code, with only the data dir writable, no capabilities, and memory and pid limits. The site build also has no network and reads the data read-only.
- **One lock:** `/srv/data/debrief-v2/.run.lock`. It's taken by the runs, `update.sh` and `scripts/dev`, so nothing else writes the state database during a run. A poll that finds it taken is skipped; the daily run waits up to an hour.
- **Logs:** `/srv/apps/debrief-v2/logs/<date>/<mode>-<HHMM>.log`, and the journal (`journalctl -u debrief-v2-daily`).
- **Failure:** a unit fails (`systemctl --failed`) when a step fails, the scoring call fell back to the rules-only keep, or the brief has no article. A degraded collect (a feed down) is only a warning. A brief without its article still publishes: the day's page is the scan, with a note, and no `reported.json` is written, so those stories come back tomorrow. After a failed scoring call, `run.sh triage` then `run.sh brief`.

Installing the units needs root; run this once on the server:

```sh
sudo cp /srv/apps/debrief-v2/repo/deploy/systemd/debrief-v2-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now debrief-v2-daily.timer debrief-v2-poll.timer
systemctl list-timers 'debrief*'
```

## The site

debrief.alexwoodka.com is static files in `/srv/www/debrief`, served by the edge stack's Caddy (`/srv/edge`, which mounts that folder read-only) through the Cloudflare Tunnel. Each publish writes `releases/<stamp>/`, swaps the `current` symlink to it atomically (Caddy follows it per request), and keeps the newest 5 releases. The page itself is `debrief site build` (docs/README.md, "How the page works").

v2 took the site over from the v1 pipeline on 2026-10-25. v1's day pages (2026-09-14 to 2026-10-25) were copied byte for byte into `/srv/data/debrief-v2/site/v1/`, and every build lists them in the archive. v1 itself is gone: its code is in git history (before the merge into `main`), and its server folders, data and logs are in `/srv/data/archive/debrief-v1-2026-10-25.tar.gz`.

To put an earlier release back (a bad build, say), point `current` at it: `ln -sfn releases/<stamp> /srv/www/debrief/.current.tmp && mv -T /srv/www/debrief/.current.tmp /srv/www/debrief/current`. The next publish replaces it.

## Regenerating the locks

Run from the repo root:

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD/deploy:/deploy" python:3.13-slim sh -c '
  set -e
  python -m venv /tmp/rt && /tmp/rt/bin/pip install -q -r /deploy/requirements.in
  { echo "# Exact pins for requirements.in, from pip freeze in python:3.13-slim on $(date +%F)."; /tmp/rt/bin/pip freeze; } > /deploy/requirements.lock
  python -m venv /tmp/dv && /tmp/dv/bin/pip install -q -r /deploy/requirements-dev.in
  { echo "# Exact pins for requirements-dev.in (runtime + test tools), from pip freeze on $(date +%F)."; /tmp/dv/bin/pip freeze; } > /deploy/requirements-dev.lock'
scripts/dev build
```

## Bumping Claude Code

Change `CLAUDE_VERSION` in the Dockerfile and rebuild. The build fails if the download doesn't match the checksum in that release's manifest.
