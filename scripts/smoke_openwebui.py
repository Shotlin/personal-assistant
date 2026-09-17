"""Smoke checks for the Phase 1 Open WebUI deployment (Task 7).

Verifies, without spending model tokens:
1. the native agent gateway is healthy and lists exactly one model,
2. the Open WebUI container answers on 127.0.0.1:3000,
3. gateway auth behaves (401 without key),
4. an end-to-end gateway call works with dev test headers.

Header-lineage verification (X-OpenWebUI-* arriving at the gateway) happens
in a real UI chat: send one message in Open WebUI, then check the gateway
run logs for the identity block and lineage headers.

Usage: uv run python scripts/smoke_openwebui.py [--skip-ui]
"""

from __future__ import annotations

import argparse
import sys

import httpx

from assistant.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check the Open WebUI deployment")
    parser.add_argument("--skip-ui", action="store_true", help="do not probe the Open WebUI port")
    args = parser.parse_args()

    settings = Settings()
    failures: list[str] = []
    key = settings.agent_gateway_api_key

    with httpx.Client(timeout=10) as client:
        # 1. Gateway health + model listing (exactly what Open WebUI calls).
        health = client.get(f"http://{settings.app_host}:{settings.app_port}/healthz")
        if health.status_code == 200 and health.json().get("status") == "ok":
            print("[ok] gateway /healthz")
        else:
            failures.append(f"gateway /healthz -> {health.status_code}")

        auth = {"Authorization": f"Bearer {key}"}
        models = client.get(
            f"http://{settings.app_host}:{settings.app_port}/v1/models", headers=auth
        )
        if models.status_code == 200:
            data = models.json().get("data", [])
            ids = [m.get("id") for m in data]
            if ids == [settings.assistant_model_id]:
                print(f"[ok] /v1/models lists exactly {ids}")
            else:
                failures.append(f"unexpected model list: {ids}")
        else:
            failures.append(f"/v1/models -> {models.status_code}")

        # 2. Auth must reject missing/invalid keys.
        no_auth = client.get(f"http://{settings.app_host}:{settings.app_port}/v1/models")
        if no_auth.status_code == 401:
            print("[ok] /v1/models rejects missing key with 401")
        else:
            failures.append(f"missing key returned {no_auth.status_code}, expected 401")

        # 3. Gateway end-to-end with dev test headers (no CUA, scripted env).
        dev = client.post(
            f"http://{settings.app_host}:{settings.app_port}/v1/chat/completions",
            headers={
                **auth,
                "X-Assistant-Dev-User-Id": "smoke-user",
                "X-Assistant-Dev-Chat-Id": "smoke-chat",
            },
            json={
                "model": settings.assistant_model_id,
                "messages": [{"role": "user", "content": "smoke test"}],
            },
        )
        if dev.status_code == 200:
            print("[ok] dev-header chat completion round trip")
        else:
            failures.append(f"dev chat completion -> {dev.status_code}: {dev.text[:200]}")

        # 4. Open WebUI UI reachable on loopback.
        if not args.skip_ui:
            try:
                ui = client.get("http://127.0.0.1:3000/")
                if ui.status_code == 200:
                    print("[ok] Open WebUI reachable at http://127.0.0.1:3000")
                else:
                    failures.append(f"Open WebUI -> {ui.status_code}")
            except httpx.HTTPError as exc:
                failures.append(f"Open WebUI unreachable (docker compose up -d?): {exc}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nSmoke passed. For header-lineage verification, send one chat in the")
    print("Open WebUI UI and confirm X-OpenWebUI-* headers in the gateway run logs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
