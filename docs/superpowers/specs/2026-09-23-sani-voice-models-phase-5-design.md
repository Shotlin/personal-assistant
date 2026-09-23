# Sani Phase 5 — Voice Model Manager Design

## Outcome

The Voice page manages exactly Moonshine `tiny-streaming-en`, `base-streaming-en`, `small-streaming-en`, and `medium-streaming-en`. Small remains the missing-setting default.

## Architecture

One native STT model catalog is derived from the sidecar-supported catalog and returned with cache/install state; React renders it but does not define models. Install status and progress come only from the actual Moonshine cache/downloader. Model selection persists through native settings and restarts only the STT sidecar.

## Transactions

Switch first records the prior ready model, starts the selected installed model, waits for its real `ready` event, and then persists/marks active. Failure restarts the prior known-working sidecar and reports a friendly error. Install reports only provider signals actually received. Remove requires explicit action, refuses the active model, and deletes only distinct Sani/Moonshine-owned cache files.

## Tests

Exact catalog/default/cache detection; install signal fidelity; each model’s switch/restart/persistence path; failed switch rollback; active-model remove refusal; and microphone operation after switching.
