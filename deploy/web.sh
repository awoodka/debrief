#!/usr/bin/env bash
# The jobs site (docs/jobs/PLAN.md, M4): one long-running container, debrief-jobs-web, serving
# jobs.alexwoodka.com through Caddy on the `edge` network (alias jobs-web, port 8000, nothing
# published on the host). Cloudflare Access is the gate; the app verifies every token too.
#
#   deploy/web.sh up        start it (refuses without a complete web.env)
#   deploy/web.sh restart   recreate it, so a rebuilt image and new code are live (update.sh does this)
#   deploy/web.sh down      stop and remove it
#   deploy/web.sh status    its state, and whether it refuses a request with no Access token
#
# It runs like the nightly steps: the same image, the prod checkout read-only at /app, a read-only
# root, no capabilities, uid 1000. It may write only <data>/jobs (the Tracked mark, profile versions).
# Its settings, and nothing else, are in $APP/web.env (mode 600):
#   JOBS_WEB_ACCESS_TEAM, JOBS_WEB_ACCESS_AUD, JOBS_WEB_ALLOWED_EMAILS, JOBS_WEB_ORIGIN, DEBRIEF_CONTACT
set -Eeuo pipefail

APP=${DEBRIEF_APP:-/srv/apps/debrief-v2}
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=${DEBRIEF_DATA_DIR:-/srv/data/debrief-v2}
IMAGE=${DEBRIEF_IMAGE:-debrief-v2:run}
NAME=${JOBS_WEB_CONTAINER:-debrief-jobs-web}
ENV_FILE=${JOBS_WEB_ENV:-$APP/web.env}

log() { printf '%s %s\n' "$(date +%T)" "$*"; }
die() { log "FAIL: $*"; exit 1; }

check_env() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE is missing"
  [ "$(stat -c %a "$ENV_FILE")" = 600 ] || die "$ENV_FILE must be mode 600"
  for k in JOBS_WEB_ACCESS_TEAM JOBS_WEB_ACCESS_AUD JOBS_WEB_ALLOWED_EMAILS JOBS_WEB_ORIGIN; do
    grep -Eq "^$k=.+" "$ENV_FILE" || die "$ENV_FILE has no $k"
  done
}

up() {
  check_env
  docker network inspect edge >/dev/null 2>&1 || die "the edge network doesn't exist"
  docker run -d --name "$NAME" --restart unless-stopped \
    --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 512m \
    --read-only --tmpfs /tmp --tmpfs /home/debrief:uid=1000,gid=1000,mode=0700 \
    --log-opt max-size=10m --log-opt max-file=3 \
    -v "$REPO:/app:ro" -v "$DATA/jobs:/data/jobs" -e DEBRIEF_DATA_DIR=/data -w /app \
    --env-file "$ENV_FILE" --network edge --network-alias jobs-web \
    "$IMAGE" python -m debrief jobs web --port 8000 >/dev/null
  for _ in $(seq 30); do
    [ "$(probe)" = 403 ] && { log "up: $NAME answers, and refuses a request with no Access token (403)"; return 0; }
    sleep 1
  done
  docker logs --tail 30 "$NAME" >&2 || true
  die "$NAME didn't come up (see its log above)"
}

# The status of a request with no Access token, from inside the container: it must be 403.
probe() {
  docker exec "$NAME" python -c 'import urllib.request, urllib.error
try:
    urllib.request.urlopen("http://127.0.0.1:8000/", timeout=5); print(200)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print(0)' 2>/dev/null || echo 0
}

down() { docker rm -f "$NAME" >/dev/null 2>&1 && log "down: $NAME removed" || log "down: $NAME wasn't there"; }

case "${1:-}" in
  up)      docker inspect "$NAME" >/dev/null 2>&1 && die "$NAME already exists (use restart)"; up ;;
  restart) check_env; down; up ;;
  down)    down ;;
  status)
    docker inspect -f '{{.Name}} {{.State.Status}} since {{.State.StartedAt}} image {{.Config.Image}}' "$NAME" 2>/dev/null \
      || { echo "$NAME isn't running"; exit 1; }
    echo "a request with no Access token gets: $(probe) (must be 403)"
    docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}network {{$k}} {{end}}' "$NAME" ;;
  *) echo "usage: deploy/web.sh up|restart|down|status" >&2; exit 2 ;;
esac
