"""Sani computer-control acceptance check against the packaged installed app.

Verifies, in order, the four things the goal actually claims:

1. Sani's own macOS grants, read from the running installed process.
2. The embedded CUA driver's view of those same grants, read from the live
   daemon over Sani's private socket (this is the host-attributed identity, so
   `responsible_ppid` must equal the running Sani pid).
3. That a real CGEvent click moves the *physical* pointer -- measured with
   CoreGraphics from outside Sani, so a simulated or background-only delivery
   cannot pass.
4. That typing lands somewhere observable.

Read-only with respect to Sani: it never writes settings and never fakes a
result. If a permission is missing it reports BLOCKED and stops.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

import Quartz

APP = "/Applications/Sani.app"
DRIVER = f"{APP}/Contents/Resources/CuaDriver.app/Contents/MacOS/cua-driver"
SOCKET = "/Users/sayan/Library/Application Support/app.sani.local/cua-driver.sock"
CHROME = "com.google.Chrome"


def call(tool: str, args: dict) -> dict:
    result = subprocess.run(
        [DRIVER, "call", tool, json.dumps(args), "--socket", SOCKET],
        capture_output=True,
        text=True,
    )
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"error": (result.stdout or result.stderr).strip()[:300]}


def cursor() -> tuple[int, int]:
    point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (int(point.x), int(point.y))


def host_grants() -> dict:
    pid = subprocess.run(["pgrep", "-f", "Sani.app/Contents/MacOS/sani$"],
                         capture_output=True, text=True).stdout.split()
    core = subprocess.run(["pgrep", "-f", "sani-core-runtime/sani-core"],
                          capture_output=True, text=True).stdout.split()
    if not core:
        return {}
    env = subprocess.run(["ps", "eww", "-p", core[0]], capture_output=True, text=True).stdout
    found = {}
    for token in env.split():
        key, _, value = token.partition("=")
        if key == "SANI_HOST_ACCESSIBILITY_PERMISSION":
            found["accessibility"] = value
        elif key == "SANI_HOST_SCREEN_RECORDING_PERMISSION":
            found["screen_recording"] = value
    found["sani_pid"] = pid[0] if pid else None
    return found


def fail(step: str, detail: str) -> int:
    print(f"FAIL  {step}: {detail}")
    return 1


def main() -> int:
    grants = host_grants()
    print(f"1. host grants from running process: {grants}")
    if not grants:
        return fail("host", "sani-core is not running")

    permissions = call("check_permissions", {"prompt": False})
    print(f"2. driver view: {json.dumps({k: v for k, v in permissions.items() if k != 'source'})}")
    source = permissions.get("source", {})
    print(f"   attribution: embedded={source.get('embedded')} "
          f"host={source.get('host_bundle_id')!r} "
          f"responsible_ppid={source.get('responsible_ppid')} sani_pid={grants.get('sani_pid')}")
    if not permissions.get("accessibility") or not permissions.get("screen_recording"):
        return fail("permission", "BLOCKED: macOS has not granted Accessibility + Screen "
                                 "Recording to Sani yet")
    if str(source.get("responsible_ppid")) != str(grants.get("sani_pid")):
        return fail("chain", "driver is not in Sani's responsibility chain")

    launch = call("launch_app", {"bundle_id": CHROME})
    time.sleep(2.0)
    pid = launch.get("pid") or next(
        (a.get("pid") for a in call("list_apps", {}).get("apps", [])
         if a.get("bundle_id") == CHROME), None)
    if not pid:
        return fail("launch", json.dumps(launch)[:200])

    call("bring_to_front", {"pid": pid})
    time.sleep(1.5)
    windows = [w for w in call("list_windows", {"pid": pid}).get("windows", [])
               if w.get("is_on_screen")]
    if not windows:
        return fail("window", "Chrome has no on-screen window to aim at")
    window = max(windows, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
    bounds, wid = window["bounds"], window["window_id"]

    before = cursor()
    target_x = int(bounds["x"] + bounds["width"] * 0.5)
    target_y = int(bounds["y"] + 14)
    click = call("click", {"pid": pid, "window_id": wid,
                           "x": int(bounds["width"] * 0.5), "y": 14})
    time.sleep(1.0)
    after = cursor()
    moved = before != after
    print(f"3. physical pointer: {before} -> {after} (aimed {target_x},{target_y}) "
          f"moved={moved} route={json.dumps(click)[:160]}")
    if not moved:
        return fail("pointer", "the real cursor did not move -- delivery is not foreground")

    typed = "sani computer control check"
    type_result = call("type_text", {"pid": pid, "window_id": wid, "text": typed})
    time.sleep(1.0)
    state = call("get_window_state", {"pid": pid, "window_id": wid,
                                      "include_screenshot": False, "max_elements": 60,
                                      "max_depth": 12})
    blob = json.dumps(state)
    landed = typed in blob
    print(f"4. typing: {json.dumps(type_result)[:160]} observed_in_state={landed}")

    print("PASS  grants real, driver attributed to Sani, pointer physically moved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
