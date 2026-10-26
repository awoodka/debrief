#!/usr/bin/env bash
# Debrief v2 scheduled runs. Installed as systemd timers (deploy/systemd/); safe to run by hand.
#
#   run.sh daily    03:00 ET   collect every source, triage with the scoring call, write the brief
#                              (the skill's Claude Code session), and publish the site. The scoring
#                              call and the brief are the steps that use the Claude plan, so they run
#                              when the plan is least likely to be in use.
#   run.sh poll     09:00, 15:00, 21:00 ET   collect --cadence 6h: Hacker News, LessWrong, the
#                              Alignment Forum and their comments. No Claude, no arXiv (the 09:00
#                              poll overlaps the v1 run, which shares arXiv's budget).
#   run.sh triage   triage today again (after a failed scoring call), without collecting
#   run.sh brief    write today's brief again (after `run.sh triage`, say), then publish the site
#   run.sh site     rebuild the site from every brief.json and publish it
#   run.sh import-v1   once, at the takeover: keep the old pipeline's last release as
#                   $WWW/v1-final (for a rollback) and copy its day pages into the data dir
#
# The site is static files in $WWW (/srv/www/debrief), which Caddy serves as debrief.alexwoodka.com:
# each publish is a new releases/<stamp>/, `current` is swapped to it atomically, and the newest 5
# releases are kept. The old pipeline published to the same folder, so this never publishes there
# while its debrief.timer is enabled. To roll back to it: point `current` at v1-final and re-enable
# that timer (deploy/README.md).
#
# It runs the code in the checkout it lives in: /srv/apps/debrief-v2/repo, pinned to a commit of
# origin/v2 by deploy/update.sh, never the dev working copy. One lock in the data dir serializes
# runs, updates and scripts/dev, so two writers never share the state database. A poll that finds
# the lock taken is skipped; the daily run waits for it.
#
# Exit status: 0 when everything ran (a degraded collect, say one feed down, is logged as a
# warning); 1 when a step failed, the scoring call fell back to the rules-only keep, or the brief
# has no article (the site still publishes its scan), so the unit shows up in `systemctl --failed`.
set -Eeuo pipefail

MODE=${1:-}
case "$MODE" in daily|poll|triage|brief|site|import-v1) ;;
  *) echo "usage: run.sh daily|poll|triage|brief|site|import-v1" >&2; exit 2 ;; esac

APP=${DEBRIEF_APP:-/srv/apps/debrief-v2}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=${DEBRIEF_DATA_DIR:-/srv/data/debrief-v2}
IMAGE=${DEBRIEF_IMAGE:-debrief-v2:run}
CLAUDE_ENV=${DEBRIEF_CLAUDE_ENV:-/srv/apps/debrief/claude.env}
WWW=${DEBRIEF_WWW:-/srv/www/debrief}
LIVE_WWW=/srv/www/debrief
TODAY=$(TZ=America/New_York date +%F)
LOGDIR=$APP/logs/$TODAY
FAILED=0

mkdir -p "$LOGDIR"
exec > >(tee -a "$LOGDIR/$MODE-$(date +%H%M).log") 2>&1

log() { printf '%s %s\n' "$(date +%T)" "$*"; }
die() { log "FAIL: $*"; exit 1; }

[ ! -d "$DATA/debriefs" ] || die "$DATA holds the v1 pipeline's data; refusing"
exec 9>"$DATA/.run.lock"
if [ "$MODE" = poll ]; then
  flock -n 9 || { log "another run holds $DATA/.run.lock; skipping this poll"; exit 0; }
else
  flock -w 3600 9 || die "waited an hour for $DATA/.run.lock"
fi

log "=== debrief v2 $MODE $TODAY · $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '?') ==="
docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE not built (run deploy/update.sh)"
[ -s "$APP/.env" ] || die "missing $APP/.env (the collector's GITHUB_PAT and DEBRIEF_CONTACT)"

SANDBOX=(--rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --pids-limit 512
         --read-only --tmpfs /tmp --tmpfs /home/debrief:uid=1000,gid=1000,mode=0700
         -v "$REPO:/app:ro" -e DEBRIEF_DATA_DIR=/data -w /app)
BASE=("${SANDBOX[@]}" -v "$DATA:/data")

LAST=$(mktemp)
trap 'rm -f "$LAST"' EXIT

# Runs a step: its output is logged, and kept in $LAST until the next step. Its exit is in $CODE.
run_step() {
  local name=$1; shift
  log "$name: start"
  set +e
  "$@" </dev/null 2>&1 | tee "$LAST"
  CODE=${PIPESTATUS[0]}
  set -e
}

# A step whose exit means: 0 ok, 3 degraded but written (warn), anything else a failure.
step() {
  run_step "$@"
  case $CODE in
    0) log "$1: ok" ;;
    3) log "WARN: $1 finished degraded (see the line above)" ;;
    *) die "$1 exited $CODE" ;;
  esac
}

