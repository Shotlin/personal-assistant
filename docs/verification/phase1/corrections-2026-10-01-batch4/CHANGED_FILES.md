# Changed implementation areas

- Provider admission: `src/assistant/models/openrouter.py`, `src/assistant/models/admission.py`, provider/model tests.
- Mission durability and privacy: `src/assistant/missions/store.py`, `service.py`, `evidence.py`, `executor.py`, contracts and integration tests.
- Desktop safety: `src/assistant/runtime/desktop_queue.py`, core IPC and queue/service tests.
- TTS packaging/playback: `sani/scripts/build-tts.sh`, `sani/src-tauri/src/tts.rs`, `tts_queue.rs`, resource configuration and worker tests.
- Approval UI: `sani/src/lib/tauri.ts`, `sani/src/components/MissionStatus.tsx`, `MainConversation.tsx` and approval-flow tests.
- Acceptance harness: `tests/e2e/_live.py`, live suite files, offline-harness tests, and `scripts/verify_phase1.py`.

For the full machine-readable source inventory, run the command in `CUMULATIVE_PATCH.md` against `SOURCE_BINDING.json`; it covers tracked and non-ignored untracked implementation files through the verifier's source-manifest policy.
