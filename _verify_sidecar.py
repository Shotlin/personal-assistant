"""Throwaway harness: drive the sani-core sidecar exactly like the Tauri host does.

Speaks one framed request, prints the framed replies. Delete after use.
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
    sys.argv[1:],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
proc.stdin.write(frame({"type": "request", "id": "1", "method": "agents.list", "params": {}}))
proc.stdin.flush()
reply = read_frame(proc.stdout)
print("AGENTS.LIST REPLY:", json.dumps(reply, indent=2) if reply else "NONE")
proc.stdin.close()
err = proc.stderr.read().decode()[-1500:]
print("STDERR TAIL:", err)
proc.wait(timeout=10)
print("EXIT:", proc.returncode)
