#!/usr/bin/env bash
# Close the loop on the INSTALLED app: speak a real sentence out loud (rendered
# by the macOS synthesiser and played through the speakers into the microphone)
# and watch it travel voice -> STT -> agent -> streamed answer.
#
# Prerequisite: macOS has granted Sani microphone access (click "Grant Access"
# on the pill, then Allow). Nothing here bypasses TCC; it only plays sound.
#
# Usage: ./scripts/probe-voice-loop.sh ["sentence"] [wait_seconds]
set -uo pipefail

LOG="$HOME/Library/Logs/app.sani.local/sani.log"
SENTENCE="${1:-What is seven times eight?}"
WAIT="${2:-75}"
SHOT_DIR="${SANI_SNAPSHOT_DIR:-/tmp/sani-shots}"

[ -f "$LOG" ] || { echo "no log at $LOG — is /Applications/Sani.app running?" >&2; exit 1; }

say -v Samantha -o /tmp/sani-loop.aiff "$SENTENCE"
afconvert -f WAVE -d LEI16 -c 1 /tmp/sani-loop.aiff /tmp/sani-loop.wav && rm -f /tmp/sani-loop.aiff

mark=$(wc -l < "$LOG")
echo "==> saying out loud: $SENTENCE"
echo "==> waiting up to ${WAIT}s for [state] listening (press Alt+Space, or the pill mic, if prompted)"

wait_for() { # $1=grep pattern  $2=timeout seconds
  local end=$((SECONDS + $2))
  while [ $SECONDS -lt $end ]; do
    if tail -n +"$((mark + 1))" "$LOG" | grep -aq "$1"; then return 0; fi
    sleep 0.4
  done
  return 1
}

if ! wait_for "\[state\] listening" "$WAIT"; then
  echo "!! never reached Listening. Recent log:" >&2
  tail -n 12 "$LOG" | sed 's/^/   /' >&2
  exit 1
fi

# Play it twice: the first pass primes the endpointer, the second is the take.
afplay /tmp/sani-loop.wav; sleep 1.2; afplay /tmp/sani-loop.wav
echo "==> audio played; waiting for the transcript and the agent turn"

wait_for "\[stt final\]" 40 || echo "!! no final transcript (was anything audible?)"
wait_for "begin turn:" 20 || echo "!! no agent turn was started"
wait_for "turn finished:" 180 || echo "!! the agent never finished"

echo
echo "==> this run, from the installed app's own log:"
tail -n +"$((mark + 1))" "$LOG" \
  | grep -a "\[state\]\|\[stt partial\]\|\[stt final\]\|begin turn\|activity\|turn finished\|stale\|cancel\|\[mic\]\|ERROR" \
  | tail -30 | sed 's/^/   /'
echo
echo "==> panel screenshot of the answer: $SHOT_DIR/panel-idle.png (if SANI_SNAPSHOT_DIR was set)"
