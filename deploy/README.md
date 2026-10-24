# deploy/

- `Dockerfile` builds the v2 image: Python 3.13, the locked dependencies, and Claude Code pinned by version and checked against the release's SHA-256. Only the `dev` target exists so far. It holds no code; `scripts/dev` and `run.sh` mount a checkout at run time (tagged `debrief-v2:dev` for development, `debrief-v2:run` for the timers).
- `run.sh`, `update.sh` and `systemd/` are the scheduled runs (below).
- `requirements.in` / `requirements-dev.in` are the direct dependencies. `requirements.lock` / `requirements-dev.lock` are exact pins from `pip freeze` in `python:3.13-slim`.
- `../.dockerignore` limits the build context to this folder, so `.env`, data and other secrets can never reach an image.

## Scheduled runs

| Timer | When (ET) | What | Uses Claude |
|---|---|---|---|
| `debrief-v2-daily` | 03:00 | `run.sh daily`: collect every source, then `triage` with the scoring call | yes: one Sonnet call (about 40k tokens in), plus a one-word auth check |
| `debrief-v2-poll` | 09:00, 15:00, 21:00 | `run.sh poll`: `collect --cadence 6h` (Hacker News, LessWrong, the Alignment Forum, their comments) | no |

The daily run is at 03:00 because the scoring call is the only step that draws on the Claude plan, and that's when the plan is least likely to be in use. The polls fill in the other three 6-hour slots; the daily run collects the discussion sources too. None of the polls touches arXiv, so the 09:00 poll can overlap the v1 run, which shares arXiv's budget.

- **What runs:** the checkout at `/srv/apps/debrief-v2/repo`, a git worktree of the dev repo, detached at a pushed commit of `v2`. The dev working copy never runs on a timer. `deploy/update.sh` moves the checkout to the latest `origin/v2` (or a given commit), rebuilds `debrief-v2:run`, runs the tests there, and rolls back if they fail.
- **Secrets:** `/srv/apps/debrief-v2/.env` (mode 600) has the collector's `GITHUB_PAT` and `DEBRIEF_CONTACT`. Triage gets only the Claude token (`/srv/apps/debrief/claude.env`, shared with v1).
- **Containers:** read-only root and code, with only the data dir writable, no capabilities, and memory and pid limits.
- **One lock:** `/srv/data/debrief-v2/.run.lock`. It's taken by the runs, `update.sh` and `scripts/dev`, so nothing else writes the state database during a run. A poll that finds it taken is skipped; the daily run waits up to an hour.
- **Logs:** `/srv/apps/debrief-v2/logs/<date>/<mode>-<HHMM>.log`, and the journal (`journalctl -u debrief-v2-daily`).
- **Failure:** a unit fails (`systemctl --failed`) when a step fails or the scoring call fell back to the rules-only keep. A degraded collect (a feed down) is only a warning. After a failed scoring call, `run.sh triage` re-runs triage without collecting again.

Installing the units needs root; run this once on the server:

```sh
sudo cp /srv/apps/debrief-v2/repo/deploy/systemd/debrief-v2-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now debrief-v2-daily.timer debrief-v2-poll.timer
systemctl list-timers 'debrief*'
```

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
