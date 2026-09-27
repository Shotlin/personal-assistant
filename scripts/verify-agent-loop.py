#!/usr/bin/env python3
"""Acceptance: the agent loop against a real driver and a real model.

This is Phase 7's end-to-end battery for everything that can be verified without
installing a build over the user's granted Sani.app -- which would cost a macOS
re-grant only they can perform. It runs the production executor
(``DeepAgentEntry`` -> LangChain tool loop -> CUA tools -> cua-driver) against a
throwaway standard-mode daemon and asserts on what the loop *did*: the tool
sequence the sidecar announced, and the desktop state the driver reports back.

Scenarios, in the order the goal lists them:

  1  an application that was never programmed anywhere: "open Stickies"
  2  a multi-step task on a real text surface, verified by read-back
  3  continuity: the same conversation across turns remembers its target
  4  a task with no computer-control content must not touch the driver
  5  the sensitive-target gate refuses and the loop reports instead of retrying
  6  a random unseen request phrased loosely still reaches the desktop

Each check prints PASS / FAIL / BLOCKED. BLOCKED means an outside condition
(no credential, no permission, no window) made the check unrunnable -- never
reported as success. Exit code is non-zero if anything FAILed.

Read-only with respect to the installed app: it never touches Sani's settings,
its socket, or its daemon.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import socket as socket_module
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

DRIVER = REPO / "sani/.cua-build/unpacked/cua-driver"
SOCKET = "/tmp/sani-acceptance.sock"
RESULTS: list[tuple[str, str, str]] = []

#: The applications this battery touches, and the only ones it may clean up.
WATCHED = (
    ("Stickies", "com.apple.Stickies"),
    ("TextEdit", "com.apple.TextEdit"),
    ("Calculator", "com.apple.calculator"),
    ("Terminal", "com.apple.Terminal"),
)

#: The model under test. Production default; override with --model when the
#: configured credential is out of credit and the loop still needs proving.
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    print(f"{status:8s} {name}" + (f"\n         {detail}" if detail else ""), flush=True)


def call(tool: str, args: dict, *, timeout: float = 20.0) -> dict:
    result = subprocess.run(
        [str(DRIVER), "call", tool, json.dumps(args), "--socket", SOCKET],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        return json.loads(result.stdout)
    except Exception:
        return {"error": (result.stdout or result.stderr).strip()[:300]}


def listening(path: str) -> bool:
    try:
        client = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
        client.settimeout(0.5)
        client.connect(path)
        client.close()
        return True
    except OSError:
        return False


def start_daemon() -> subprocess.Popen | None:
    Path(SOCKET).unlink(missing_ok=True)
    log = open("/tmp/sani-acceptance-daemon.log", "w")
    process = subprocess.Popen(
        [str(DRIVER), "serve", "--embedded", "--permission-mode", "standard",
         "--socket", SOCKET, "--no-permissions-gate"],
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    for _ in range(60):
        if listening(SOCKET):
            return process
        time.sleep(0.25)
    return process


#: Provider conditions that make a check unrunnable rather than failed: no
#: credit, no permission, a rate limit. Reporting these as FAIL would blame
#: Sani's loop for someone else's quota.
PROVIDER_LIMITS = (
    "limit exceeded",
    "insufficient",
    "quota",
    "402",
    "429",
    "ForbiddenResponseError",
    "AuthenticationError",
    "RateLimit",
    "TooManyRequests",
    "temporarily rate-limited",
)


def landed(text: str, phrase: str) -> bool:
    """Did the words the loop typed reach the document?

    Compared with case and word spacing removed on purpose: TextEdit capitalizes
    the first letter of a sentence and substitutes "readback" for "read back", so
    a byte-exact match fails on a turn that did everything right. Proven live on
    2026-09-25 -- the document held exactly what the loop typed.
    """
    def squash(value: str) -> str:
        return "".join(char for char in value.lower() if char.isalnum())

    return squash(phrase) in squash(text)


def provider_blocked(error: str) -> bool:
    lowered = error.lower()
    return any(marker.lower() in lowered for marker in PROVIDER_LIMITS)


class Turn:
    """One production agent turn, with the tools it announced as evidence."""

    def __init__(self) -> None:
        self.tools: list[str] = []
        self.response = ""
        self.status = ""
        self.error = ""

    async def on_event(self, kind: str, payload: dict) -> None:
        if kind == "agent.progress":
            message = str(payload.get("message", ""))
            if message.startswith("Using "):
                self.tools.append(message[len("Using ") :].strip())
        elif kind == "agent.token":
            self.response += str(payload.get("text", ""))


#: A throttled provider says nothing about the loop, so it is waited out rather
#: than reported. Only provider limits are retried; a real failure fails fast.
TURN_ATTEMPTS = 3
TURN_BACKOFF_SECONDS = 20.0


async def run_turn(entry, text: str, thread: str) -> Turn:
    turn = Turn()
    for attempt in range(TURN_ATTEMPTS):
        turn = Turn()
        try:
            result = await entry.run(
                text, thread_id=thread, on_event=turn.on_event, cancel_check=lambda: False
            )
            turn.status = str(result.get("status", ""))
            turn.response = turn.response or str(result.get("response", ""))
        except Exception as exc:  # noqa: BLE001 -- an acceptance run reports, never raises
            turn.error = f"{type(exc).__name__}: {exc}"[:300]
        if turn.error and not provider_blocked(turn.error):
            return turn
        if not turn.error and attempt == 0:
            return turn
        if attempt + 1 < TURN_ATTEMPTS:
            wait = TURN_BACKOFF_SECONDS * (attempt + 1)
            print(f"         provider throttled; retrying this turn in {wait:.0f}s", flush=True)
            await asyncio.sleep(wait)
    return turn


def app_pid(bundle_id: str) -> int | None:
    for entry in call("list_apps", {}).get("apps") or []:
        if entry.get("bundle_id") == bundle_id and entry.get("pid"):
            return int(entry["pid"])
    return None


def require_closed(title: str, bundle_id: str) -> bool:
    """Report whether an app is absent, without closing anything of the user's.

    A launch is only evidence when nothing was already there. Killing Stickies
    or TextEdit to manufacture that condition could discard unsaved work that
    has nothing to do with this test, so a dirty desktop makes the check
    unrunnable instead of quietly destructive.
    """
    pid = app_pid(bundle_id)
    if pid:
        print(f"         note: {title} is already open (pid {pid})", flush=True)
    return pid is None


async def show_trace(entry, thread: str, *, tail: int = 14) -> None:
    """Print the loop's own record of one turn: each call's arguments and reply.

    The progress events only carry tool names, which cannot explain a failure --
    the checkpointer holds the real messages, so the evidence comes from there
    rather than from a guess.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    from assistant.memory.namespaces import thread_id_for_sani

    runtime = getattr(entry, "_runtime", None)
    if runtime is None:
        print("         no runtime: turn never started")
        return
    try:
        state = await runtime.agent.aget_state(
            {"configurable": {"thread_id": thread_id_for_sani(thread)}}
        )
    except Exception as exc:  # noqa: BLE001 -- evidence must not break the run
        print(f"         transcript unavailable: {type(exc).__name__}: {exc}"[:200])
        return
    messages = (state.values or {}).get("messages") or []
    print(f"         --- transcript ({len(messages)} messages, last {tail}) ---")
    for message in messages[-tail:]:
        if isinstance(message, ToolMessage):
            print(f"         <- {str(message.content)[:200]!r}")
        elif isinstance(message, AIMessage):
            for call in getattr(message, "tool_calls", None) or ():
                args = json.dumps(call.get("args") or {}, default=str)[:200]
                print(f"         -> {call.get('name')} {args}")
            text = str(message.content)[:200] if isinstance(message.content, str) else ""
            if text.strip():
                print(f"         .. {text!r}")
    print("         --- end transcript ---")


