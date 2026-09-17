"""Configure the pinned Open WebUI's gateway connection with lineage headers.

Open WebUI v0.11.3 stores per-connection custom headers in its database
(the `OPENAI_API_CUSTOM_HEADERS` environment variable does not exist in
this version). This script applies the Phase 1 lineage scheme:

    X-OpenWebUI-Chat-Id                = {{CHAT_ID}}
    X-OpenWebUI-Message-Id             = {{MESSAGE_ID}}
    X-OpenWebUI-User-Message-Id        = {{USER_MESSAGE_ID}}
    X-OpenWebUI-User-Message-Parent-Id = {{USER_MESSAGE_PARENT_ID}}
    X-OpenWebUI-Task                   = {{TASK}}

Usage (admin credentials are used only for this API call, never stored):

    uv run python scripts/configure_openwebui_connection.py \\
        --email <admin email> --password <admin password>
"""

from __future__ import annotations

import argparse
import sys

import httpx

BASE_URL = "http://127.0.0.1:3000"
GATEWAY_CONNECTION_URL = "http://host.docker.internal:8787/v1"

LINEAGE_HEADERS = {
    "X-OpenWebUI-Chat-Id": "{{CHAT_ID}}",
    "X-OpenWebUI-Message-Id": "{{MESSAGE_ID}}",
    "X-OpenWebUI-User-Message-Id": "{{USER_MESSAGE_ID}}",
    "X-OpenWebUI-User-Message-Parent-Id": "{{USER_MESSAGE_PARENT_ID}}",
    "X-OpenWebUI-Task": "{{TASK}}",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--base-url", default=BASE_URL, help="Open WebUI base URL")
    parser.add_argument(
        "--gateway-url",
        default=GATEWAY_CONNECTION_URL,
        help="gateway URL the connection should point at",
    )
    args = parser.parse_args()

    with httpx.Client(timeout=30) as ui:
        signin = ui.post(
            f"{args.base_url}/api/v1/auths/signin",
            json={"email": args.email, "password": args.password},
        )
        if signin.status_code != 200:
            print(f"sign-in failed: {signin.status_code}")
            return 1
        auth = {"Authorization": f"Bearer {signin.json()['token']}"}

        current = ui.get(f"{args.base_url}/openai/config", headers=auth)
        if current.status_code != 200:
            print(f"cannot read connection config: {current.status_code}")
            return 1
        current_body = current.json()
        base_urls = current_body.get("OPENAI_API_BASE_URLS") or [args.gateway_url]
        keys = current_body.get("OPENAI_API_KEYS") or [""]

        configs = current_body.get("OPENAI_API_CONFIGS") or {}
        entry = dict(configs.get("0") or {})
        entry["headers"] = LINEAGE_HEADERS
        # Ensure the connection points at our gateway.
        try:
            index = base_urls.index(args.gateway_url)
        except ValueError:
            index = 0
        configs[str(index)] = entry

        update = ui.post(
            f"{args.base_url}/openai/config/update",
            headers=auth,
            json={
                "ENABLE_OPENAI_API": current_body.get("ENABLE_OPENAI_API", True),
                "OPENAI_API_BASE_URLS": base_urls,
                "OPENAI_API_KEYS": keys,
                "OPENAI_API_CONFIGS": configs,
            },
        )
        if update.status_code != 200:
            print(f"config update failed: {update.status_code} {update.text[:200]}")
            return 1

        verify = ui.get(f"{args.base_url}/openai/config", headers=auth).json()
        applied = (verify.get("OPENAI_API_CONFIGS") or {}).get(str(index), {}).get("headers")
        if applied == LINEAGE_HEADERS:
            print("lineage headers applied to the gateway connection.")
            print("Verify by sending one chat in the UI; the gateway run log")
            print("should show chat_id + user_message_id from X-OpenWebUI-* headers.")
            return 0
        print(f"unexpected config after update: {applied}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
