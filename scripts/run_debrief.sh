#!/bin/bash
# Autonomous daily debrief — invoked by the launchd LaunchAgent (see scripts/*.plist).
# Runs `/debrief` headless on the Claude Max subscription, then logs the result.
#
# Why a wrapper instead of calling claude directly from the plist:
#   - launchd runs with a minimal PATH; the pipeline needs node/npm/wrangler (publish),
#     claude, and homebrew on PATH. We set it explicitly here.
#   - Per-run dated log files (launchd's plist paths can't expand $(date)).
#   - Enforces the project's hard rule: never run against the paid API.
set -u

REPO="/Users/alex/Documents/debrief"
CLAUDE="/Users/alex/.local/bin/claude"

# launchd gives almost no PATH — add everything the run touches:
#   ~/.local/bin (claude) · ~/.npm-global/bin (wrangler) · /usr/local/bin (node) · homebrew · system
export PATH="/Users/alex/.local/bin:/Users/alex/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$REPO/logs"
STAMP="$(date '+%Y-%m-%d')"
LOG="$REPO/logs/debrief-$STAMP.log"

{
  echo "=================================================================="
  echo "=== debrief run started $(date '+%F %T %Z') ==="

  # Hard rule (docs/PLAN.md §2): the pipeline must bill to the Max subscription, never the API.
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "ABORT: ANTHROPIC_API_KEY is set — refusing to run (must use the Max subscription)."
    exit 1
  fi

  if [ ! -x "$CLAUDE" ]; then
    echo "ABORT: claude CLI not found/executable at $CLAUDE"
    exit 1
  fi

  cd "$REPO" || { echo "ABORT: cannot cd to $REPO"; exit 1; }

  # Headless run. --dangerously-skip-permissions so the unattended run never hangs on a
  # tool prompt (trusted local automation of our own command). Step 6 of /debrief also
  # rebuilds + publishes the site, so a successful run auto-updates alexwoodka.com/debrief.
  "$CLAUDE" -p "/debrief" --dangerously-skip-permissions
  code=$?

  echo "=== debrief run finished $(date '+%F %T %Z') — claude exit $code ==="
  exit $code
} >> "$LOG" 2>&1