collect() {
  step "collect${1:+ ($1)}" timeout 90m docker run "${BASE[@]}" --env-file "$APP/.env" --memory 2g "$IMAGE" \
    python -m debrief collect ${1:+--cadence "$1"}
}

# The Claude token is there, no API key is, and the token works. A logged-out or expired token
# fails here in seconds, before the scoring call or the brief.
claude_ready() {
  [ -s "$CLAUDE_ENV" ] || die "missing $CLAUDE_ENV (the Claude token)"
  if grep -qs '^ANTHROPIC_API_KEY' "$CLAUDE_ENV" "$APP/.env"; then
    die "ANTHROPIC_API_KEY found in an env file; triage and the brief must use the Max plan"
  fi
  local auth
  auth=$(timeout 180 docker run "${BASE[@]}" --env-file "$CLAUDE_ENV" "$IMAGE" \
           claude -p "Reply with exactly the word OK." --model haiku --tools "" --no-session-persistence \
           --setting-sources "" --strict-mcp-config </dev/null 2>&1) || true
  case "$auth" in *OK*) log "claude auth: ok" ;; *) die "claude auth check failed: ${auth:0:300}" ;; esac
}

triage() {
  claude_ready
  step triage timeout 30m docker run "${BASE[@]}" --env-file "$CLAUDE_ENV" \
    -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 --memory 2g "$IMAGE" python -m debrief triage --date "$TODAY"
  if grep -q '(rules_only)' "$LAST"; then
    log "FAIL: the scoring call failed; the index is the rules-only keep (rerun: run.sh triage)"
    FAILED=1
  fi
}

# The brief: the skill's session (it needs the network, for Claude and WebFetch), then finalize.
# Not CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, which can hide WebFetch. A brief without its article
# fails the run but not the step after it: the site still publishes the day's scan.
brief() {
  claude_ready
  run_step brief timeout 60m docker run "${BASE[@]}" --env-file "$CLAUDE_ENV" --memory 2g "$IMAGE" \
    python -m debrief brief run --date "$TODAY"
  case $CODE in
    0) log "brief: ok" ;;
    3) log "FAIL: the brief has no article, only the scan (see brief_session.jsonl; rerun: run.sh brief)"; FAILED=1 ;;
    *) log "FAIL: brief exited $CODE (rerun: run.sh brief)"; FAILED=1 ;;
  esac
}

# The site: built in a container with no network and the data read-only, into a new release, then
# swapped in.
site() {
  if [ "$WWW" = "$LIVE_WWW" ] && systemctl is-enabled --quiet debrief.timer 2>/dev/null; then
    log "WARN: the old pipeline's debrief.timer is enabled, so the site isn't published" \
        "(sudo systemctl disable --now debrief.timer, then run.sh site)"
    return
  fi
  local stamp rel
  stamp=$(date +%Y%m%d-%H%M%S)
  rel=$WWW/releases/$stamp
  mkdir -p "$rel"
  run_step site timeout 10m docker run "${SANDBOX[@]}" -v "$DATA:/data:ro" -v "$rel:/out" --network none \
    --memory 1g "$IMAGE" python -m debrief site build --out /out
  if [ "$CODE" != 0 ] || [ ! -f "$rel/index.html" ]; then
    rm -rf "$rel"; die "site build failed (exit $CODE)"
  fi
  if [ -f "$DATA/runs/$TODAY/brief.json" ] && [ ! -f "$rel/$TODAY/index.html" ]; then
    rm -rf "$rel"; die "the site build has no page for $TODAY"
  fi
  ln -sfn "releases/$stamp" "$WWW/.current.tmp"
  mv -T "$WWW/.current.tmp" "$WWW/current"   # atomic: readers never see a half-written site
  ls -1dt "$WWW"/releases/*/ | tail -n +6 | xargs -r rm -rf
  log "site: published, current -> releases/$stamp"
}

# Once, at the takeover. v1-final is outside releases/, so pruning never removes it. A later run
# imports from v1-final again, never from a release v2 published.
import_v1() {
  if [ ! -e "$WWW/v1-final" ]; then
    [ -d "$WWW/current" ] || die "no site at $WWW/current"
    cp -a "$(readlink -f "$WWW/current")" "$WWW/v1-final"
    log "import-v1: kept $(readlink "$WWW/current") as $WWW/v1-final"
  fi
  step import-v1 timeout 10m docker run "${BASE[@]}" -v "$WWW/v1-final:/v1site:ro" --network none "$IMAGE" \
    python -m debrief site import-v1 --from /v1site
}

case "$MODE" in
  daily)     collect; triage; brief; site ;;
  poll)      collect 6h ;;
  triage)    triage ;;
  brief)     brief; site ;;
  site)      site ;;
  import-v1) import_v1 ;;
esac

log "=== done$([ "$FAILED" = 0 ] || echo ', with failures') ==="
exit "$FAILED"
