#!/bin/bash
# Post-deploy visibility check, not a gate -- run detached from the CI job
# (see .github/workflows/deploy.yml) so a slow cold-cache load never blocks
# or fails the pipeline. Checks in at fixed checkpoints and appends results
# to backend/health_check.log; never exits non-zero on its own.
URL="http://localhost:3006"
LOG="$(dirname "$0")/backend/health_check.log"

check() {
  resp=$(curl -s -m 10 "$URL/api/tickers" || echo "")
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -z "$resp" ]; then
    echo "[$ts +${1}s] no response from $URL yet" >> "$LOG"
    return
  fi
  total=$(echo "$resp" | jq -r '.tickers | length')
  universe_error=$(echo "$resp" | jq -r '.universe_error // empty')
  n_errors=$(echo "$resp" | jq -r '.errors | length')
  if [ -n "$universe_error" ]; then
    echo "[$ts +${1}s] universe_error reported: $universe_error" >> "$LOG"
  elif [ "$total" -gt 0 ]; then
    echo "[$ts +${1}s] OK: $total tickers loaded ($n_errors per-ticker errors)" >> "$LOG"
  else
    echo "[$ts +${1}s] total_tickers=0, still loading" >> "$LOG"
  fi
}

echo "--- deploy health check started $(date -u +%Y-%m-%dT%H:%M:%SZ) ---" >> "$LOG"

# Checkpoints: 30s, 1m, 2m, 5m, 10m. Sleep the gap between each rather than
# the absolute time, since curl itself takes a beat.
sleep 30;  check 30
sleep 30;  check 60
sleep 60;  check 120
sleep 180; check 300
sleep 300; check 600
