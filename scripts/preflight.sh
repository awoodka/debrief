#!/usr/bin/env bash
# Preflight for the daily debrief: does the council still assemble?
#
# This checks only what lives in this repo. The server's run.sh (outside the repo)
# already guards the environment before it spends anything — ANTHROPIC_API_KEY unset,
# a working Claude token, the pinned image present — and that image locks the Python
# deps. What none of it can see is whether the seats themselves still load.
#
# Why this exists:
#   2026-08-13 13:12 — a colon in council-skeptic's description made its YAML
#   frontmatter invalid. Claude Code did not error. The seat silently fell back to a
#   general-purpose agent and the council ran a voice short (fixed in b2a9d19).
#
# That is the failure mode worth a script: a broken seat disappears instead of
# failing, so nothing downstream notices and the run looks fine.
#
# Runs anywhere the repo is checked out — the Mac, the server, or inside the run's
# container. Exit 0 = the council will assemble. Exit 1 = do not spend the tokens.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$REPO/.claude/agents"
CMD="$REPO/.claude/commands/debrief.md"

# The collector's venv is the only interpreter guaranteed to have PyYAML.
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

fail=0
ok(){  printf "  [ok]    %s\n" "$1"; }
bad(){ printf "  [FAIL]  %s\n" "$1"; fail=$((fail+1)); }

echo "=== preflight $(date '+%F %T %Z') ==="

# 1. Inputs. Without these the rest is meaningless, so stop here.
[ -d "$AGENTS" ] || bad "no agents directory at $AGENTS"
[ -f "$CMD" ]    || bad "no /debrief command at $CMD"
if [ -z "$PY" ] || ! "$PY" -c 'import yaml' 2>/dev/null; then
  bad "no python with PyYAML (tried $REPO/.venv/bin/python, then python3)"
fi
[ "$fail" -eq 0 ] || { echo "=== preflight FAILED ($fail) ==="; exit 1; }

# 2. Every agent file's frontmatter must parse and declare a name, or that seat
#    silently vanishes at runtime.
names=$("$PY" - "$AGENTS" <<'PY'
import sys, pathlib, yaml
bad, names = [], []
for p in sorted(pathlib.Path(sys.argv[1]).glob('*.md')):
    txt = p.read_text()
    if not txt.startswith('---'):
        bad.append(f"{p.name}: no frontmatter"); continue
    try:
        meta = yaml.safe_load(txt.split('---', 2)[1])
    except Exception as e:
        bad.append(f"{p.name}: {str(e).splitlines()[0]}"); continue
    if not isinstance(meta, dict) or not meta.get('name'):
        bad.append(f"{p.name}: no name field")
    else:
        names.append(meta['name'])
for b in bad:   print("BAD:" + b)
for n in names: print("NAME:" + n)
PY
)
if [ $? -ne 0 ]; then
  bad "could not read $AGENTS"
elif printf '%s\n' "$names" | grep -q '^BAD:'; then
  printf '%s\n' "$names" | grep '^BAD:' | sed 's/^BAD:/      /'
  bad "agent frontmatter failed to parse (that seat will NOT load)"
else
  ok "all $(printf '%s\n' "$names" | grep -c '^NAME:') agent files parse"
fi

# 3. Every seat /debrief names must be a registered agent. Catches a seat that was
#    renamed or removed while the command still calls for it.
missing=0
for want in $(grep -oE '`(council-[a-z-]+|summarizer)`' "$CMD" | tr -d '`' | sort -u); do
  printf '%s\n' "$names" | grep -qx "NAME:$want" \
    || { echo "      /debrief wants '$want' — not registered"; missing=$((missing+1)); }
done
[ "$missing" -eq 0 ] && ok "all seats referenced by /debrief resolve" \
                     || bad "$missing seat(s) referenced but not registered"

echo "=== preflight: $fail failed ==="
[ "$fail" -eq 0 ] || exit 1
exit 0
