#!/usr/bin/env bash
# The jobs site's guard (docs/jobs/PLAN.md, M4), the chat.alexwoodka.com pattern. Run from Alex's
# crontab every 10 minutes:
#   */10 * * * * /srv/apps/debrief-v2/repo/deploy/web-guard.sh >> /srv/apps/debrief-v2/logs/web-guard.log 2>&1
# From outside, https://jobs.alexwoodka.com/ must send an unauthenticated visitor, and one with a
# forged Access header, to Cloudflare Access's login. Two failed checks in a row take the site's
# container off the `edge` network (Caddy can't reach it); a later check that passes puts it back.
# A host that isn't online at all (no tunnel route yet: Cloudflare error or no answer) counts as safe.
set -Eeuo pipefail

URL=${JOBS_WEB_URL:-https://jobs.alexwoodka.com/}
NAME=${JOBS_WEB_CONTAINER:-debrief-jobs-web}
STATE=${JOBS_WEB_GUARD_STATE:-/srv/apps/debrief-v2/logs/web-guard.state}
mkdir -p "$(dirname "$STATE")"

stamp() { date '+%F %T'; }
# "pass", "offline" or "fail: <why>" for one request (extra curl args in $@)
check() {
  local out code loc
  out=$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{redirect_url}' "$@" "$URL" 2>/dev/null || true)
  code=${out%% *}; loc=${out#* }
  case "$code" in
    301|302|303|307|308)
      [[ $loc =~ ^https://[a-z0-9-]+\.cloudflareaccess\.com/ ]] && { echo pass; return; }
      echo "fail: redirected to $loc"; return ;;
    000|""|52[0-9]|530) echo offline; return ;;
    *) echo "fail: answered $code" ;;
  esac
}

plain=$(check)
forged=$(check -H "Cf-Access-Jwt-Assertion: forged")
fails=$(cat "$STATE" 2>/dev/null || echo 0)
connected=$(docker inspect -f '{{if index .NetworkSettings.Networks "edge"}}yes{{else}}no{{end}}' "$NAME" 2>/dev/null || echo absent)

if [[ $plain == fail* || $forged == fail* ]]; then
  fails=$((fails + 1)); echo "$fails" > "$STATE"
  echo "$(stamp) FAIL ($fails in a row): plain=$plain forged=$forged"
  if [ "$fails" -ge 2 ] && [ "$connected" = yes ]; then
    docker network disconnect edge "$NAME" && echo "$(stamp) took $NAME off the edge network: Access isn't in front of $URL"
  fi
else
  echo 0 > "$STATE"
  if [ "$connected" = no ]; then
    docker network connect --alias jobs-web edge "$NAME" && echo "$(stamp) Access works again ($plain): put $NAME back on the edge network"
  elif [ "$fails" -gt 0 ]; then
    echo "$(stamp) ok again: plain=$plain forged=$forged"
  fi
fi
