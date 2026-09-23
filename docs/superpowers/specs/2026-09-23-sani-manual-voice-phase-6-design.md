# Sani Phase 6 — Manual Voice Submission Design

## Outcome

Only an explicit Finish & Send invokes `flush` and can create one user turn/agent run. Silence, VAD endpointing, Moonshine line completion, and partials never submit.

## Architecture

The existing sidecar remains the transcript accumulator. It accumulates stable segments and partial text indefinitely within a bounded safety budget. Native `app_state` owns listening/finalizing/working transitions: it keeps audio open across pauses, calls `flush` only for Finish/hotkey-while-listening, and calls `discard` for Escape/cancel.

## Experience and safety

Main and pill show the accumulated draft while listening. Finish & Send stops capture and awaits one final; Cancel discards and creates zero runs. At a resource threshold capture pauses safely while preserving text and asks the user to finish or cancel. Duplicate/late final events are dropped with generation protection.

## Tests

2/5/15-second pauses, line completion, and VAD possible-end yield zero runs; continued speech accumulates; Finish creates exactly one final/run; Esc creates none; hotkey and pill/main flows match.
