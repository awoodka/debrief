#!/usr/bin/env bash
# Install or update the jobs site's block in the shared edge Caddyfile (docs/jobs/PLAN.md, M4), the
# way LLM Lab's apply-caddy.sh does it:
#   1. build a candidate: deploy/edge/Caddyfile.jobs between the `# >>> jobs` / `# <<< jobs` markers,
#      or, the first time, inserted just before the catch-all `:80` block
#   2. validate it in a throwaway container from the running Caddy image
#   3. back up the live file and write the candidate IN PLACE (the Caddyfile is a single-file bind
#      mount: replacing its inode would leave Caddy on the old file)
#   4. restart Caddy (`admin off` rules out a hot reload; every site blips for a few seconds), check
#      every site, and restore the backup on any failure
#   deploy/edge-jobs.sh [--check | --yes]     --check: show the diff, change nothing
set -Eeuo pipefail

F=${EDGE_CADDYFILE:-/srv/edge/Caddyfile}
C=${EDGE_CADDY_CONTAINER:-edge-caddy-1}
BLOCK=$(cd "$(dirname "$0")" && pwd)/edge/Caddyfile.jobs
HOST=jobs.alexwoodka.com
PUBLIC=(https://debrief.alexwoodka.com https://timbre.alexwoodka.com https://localinference.alexwoodka.com)

mode=ask
case "${1:-}" in "") ;; --yes) mode=yes ;; --check) mode=check ;; *) echo "usage: $0 [--check | --yes]" >&2; exit 2 ;; esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cand=$work/Caddyfile
python3 - "$F" "$BLOCK" "$cand" <<'PY'
import sys
live, block, out = sys.argv[1:]
src, new = open(live).read(), open(block).read().rstrip("\n") + "\n"
start, end = "# >>> jobs", "# <<< jobs"
if start in src:
    i = src.index(start); j = src.index(end, i); j = src.find("\n", j) + 1 or len(src)
    result = src[:i] + new + src[j:]
else:
    anchor = next((a for a in ("# Anything cloudflared forwards", ":80 {") if a in src), None)
    if anchor is None:
        sys.exit("no insertion point: neither the jobs markers nor the :80 block")
    i = src.index(anchor)
    result = src[:i] + new + "\n" + src[i:]
open(out, "w").write(result)
PY

if cmp -s "$F" "$cand"; then echo "Caddyfile already up to date"; exit 0; fi
image=$(docker inspect -f '{{.Image}}' "$C")
chmod 755 "$work"; chmod 644 "$cand"
docker run --rm --network none -v "$cand:/etc/caddy/Caddyfile:ro" "$image" caddy validate --config /etc/caddy/Caddyfile \
  --adapter caddyfile >/dev/null 2>"$work/validate.log" || { cat "$work/validate.log"; echo "FAIL: Caddy rejects the candidate; nothing changed"; exit 2; }
echo "Caddy accepts the candidate. The change:"
diff -u "$F" "$cand" || true
[ "$mode" = check ] && exit 0
if [ "$mode" = ask ]; then
  read -rp "Apply it and restart Caddy (every site blips for a few seconds)? [y/N] " ok
  [ "$ok" = y ] || exit 1
fi

backup=$F.bak-$(date +%Y%m%d-%H%M%S)
cp -p "$F" "$backup"; echo "backup: $backup"
inode=$(stat -c %i "$F")
code() { local c; c=$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true); echo "${c:-000}"; }
inside() {   # Caddy's answer for Host $1 with no Access header, asked from inside its own container
  docker exec "$C" wget -q -S -O /dev/null --header "Host: $1" http://127.0.0.1/ 2>&1 | grep -oE 'HTTP/1\.[01] [0-9]{3}' \
    | tail -1 | cut -d' ' -f2 || true
}
healthy() { for _ in $(seq 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$C")" = healthy ] && return 0; sleep 2; done; return 1; }
restore() {
  echo "restoring $backup"; cat "$backup" > "$F"; docker restart "$C" >/dev/null
  healthy || echo "WARN: Caddy isn't healthy after restoring: docker logs $C"
  for u in "${PUBLIC[@]}"; do echo "$u after restore: $(code "$u")"; done
  exit 1
}

cat "$cand" > "$F"
[ "$(stat -c %i "$F")" = "$inode" ] || { echo "FAIL: the Caddyfile's inode changed"; restore; }
docker exec "$C" cat /etc/caddy/Caddyfile | cmp -s - "$F" || { echo "FAIL: Caddy's container sees a different file"; restore; }
docker restart "$C" >/dev/null
healthy || { echo "FAIL: Caddy didn't become healthy"; restore; }

problems=()
for u in "${PUBLIC[@]}"; do c=$(code "$u"); [[ $c =~ ^[23] ]] || problems+=("$u answered $c"); done
[ "$(inside chat.alexwoodka.com)" = 403 ] || problems+=("chat.alexwoodka.com without an Access header didn't answer 403")
[ "$(inside "$HOST")" = 403 ] || problems+=("$HOST without an Access header didn't answer 403")
if [ ${#problems[@]} -gt 0 ]; then printf 'FAIL: %s\n' "${problems[@]}"; restore; fi
echo "applied: every site answers, and $HOST refuses requests without an Access header"
