#!/usr/bin/env bash
# Prove the gateway contract Sani depends on, using Sani's exact request shape:
#   A) chat SSE carries an id (chatcmpl-<run_id>) and closes with data: [DONE]
#   B) the activity SSE keeps emitting past run.started and ends on a terminal event
#   C) re-delivering the same X-Assistant-Message-Id is deduped, not re-run
#   D) POST /v1/runs/{id}/stop drives the run to a cancelled terminal event
# The gateway key is read from Sani's private store and never printed.
set -uo pipefail

K=$(cat "$HOME/Library/Application Support/app.sani.local/agent_key")
BASE=http://127.0.0.1:8787
OUT=/tmp/sani-contract
mkdir -p "$OUT"
MID="contract-$(date +%s)-$RANDOM"

chat() { # $1=message id  $2=text  $3=outfile
  curl -sN --max-time 240 -X POST "$BASE/v1/chat/completions" \
    -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
    -H "X-Assistant-User-Id: local-user" -H "X-Assistant-Chat-Id: contract-probe" \
    -H "X-Assistant-Message-Id: $1" \
    -d "{\"model\":\"personal-assistant-v1\",\"messages\":[{\"role\":\"user\",\"content\":$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$2")}],\"stream\":true}" \
    > "$3" 2>"$3.err"
}

echo "=== A/B: stream one turn and watch activity in parallel"
ACT_PID=""
chat "$MID" "Answer in one short sentence: what is 12 times 9?" "$OUT/chat1.txt" &
CHAT_PID=$!

RUN_ID=""
for _ in $(seq 1 60); do
  RUN_ID=$(grep -ao 'chatcmpl-[A-Za-z0-9_-]*' "$OUT/chat1.txt" 2>/dev/null | head -1 | sed 's/^chatcmpl-//')
  [ -n "$RUN_ID" ] && break
  sleep 0.5
done
echo "run_id=${RUN_ID:-<none>}"
if [ -n "$RUN_ID" ]; then
  curl -sN --max-time 200 -H "Authorization: Bearer $K" \
    "$BASE/v1/runs/$RUN_ID/events" > "$OUT/activity1.txt" 2>&1 &
  ACT_PID=$!
fi
wait $CHAT_PID
echo "chat exit=$?"
sleep 6
kill $ACT_PID 2>/dev/null

echo "-- chat1: data lines=$(grep -c '^data: ' "$OUT/chat1.txt"), DONE=$(grep -c '^\[DONE\]$' "$OUT/chat1.txt" || grep -c 'data: \[DONE\]' "$OUT/chat1.txt")"
grep -o 'data: \[DONE\]' "$OUT/chat1.txt" | head -1
echo "-- activity1 events (in order):"
grep -a '^event: ' "$OUT/activity1.txt" | sed 's/^event: /   /'
echo "-- activity1 labels:"
grep -ao '"label":"[^"]*"' "$OUT/activity1.txt" | sed 's/.*"label":"//;s/"$//' | sort | uniq -c

echo
echo "=== C: re-deliver the SAME message id (must dedup, not re-run)"
chat "$MID" "Answer in one short sentence: what is 12 times 9?" "$OUT/chat2.txt"
echo "-- chat2 data lines=$(grep -c '^data: ' "$OUT/chat2.txt")"
python3 - "$OUT/chat1.txt" "$OUT/chat2.txt" <<'PY'
import json, sys
def text(path):
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        for ch in obj.get("choices") or []:
            c = (ch.get("delta") or {}).get("content") or ""
            out.append(c)
    return "".join(out)
a, b = text(sys.argv[1]), text(sys.argv[2])
print(f"   answer1={a[:70]!r}")
print(f"   answer2={b[:70]!r}")
print(f"   duplicate delivered the same final text: {a.strip() == b.strip() and bool(a.strip())}")
PY
echo "-- gateway log for the dedup:"
grep -a "run_duplicate_rejected" /Users/sayan/Documents/personal-assistant/var/gateway.log | tail -2 | python3 -c 'import sys,json
for l in sys.stdin:
    try:
        o=json.loads(l)
        print("  ", o.get("event"), o.get("claim_status"), o.get("reason"))
    except Exception: pass'

echo
echo "=== D: stop a live run"
MID2="contract-stop-$(date +%s)-$RANDOM"
chat "$MID2" "List the ten largest cities in the world with their populations, one per line, and explain each briefly." "$OUT/chat3.txt" &
P3=$!
RUN2=""
for _ in $(seq 1 80); do
  RUN2=$(grep -ao 'chatcmpl-[A-Za-z0-9_-]*' "$OUT/chat3.txt" 2>/dev/null | head -1 | sed 's/^chatcmpl-//')
  [ -n "$RUN2" ] && break
  sleep 0.25
done
echo "run2=${RUN2:-<none>}"
if [ -n "$RUN2" ]; then
  curl -sN --max-time 60 -H "Authorization: Bearer $K" "$BASE/v1/runs/$RUN2/events" > "$OUT/activity3.txt" 2>&1 &
  A3=$!
  sleep 2
  code=$(curl -s -o "$OUT/stop.json" -w '%{http_code}' -X POST -H "Authorization: Bearer $K" "$BASE/v1/runs/$RUN2/stop")
  echo "stop http=$code body=$(head -c 160 "$OUT/stop.json")"
  wait $P3
  sleep 4
  kill $A3 2>/dev/null
  echo "-- activity3 events:"
  grep -a '^event: ' "$OUT/activity3.txt" | sed 's/^event: /   /'
fi
echo "-- chat3 DONE present: $(grep -c 'data: \[DONE\]' "$OUT/chat3.txt")"
echo
echo "files in $OUT"
