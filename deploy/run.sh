#!/usr/bin/env bash
# Debrief v2 scheduled runs. Installed as systemd timers (deploy/systemd/); safe to run by hand.
#
#   run.sh daily    03:00 ET   collect every source, then triage with the scoring call. The scoring
#                              call is the only step that uses the Claude plan, so it runs when the
#                              plan is least likely to be in use.
#   run.sh poll     09:00, 15:00, 21:00 ET   collect --cadence 6h: Hacker News, LessWrong, the
#                              Alignment Forum and their comments. No Claude, no arXiv (the 09:00
#                              poll overlaps the v1 run, which shares arXiv's budget).
#   run.sh triage   triage today again (after a failed scoring call), without collecting
#
# It runs the code in the checkout it lives in: /srv/apps/debrief-v2/repo, pinned to a commit of
# origin/v2 by deploy/update.sh, never the dev working copy. One lock in the data dir serializes
# runs, updates and scripts/dev, so two writers never share the state database. A poll that finds
# the lock taken is skipped; the daily run waits for it.
#
# Exit status: 0 when everything ran (a degraded collect, say one feed down, is logged as a
# warning); 1 when a step failed or the scoring call fell back to the rules-only keep, so the unit
# shows up in `systemctl --failed`.
set -Eeuo pipefail

MODE=${1:-}
case "$MODE" in daily|poll|triage) ;; *) echo "usage: run.sh daily|poll|triage" >&2; exit 2 ;; esac

APP=${DEBRIEF_APP:-/srv/apps/debrief-v2}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=${DEBRIEF_DATA_DIR:-/srv/data/debrief-v2}
IMAGE=${DEBRIEF_IMAGE:-debrief-v2:run}
CLAUDE_ENV=${DEBRIEF_CLAUDE_ENV:-/srv/apps/debrief/claude.env}
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

BASE=(--rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --pids-limit 512
      --read-only --tmpfs /tmp --tmpfs /home/debrief:uid=1000,gid=1000,mode=0700
      -v "$REPO:/app:ro" -v "$DATA:/data" -e DEBRIEF_DATA_DIR=/data -w /app)

LAST=$(mktemp)
trap 'rm -f "$LAST"' EXIT

# A step's exit: 0 ok, 3 degraded but written (warn), anything else a failure. Its output is
# logged, and kept in $LAST until the next step.
step() {
  local name=$1; shift
  log "$name: start"
  set +e
  "$@" </dev/null 2>&1 | tee "$LAST"
  local code=${PIPESTATUS[0]}
  set -e
  case $code in
    0) log "$name: ok" ;;
    3) log "WARN: $name finished degraded (see the line above)" ;;
    *) die "$name exited $code" ;;
  esac
}

collect() {
  step "collect${1:+ ($1)}" timeout 90m docker run "${BASE[@]}" --env-file "$APP/.env" --memory 2g "$IMAGE" \
    python -m debrief collect ${1:+--cadence "$1"}
}

triage() {
  [ -s "$CLAUDE_ENV" ] || die "missing $CLAUDE_ENV (the Claude token)"
  if grep -qs '^ANTHROPIC_API_KEY' "$CLAUDE_ENV" "$APP/.env"; then
    die "ANTHROPIC_API_KEY found in an env file; triage must use the Max plan"
  fi
  local CLAUDE=(--env-file "$CLAUDE_ENV" -e CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1)
  # A logged-out or expired token fails here in seconds, before the scoring call.
  local auth
  auth=$(timeout 180 docker run "${BASE[@]}" "${CLAUDE[@]}" "$IMAGE" \
           claude -p "Reply with exactly the word OK." --model haiku --tools "" --no-session-persistence \
           --setting-sources "" --strict-mcp-config </dev/null 2>&1) || true
  case "$auth" in *OK*) log "claude auth: ok" ;; *) die "claude auth check failed: ${auth:0:300}" ;; esac
  step triage timeout 30m docker run "${BASE[@]}" "${CLAUDE[@]}" --memory 2g "$IMAGE" \
    python -m debrief triage --date "$TODAY"
  if grep -q '(rules_only)' "$LAST"; then
    log "FAIL: the scoring call failed; the index is the rules-only keep (rerun: run.sh triage)"
    FAILED=1
  fi
}

case "$MODE" in
  daily)  collect; triage ;;
  poll)   collect 6h ;;
  triage) triage ;;
esac

log "=== done$([ "$FAILED" = 0 ] || echo ', with failures') ==="
exit "$FAILED"
