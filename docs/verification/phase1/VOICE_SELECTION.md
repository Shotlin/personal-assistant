# Phase 1 voice output selection (T09)

Status: **IMPLEMENTED-TRANSPORT / BLOCKED-SELECTION**. The worker, framed
protocol, supervisor, and playback queue are implemented and tested
offline; the ENGINE SELECTION and packaged voice are BLOCKED on
owner-authorized gates, exactly as file 04 T09 anticipated ("asset
acquisition requires permitted source/access terms, hardware capacity
check and owner audition"). No engine or weights were downloaded.

## What is built and tested (offline, fixture environment)

| Piece | Path | Evidence |
|---|---|---|
| Worker protocol + validation | `sani/src-tauri/python/sani_tts.py` | `tests/unit/test_sani_tts_worker.py` — 11 PASS: framing, malformed/oversize/NaN refusal, stale generation, cancel-during-synthesis, buffer bound (2 s), queue refusal at 3 utterances, shutdown, unspecified-engine refusal |
| Rust frame validation | `sani/src-tauri/src/tts_protocol.rs` | inline tests — 7 PASS: bounds, kind/rate validation, NaN refusal, decode, monotonic sequence, stale generation |
| Worker supervision | `sani/src-tauri/src/tts.rs` | inline tests — 2 PASS: enabled flag, cancel frame; scrubbed env (no provider keys) |
| Playback queue + speech.stop | `sani/src-tauri/src/tts_queue.rs` | inline tests — 6 PASS: enqueue/finish cycle, 3-utterance bound, stop bumps generation + clears queue, stale chunk refused, buffer flood blocked, sink failure → ERROR state |
| Build script | `sani/scripts/build-tts.sh` | pinned env via `sani/tts/pyproject.toml`; engine dependency intentionally empty until selection |

The default engine is `unspecified`: synthesis requests answer a clear
error ("audition pending"), text output is unaffected, and no cloud
fallback exists anywhere in the path.

## The audition plan (proceeds only with owner authorization)

1. **Asset acquisition authorization** (owner): the candidates below
   require downloading model weights and voice assets; access terms for
   gated assets must be accepted BY THE OWNER, not by an agent.
2. **Candidates** (at most two, from file 10's shortlist):
   - Pocket TTS (kyutai-labs) — first audition: documented local CPU
     streaming; runtime MIT-style, model weights CC-BY-4.0 gated, voice
     assets carry mixed licences (CC-BY / CC0 / noncommercial) — pick only
     an explicitly permitted voice.
   - Kokoro-82M (hexgrad) — fallback: Apache-2.0 model card; British male
     entries include `bm_george`, `bm_fable`; verify voice provenance.
3. **Measurement protocol**: same local corpus
   (`tests/fixtures/voice/audition.txt`), same volume/device/rate, isolated
   env per candidate, no provider keys, five cold starts + thirty warm
   segments; capture first-audio latency, RTF, worker peak RSS, process-tree
   CPU, cancellation during load/chunk/playback, offline network-denial
   monitoring, and output-device swap.
4. **Owner audition**: the owner rates intelligibility, pace, fatigue, and
   preferred tone on the corpus (includes "Sayan", "Ananya", "Bengaluru",
   "Sani", "Jev", "Deep Agent", "Obsidian", "SQLite", "₹1,25,000",
   "27 September 2026", file paths, acronyms, and 90-second technical
   prose). A British-male "Jarvis-style" voice is an option to audition,
   not a mandate and not a claim on any actor's voice.
5. **Pin & package**: record the selected engine version, lock hash, model
   and voice asset hashes, licences, and notices here and in
   `THIRD_PARTY_NOTICES.md`; only then does `sani/tts/pyproject.toml` gain
   the engine dependency and `SANI_TTS_ENGINE` change from `unspecified`.

## Acceptance status

| Gate | Status |
|---|---|
| Framed protocol + validation + cancellation + backpressure (offline) | PASS |
| One selected engine with reproducible lock/asset manifest | BLOCKED — owner asset authorization required |
| Offline cold/warm speech on this machine | BLOCKED — depends on engine |
| Measured packaging size / RSS / CPU / first audio / RTF | BLOCKED — depends on engine |
| Owner audition rating | BLOCKED — owner participation required |
| No cloud fallback | PASS by construction (no egress path exists) |