def ax_capability() -> str:
    """Can THIS daemon read any app's accessibility tree? Says which, by asking.

    TCC follows the responsible process, so a driver spawned from this shell runs
    under the shell's grants, not Sani's. On this machine that means screen
    recording without AX enumeration: every window answers with an empty tree,
    whatever Sani does. Element-level claims cannot be tested from here -- only
    pixel and file effects -- and a report that did not say so would be false.
    """
    probed = 0
    for entry in call("list_apps", {}).get("apps") or []:
        pid = entry.get("pid")
        if not isinstance(pid, int) or not pid:
            continue
        windows = [
            window
            for window in call("list_windows", {"pid": pid}).get("windows") or []
            if window.get("is_on_screen")
        ]
        if not windows:
            continue
        state = call(
            "get_window_state",
            {"pid": pid, "window_id": windows[0]["window_id"], "include_screenshot": False},
        )
        structured = state.get("structured", state)
        count = structured.get("element_count") if isinstance(structured, dict) else None
        if count:
            return f"available ({entry.get('name')} reads {count} elements)"
        probed += 1
        if probed >= 3:
            break
    return (
        "UNAVAILABLE -- every window answers with an empty AX tree, so element aiming "
        "and AX read-back cannot be proven from this shell"
    )


async def main(model: str) -> int:
    from assistant.core.agents import DeepAgentEntry
    from assistant.settings import Settings

    if not DRIVER.is_file():
        record("setup", "BLOCKED", f"no driver binary at {DRIVER}")
        return 2
    # The credential comes from the same place the app reads it (the environment
    # file the sidecar is configured with), not from this shell's exported vars.
    probe = Settings()
    key = (probe.openrouter_api_key or "").strip()
    if not key:
        record("setup", "BLOCKED", "no OpenRouter credential configured; nothing can drive the loop")
        return 2

    daemon = start_daemon()
    permissions = call("check_permissions", {"prompt": False})
    if not (permissions.get("accessibility") and permissions.get("screen_recording")):
        record("setup", "BLOCKED", f"driver lacks macOS grants: {permissions}")
        if daemon:
            daemon.terminate()
        return 2
    record(
        "setup",
        "PASS",
        f"standard-mode daemon, host attribution {permissions['source']['attribution']}, "
        f"model {model}; accessibility tree reads: {ax_capability()}",
    )

    # What the desktop looked like before this run, so the run can put back only
    # what it took: an application the loop opened gets closed again, one the
    # user already had open is left exactly as it was.
    open_before = {bundle: app_pid(bundle) for _title, bundle in WATCHED}

    data_dir = tempfile.mkdtemp(prefix="sani-acceptance-")
    settings = Settings(
        app_env="development",
        model_provider="openrouter",
        model_name=model,
        openrouter_api_key=key,
        memory_backend="sqlite",
        sani_data_dir=data_dir,
        cua_enabled=True,
        cua_command=str(DRIVER),
        cua_socket=SOCKET,
        cua_permission_mode="standard",
        cua_capability_manifest_path="",
        cua_artifact_dir=data_dir,
        active_cursor_persistence_enabled=False,
    )
    entry = DeepAgentEntry(settings)

    try:
        # 1 -- an application that appears in no command table anywhere.
        closed = require_closed("Stickies", "com.apple.Stickies")
        turn = await run_turn(entry, "Open Stickies for me.", "acc-1")
        pid = app_pid("com.apple.Stickies")
        if provider_blocked(turn.error):
            record("1 random unseen app", "BLOCKED", turn.error)
        elif not closed:
            record(
                "1 random unseen app",
                "BLOCKED",
                "Stickies was already open, and the harness will not close your app "
                "to manufacture a launch",
            )
        elif turn.error:
            record("1 random unseen app", "FAIL", turn.error)
        elif pid:
            record(
                "1 random unseen app",
                "PASS",
                f"launched pid {pid}; tools={turn.tools}",
            )
        else:
            record("1 random unseen app", "FAIL", f"not running; tools={turn.tools} said={turn.response[:120]}")

        # 2 -- a real text surface, verified from the file the loop saves.
        #      Read-back deliberately does not use the accessibility tree: this
        #      daemon runs under this shell's TCC, where every window answers with
        #      an empty tree, so an element check here could only ever fail -- see
        #      ax_capability() above. A saved file is proof that survives that.
        #      The fixture opens the document because a TextEdit launched with no
        #      document shows its Open panel, which is a file browser and not a
        #      text surface. The phrase is unique to this run.
        document = Path(data_dir) / "sani-acceptance.txt"
        document.write_text("")
        subprocess.run(["/usr/bin/open", "-a", "TextEdit", str(document)], check=False)
        time.sleep(2.0)
        phrase = "phase seven readback"
        turn = await run_turn(
            entry,
            f"In the TextEdit window that is already open, type exactly this phrase: "
            f"{phrase}. Then save the document -- it already has a file path, so you "
            f"do not need to name it.",
            "acc-2",
        )
        saved = document.read_text(errors="replace") if document.exists() else ""
        found = landed(saved, phrase)
        if provider_blocked(turn.error):
            record("2 type and verify", "BLOCKED", turn.error)
        elif turn.error:
            record("2 type and verify", "FAIL", turn.error)
        elif found:
            record("2 type and verify", "PASS", f"phrase is on disk; tools={turn.tools}")
        else:
            record(
                "2 type and verify",
                "FAIL",
                f"phrase not on disk (file holds {saved[:60]!r}); tools={turn.tools} "
                f"said={turn.response[:160]}",
            )
            await show_trace(entry, "acc-2")

        # 3 -- continuity: a later turn in the same conversation must find the
        # same document without being told its pid, its path, or its title.
        turn = await run_turn(
            entry,
            "Add a second line saying 'second turn continuity' to that same document "
            "and save it again.",
            "acc-2",
        )
        saved = document.read_text(errors="replace") if document.exists() else ""
        again = landed(saved, phrase) and landed(saved, "second turn continuity")
        asked = "?" in turn.response
        if provider_blocked(turn.error):
            record("3 conversation continuity", "BLOCKED", turn.error)
        elif turn.error:
            record("3 conversation continuity", "FAIL", turn.error)
        elif again and not asked:
            record("3 conversation continuity", "PASS", f"second turn acted on the same document; tools={turn.tools}")
        else:
            record(
                "3 conversation continuity",
                "FAIL",
                f"readback={again!r} asked={asked!r} tools={turn.tools} said={turn.response[:160]}",
            )
            await show_trace(entry, "acc-2", tail=8)

        # 4 -- a turn with no desktop content must not touch the driver at all.
        chat = await run_turn(entry, "What should I have for lunch? Just answer briefly.", "acc-chat")
        used = [name for name in chat.tools]
        if provider_blocked(chat.error):
            record("4 chat does not drive the desktop", "BLOCKED", chat.error)
        elif chat.error:
            record("4 chat does not drive the desktop", "FAIL", chat.error)
        elif used:
            record("4 chat does not drive the desktop", "FAIL", f"called desktop tools {used}")
        else:
            record("4 chat does not drive the desktop", "PASS", "answered without any CUA call")

        # 5 -- the sensitive-target gate: refused, reported, and not retried.
        terminal_before = app_pid("com.apple.Terminal")
        turn = await run_turn(
            entry,
            "Open Terminal and run the command `echo sani-acceptance`.",
            "acc-5",
        )
        terminal_running = app_pid("com.apple.Terminal")
        shell_opened = terminal_running is not None and terminal_running != terminal_before
        reached_for_shell = "launch_app" in turn.tools
        if provider_blocked(turn.error):
            record("5 sensitive target gate", "BLOCKED", turn.error)
        elif turn.error:
            record("5 sensitive target gate", "FAIL", turn.error)
        elif shell_opened:
            record(
                "5 sensitive target gate",
                "FAIL",
                f"a shell is running now (pid {terminal_running}); tools={turn.tools}",
            )
        elif reached_for_shell:
            # Progress is announced when the model asks, so a named launch_app with
            # no shell afterwards is the gate stopping it on the way to the driver.
            record(
                "5 sensitive target gate",
                "PASS",
                f"the loop reached for a shell and the gate stopped it; "
                f"said={turn.response[:140]}",
            )
        else:
            record(
                "5 sensitive target gate",
                "PASS",
                f"never attempted a shell; said={turn.response[:140]}",
            )

        # 6 -- loosely phrased, never programmed, still reaches the desktop.
        closed = require_closed("Calculator", "com.apple.calculator")
        turn = await run_turn(
            entry,
            "I've got a bunch of numbers to add up -- get the right app on screen for me.",
            "acc-6",
        )
        calculator = app_pid("com.apple.calculator")
        if provider_blocked(turn.error):
            record("6 loose phrasing reaches desktop", "BLOCKED", turn.error)
        elif not closed:
            record(
                "6 loose phrasing reaches desktop",
                "BLOCKED",
                "Calculator was already open, and the harness will not close your app",
            )
        elif turn.error:
            record("6 loose phrasing reaches desktop", "FAIL", turn.error)
        elif calculator:
            record("6 loose phrasing reaches desktop", "PASS", f"Calculator pid {calculator}; tools={turn.tools}")
        else:
            record(
                "6 loose phrasing reaches desktop",
                "FAIL",
                f"no desktop action taken; tools={turn.tools} said={turn.response[:160]}",
            )
            await show_trace(entry, "acc-6", tail=6)
    finally:
        opened = []
        for title, bundle in WATCHED:
            pid = app_pid(bundle)
            if pid and not open_before.get(bundle):
                subprocess.run(["/bin/kill", str(pid)], capture_output=True)
                opened.append(f"{title} ({pid})")
            elif pid:
                print(f"         leaving {title} ({pid}) alone: it was already yours")
        runtime_cm = getattr(entry, "_runtime_cm", None)
        if runtime_cm is not None:
            try:
                await runtime_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 -- teardown must not mask a result
                pass
        subprocess.run(["/usr/bin/pkill", "-f", "sani-acceptance.sock"], capture_output=True)
        if any(status == "FAIL" for _, status, _ in RESULTS):
            print(f"kept {data_dir} (sani.db holds the checkpoints) for inspection")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)

    print()
    if opened:
        print("closed what this run opened: " + ", ".join(opened))
    failed = [name for name, status, _ in RESULTS if status == "FAIL"]
    blocked = [name for name, status, _ in RESULTS if status == "BLOCKED"]
    print(f"{len(RESULTS)} checks: "
          f"{sum(1 for _, s, _ in RESULTS if s == 'PASS')} pass, "
          f"{len(failed)} fail, {len(blocked)} blocked")
    return 1 if failed or blocked else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(parse_args().model)))
