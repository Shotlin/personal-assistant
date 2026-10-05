"""Reads the file Shubh wrote into a Sani memory DB the way the Deep Agent does (PolicyStoreBackend over SqliteStore).
Usage (repo root): uv run python voice-service/examples/mirror-check.py voice-service/var/test-memory/sani.db"""
import sys
from assistant.memory.local import SqliteStore
from assistant.memory.policy import PolicyStoreBackend

store = SqliteStore.open(sys.argv[1])
backend = PolicyStoreBackend(store=store, namespace=lambda _runtime: ("users", "sani-local", "assistant-memory"))
ls = backend.ls("/")
print("ls:", [e["path"] for e in (getattr(ls, "entries", None) or ls)])
r = backend.read("/voice-calls.md")
fd = getattr(r, "file_data", None)
print((fd or {}).get("content", r)[:500] if fd else r)
