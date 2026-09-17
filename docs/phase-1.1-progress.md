# Phase 1.1 implementation progress

## Current checkpoint

Branch: `phase-1.1-latency`. Last committed revision before this work:
`1977c01` (WP2 defaults/ContextVar regression fixes). WP3 work is uncommitted
and incomplete. No push or deployment was performed during this goal round.
The previously requested gateway process remains running; it has not reloaded
these subsequent source changes.

## Verified locally in this round

Commands from repository root (existing locked virtual environment):

```sh
.venv/bin/python -m pytest tests/unit/test_trusted_session_binding.py -o addopts='' -x --tb=long
.venv/bin/python -m pytest tests/unit tests/integration -o addopts='' --tb=long -q
.venv/bin/ruff check src tests
.venv/bin/mypy src
```

Results: 5 trusted-session tests passed; full suite 134 passed, 1 skipped,
1 Starlette deprecation warning, in 2.59 seconds. Ruff passed; mypy passed
for 38 source files. These are regression execution times, NOT task latency
or performance claims. Live provider test remains skipped.

New/strengthened checks cover lazy desktop lease acquisition (plain chat
must not take the lease), concurrent startup, rejection of a closed handle,
cleanup failure quarantine, forced trusted session binding, and a queued
mutation refused after Stop while an earlier in-flight effect is preserved.

## Remaining WP3 work / known limitations

- Wire DesktopSessionManager into both non-streaming execution and the
  streaming generator's own context; remove eager gateway cursor setup.
- Establish/reset budget, session, and artifact ContextVars in their owning
  execution scope. The current production path does not set cua_desktop_run.
- Provide controller-only normalized MCP results. Current `_caller` still
  drops its normalized structured outcome after constructing model text.
- Validate native controller result status rather than treating strings,
  malformed outcomes or transport acknowledgements as success.
- Review cursor-motion manifest addition before any invocation; leave its
  activation flag off until the manifest and installed schema are validated.
- Bound and test cleanup during cancellation/disconnect, adapter cleanup
  errors, shutdown and startup uncertainty. Current adapter error swallowing
  and close_all handling need further hardening; mock lease tests alone are
  not sufficient proof of production recovery.
- Cross-process durable desktop ownership belongs to WP4, not the current
  process-local lease.
- Complete package review and gates before a WP3 local commit.

## WP1/WP2 audit caveats

Existing groundwork is not full acceptance. Usage callbacks do not yet count
all native SDK retry attempts or reconcile actual cost. API usage can still
turn unknown values into zero. Structured evidence is normalized but then
lost at the wrapper boundary; hard latest-observation limits and per-window
context still need completion. Previous Calculator report timings are not a
measured gateway/Open WebUI baseline.

## Model and live testing

The user changed the local configuration to `stealth/union-alpha` via
OpenRouter and requested a restart. Do not silently rewrite that setting
back to GLM during autonomous continuation. Model-specific acceptance against
the original GLM plan remains unresolved; no provider request was made in
this round, and endpoint support/cost for the changed model is unverified.

No live desktop action, >30-second cursor wait, real Calculator display
readback, browser search, playback, paid smoke trial, or Open WebUI visible
acknowledgement benchmark was performed. Do not enable the unfinished local
runtime for routine desktop use based on these mocked tests.
