#!/usr/bin/env bash
# Deploy Debrief v2: point the production checkout at a pushed commit, rebuild the image, run the
# tests there, and roll back if they fail. The timers run whatever this leaves checked out.
#
#   deploy/update.sh            the latest origin/main
#   deploy/update.sh <commit>   a specific commit (a rollback, say)
#
# The checkout (/srv/apps/debrief-v2/repo) is a git worktree of the dev repo, detached at the
# commit, so only pushed code is deployed and the dev working copy can change freely. It takes
# the runs' lock, so it never swaps code under a running collect.
set -Eeuo pipefail

APP=${DEBRIEF_APP:-/srv/apps/debrief-v2}
SRC=${DEBRIEF_SRC:-$HOME/repo-work/debrief}
DATA=${DEBRIEF_DATA_DIR:-/srv/data/debrief-v2}
IMAGE=${DEBRIEF_IMAGE:-debrief-v2:run}
CHECKOUT=$APP/repo

log() { printf '%s %s\n' "$(date +%T)" "$*"; }
die() { log "FAIL: $*"; exit 1; }

mkdir -p "$APP"
exec 9>"$DATA/.run.lock"
flock -w 1800 9 || die "a run has held $DATA/.run.lock for 30 minutes"

git -C "$SRC" fetch -q origin main
target=$(git -C "$SRC" rev-parse --verify "${1:-origin/main}^{commit}")
previous=$(git -C "$CHECKOUT" rev-parse HEAD 2>/dev/null || true)

if [ -z "$previous" ]; then
  git -C "$SRC" worktree add -q --detach "$CHECKOUT" "$target"
else
  git -C "$CHECKOUT" checkout -q --detach "$target"
fi
log "checkout: ${previous:0:7} -> ${target:0:7} ($(git -C "$CHECKOUT" log -1 --format=%s))"

build_and_test() {
  docker build -q --target dev -t "$IMAGE" -f "$CHECKOUT/deploy/Dockerfile" "$CHECKOUT" >/dev/null &&
  docker run --rm --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --network none \
    --read-only --tmpfs /tmp --tmpfs /home/debrief:uid=1000,gid=1000,mode=0700 \
    -v "$CHECKOUT:/app:ro" -w /app "$IMAGE" python -m pytest -q -p no:cacheprovider </dev/null
}

if build_and_test; then
  log "deployed ${target:0:7}"
else
  [ -n "$previous" ] || die "tests failed on ${target:0:7}, and there's no earlier deploy to go back to"
  git -C "$CHECKOUT" checkout -q --detach "$previous"
  build_and_test >/dev/null || die "tests failed on ${target:0:7}, and again on ${previous:0:7} after rolling back"
  die "tests failed on ${target:0:7}; rolled back to ${previous:0:7}"
fi
