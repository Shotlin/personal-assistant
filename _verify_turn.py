"""Throwaway harness: one real agent turn through the sani-core sidecar.

Drives run.start exactly like the Tauri host and prints the event sequence, so
agent attribution and token streaming are observable. Delete after use.
"""

import json
import subprocess
import sys


def frame(payload):
    body = json.dumps(payload).encode()
    return len(body).to_bytes(4, "big") + body


def read_frame(stream):
    header = stream.read(4)
    if not header or len(header) < 4:
        return None
    length = int.from_bytes(header, "big")
    return json.loads(stream.read(length))


proc = subprocess.Popen(
    ["-m"] and [sys.executable, "-m", "assistant.core"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
)
proc.stdin.write(
    frame(
        {
            "type": "request",
            "id": "r1",
            "method": "run.start",
            "params": {
                "agent_id": sys.argv[1],
                "text": sys.argv[2],
                "run_id": "host-run-1",
                "thread_id": "verify-conversation",
            },
        }
    )
)
proc.stdin.flush()

tokens = []
kinds = []
while True:
    reply = read_frame(proc.stdout)
    if reply is None:
        print("SIDECAR CLOSED STDOUT WITHOUT A FINAL RESPONSE")
        break
    if reply.get("type") == "event":
        kinds.append(f"{reply['kind']}({reply.get('agent_id')})")
        if reply["kind"] == "agent.token":
            tokens.append(reply["data"].get("text", ""))
        continue
    if reply.get("type") == "response":
        print("EVENT SEQUENCE:", " ".join(kinds))
        print("STREAMED TEXT:", "".join(tokens)[:600])
        result = reply.get("result") or {}
        print("FINAL ok=", reply.get("ok"), "status=", result.get("status"))
        print("FINAL thread_id=", result.get("thread_id"))
        print("FINAL response==streamed:", (result.get("response") or "") == "".join(tokens))
        print("ERROR:", reply.get("error"))
        break
proc.stdin.close()
proc.wait(timeout=10)
