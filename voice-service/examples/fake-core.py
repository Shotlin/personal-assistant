"""A stand-in for sani-core that speaks the same framed JSON on stdin/stdout, for offline tests (no model, no cost).
Scripted behaviour: a prompt containing "QUESTION:" is answered after 1.5 s (or never if it contains "NEVER ANSWER"); a prompt starting
with "VOICE HAND-OFF" reports progress and finishes after ~3 s. Every prompt received is appended to the file in FAKE_CORE_LOG."""
import json, os, sys, threading, time

out_lock = threading.Lock()
def send(obj):
    body = json.dumps(obj).encode()
    with out_lock:
        sys.stdout.buffer.write(len(body).to_bytes(4, "big") + body); sys.stdout.buffer.flush()

def event(run_id, kind, data):
    send({"type": "event", "run_id": run_id, "agent_id": "deep", "kind": kind, "data": data})

def run(req):
    p = req["params"]; text = p["text"]; rid = p["run_id"]
    if os.environ.get("FAKE_CORE_LOG"):
        with open(os.environ["FAKE_CORE_LOG"], "a") as f: f.write(json.dumps({"run_id": rid, "thread": p.get("thread_id"), "text": text}) + "\n")
    event(rid, "agent.started", {"text": text[:50]})
    if "QUESTION:" in text:
        if "NEVER ANSWER" in text: time.sleep(60); return
        time.sleep(1.5); ans = "A 502 means nginx got a bad answer from the app behind it: restart the app process and check its log for a crash. If it only happens on one route, that route timed out."
        for w in ans.split(" "): event(rid, "agent.token", {"text": w + " "})
        send({"type": "response", "id": req["id"], "ok": True, "result": {"status": "done", "response": ans}}); return
    event(rid, "agent.progress", {"message": "Using claude_code"}); time.sleep(1.5)
    event(rid, "agent.progress", {"message": "Using run_tests"}); time.sleep(1.5)
    ans = "Fixed the refund flow in the customer app and the dashboard, the UI files are untouched and the tests pass. Nothing was deployed."
    event(rid, "agent.token", {"text": ans})
    event(rid, "agent.completed", {"status": "done"})
    send({"type": "response", "id": req["id"], "ok": True, "result": {"status": "done", "response": ans}})

def main():
    inp = sys.stdin.buffer
    while True:
        head = inp.read(4)
        if len(head) < 4: return
        req = json.loads(inp.read(int.from_bytes(head, "big")))
        if req["method"] == "agents.list":
            send({"type": "response", "id": req["id"], "ok": True, "result": {"agents": [{"id": "deep"}, {"id": "velo"}], "protocol_version": 2}})
        elif req["method"] == "run.start":
            threading.Thread(target=run, args=(req,), daemon=True).start()
        elif req["method"] == "run.cancel":
            send({"type": "response", "id": req["id"], "ok": True, "result": {"status": "cancelling"}})
        else:
            send({"type": "response", "id": req["id"], "ok": False, "error": "unknown method"})
main()
