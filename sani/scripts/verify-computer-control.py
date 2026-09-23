"""Sani computer-control acceptance check against the packaged installed app.

Verifies, in order, the things the goal actually claims:

1. The embedded CUA driver's view of macOS's grants, read live from the daemon
   over Sani's private socket. This is the authoritative source: it is the same
   process that will move the pointer, and it answers from its own TCC identity.
2. That the driver sits in *Sani's* responsibility chain, so the grant the user
   gave to Sani is the grant macOS applies here.
3. That a pixel-addressed click moves the *physical* pointer to inside the
   target window. Measured with CoreGraphics from outside Sani. The accessibility
   click path deliberately never moves the cursor, so this is what distinguishes
   a real desktop action from a background AX write.
4. That typing lands in a place that can be read back independently.

TextEdit is used for 3 and 4 on purpose: it is a native Cocoa text view, so its
AXValue is genuine proof of what the field holds. A browser tab is not -- the
driver itself refuses to trust AXValue read-back there.

Read-only with respect to Sani: never writes settings, never fakes a result. A
missing permission reports BLOCKED and stops; an action that did not physically
happen reports FAIL.
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
TEXTEDIT = "com.apple.TextEdit"
PHRASE = "sani computer control check"

#: How far the pointer is parked before the click, in logical points.
PARK_OFFSET = 400


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


def cursor() -> tuple[float, float]:
    point = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return (float(point.x), float(point.y))


def park(where: tuple[float, float]) -> None:
    """Move the pointer out of the way. Setup only -- the driver must move it back."""
    Quartz.CGWarpMouseCursorPosition(Quartz.CGPoint(where[0], where[1]))


def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def fail(step: str, detail: str) -> int:
    print(f"FAIL  {step}: {detail}")
    return 1


def blocked(step: str, detail: str) -> int:
    print(f"BLOCKED  {step}: {detail}")
    return 2


def main() -> int:
    sani = subprocess.run(["pgrep", "-f", "Sani.app/Contents/MacOS/sani$"],
                          capture_output=True, text=True).stdout.split()
    if not sani:
        return fail("host", "Sani is not running -- launch /Applications/Sani.app first")
    sani_pid = sani[0]

    permissions = call("check_permissions", {"prompt": False})
    source = permissions.get("source", {})
    print(f"1. driver permission probe: accessibility={permissions.get('accessibility')} "
          f"screen_recording={permissions.get('screen_recording')}")
    print(f"2. identity: pid={source.get('pid')} embedded={source.get('embedded')} "
          f"host_bundle_id={source.get('host_bundle_id')!r} "
          f"responsible_ppid={source.get('responsible_ppid')} (Sani pid {sani_pid})")

    if not permissions.get("accessibility") or not permissions.get("screen_recording"):
        return blocked("permission",
                       "macOS has not granted Accessibility + Screen Recording to Sani. "
                       "Sani -> Computer Control -> Request Accessibility, then Request "
                       "Screen Recording, then quit and reopen Sani.")
    if str(source.get("responsible_ppid")) != sani_pid:
        return fail("chain", "the driver is not a child of the running Sani, so the grant "
                             "macOS applies here is not Sani's")

    launch = call("launch_app", {"bundle_id": TEXTEDIT})
    time.sleep(2.5)
    pid = launch.get("pid") or next(
        (a.get("pid") for a in call("list_apps", {}).get("apps", [])
         if a.get("bundle_id") == TEXTEDIT), None)
    if not pid:
        return fail("launch", f"TextEdit did not start: {json.dumps(launch)[:200]}")

    call("hotkey", {"pid": pid, "keys": ["command", "n"]})
    time.sleep(1.5)
    windows = [w for w in call("list_windows", {"pid": pid}).get("windows", [])
               if w.get("is_on_screen")]
    if not windows:
        return fail("window", "TextEdit has no on-screen document window")
    window = max(windows, key=lambda w: w["bounds"]["width"] * w["bounds"]["height"])
    wid = window["window_id"]
    bounds = window["bounds"]

    state = call("get_window_state", {"pid": pid, "window_id": wid,
                                      "include_screenshot": False,
                                      "max_elements": 200, "max_depth": 25})
    scale = state.get("screenshot_scale") or 1
    areas = [e for e in state.get("elements", [])
             if str(e.get("role", "")).endswith("TextArea") and (e.get("frame") or {}).get("w")]
    if not areas:
        return fail("target", "no text area in the TextEdit snapshot: "
                              + json.dumps(state)[:300])
    area = max(areas, key=lambda e: e["frame"]["w"] * e["frame"]["h"])
    frame = area["frame"]
    # `frame` is window-local points; the pixel click path wants window-local
    # screenshot pixels, which are scaled by screenshot_scale on Retina.
    aim_x = int((frame["x"] + frame["w"] / 2) * scale)
    aim_y = int((frame["y"] + frame["h"] / 2) * scale)
    print(f"3. aiming at text area local pts ({frame['x'] + frame['w'] / 2:.0f},"
          f"{frame['y'] + frame['h'] / 2:.0f}) scale={scale} -> px ({aim_x},{aim_y})")

    park((float(bounds["x"]) - PARK_OFFSET, float(bounds["y"]) + PARK_OFFSET))
    time.sleep(0.5)
    before = cursor()

    click = call("click", {"pid": pid, "window_id": wid, "x": aim_x, "y": aim_y})
    time.sleep(1.0)
    after = cursor()

    inside = (bounds["x"] - 2 <= after[0] <= bounds["x"] + bounds["width"] + 2
              and bounds["y"] - 2 <= after[1] <= bounds["y"] + bounds["height"] + 2)
    print(f"4. physical pointer: parked {before[0]:.0f},{before[1]:.0f} -> "
          f"{after[0]:.0f},{after[1]:.0f} moved {distance(before, after):.0f}pt; "
          f"inside target window={inside}")
    print(f"   click result: {json.dumps(click)[:220]}")
    if not inside or distance(before, after) < 50:
        return fail("pointer", "the real cursor did not travel to the aimed window -- "
                              "actions are not being delivered to the physical desktop")

    typed = call("type_text", {"pid": pid, "window_id": wid, "text": PHRASE,
                               "delivery_mode": "foreground"})
    time.sleep(1.5)
    after_state = call("get_window_state", {"pid": pid, "window_id": wid,
                                            "include_screenshot": False,
                                            "max_elements": 200, "max_depth": 25})
    values = [str(e.get("value", "")) for e in after_state.get("elements", [])]
    landed = any(PHRASE in value for value in values)
    print(f"5. typing: {json.dumps(typed)[:220]}")
    print(f"   read back from the document: found={landed}")
    if not landed:
        return fail("typing", f"{PHRASE!r} never appeared in TextEdit's accessibility value")

    print("PASS  grants real and attributed to Sani; the physical pointer travelled to "
          "the aimed window; typing read back from the document")
    return 0


if __name__ == "__main__":
    sys.exit(main())
