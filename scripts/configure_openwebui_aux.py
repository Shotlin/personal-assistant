"""Disable Open WebUI auxiliary generation (token conservation, Phase 1.1).

Title auto-generation, tag generation, follow-up suggestions, and
autocomplete each fire hidden model requests per message through the
gateway. The owner directed maximum token conservation, so this script
turns them off via the admin API and reports the applied state. It is
idempotent; safe to re-run.

Usage:
    uv run python scripts/configure_openwebui_aux.py \
        --email <admin email> --password <admin password>
"""

from __future__ import annotations

import argparse
import sys

import httpx

BASE_URL = "http://127.0.0.1:3000"

# Admin-config keys (dot paths) that trigger hidden model calls.
AUX_FALSE_KEYS = [
    "interface.enable_title_generation",
    "interface.enable_tag_generation",
    "interface.enable_follow_up_generation",
    "interface.enable_autocomplete_generation",
    "interface.enable_set_as_default_generation",  # harmless if absent
]


def _get_path(obj: object, dotted: str) -> object:
    cur: object = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(obj: dict, dotted: str, value: object) -> None:
    parts = dotted.split(".")
    cur: dict = obj
    for part in parts[:-1]:
        node = cur.get(part)
        if not isinstance(node, dict):
            node = {}
            cur[part] = node
        cur = node
    cur[parts[-1]] = value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    with httpx.Client(timeout=30) as ui:
        signin = ui.post(
            f"{args.base_url}/api/v1/auths/signin",
            json={"email": args.email, "password": args.password},
        )
        if signin.status_code != 200:
            print(f"sign-in failed: {signin.status_code} {signin.text[:200]}")
            return 1
        auth = {"Authorization": f"Bearer {signin.json()['token']}"}

        current = ui.get(f"{args.base_url}/api/v1/admin/config", headers=auth)
        if current.status_code != 200:
            print(f"cannot read admin config: {current.status_code}")
            return 1
        config = current.json()

        print("before:")
        for key in AUX_FALSE_KEYS:
            print(f"  {key} = {_get_path(config, key)}")
        for key in AUX_FALSE_KEYS:
            _set_path(config, key, False)

        update = ui.post(
            f"{args.base_url}/api/v1/admin/config/update", headers=auth, json=config
        )
        if update.status_code != 200:
            print(f"update failed: {update.status_code} {update.text[:300]}")
            return 1

        verify = ui.get(f"{args.base_url}/api/v1/admin/config", headers=auth).json()
        print("after:")
        ok = True
        for key in AUX_FALSE_KEYS:
            value = _get_path(verify, key)
            print(f"{key} = {value}")
            if value not in (False, None):
                ok = False
        print("auxiliary generation disabled." if ok else "some keys not applied.")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
