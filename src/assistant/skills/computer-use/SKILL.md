---
name: computer-use
description: "Step-by-step procedure for operating the user's desktop through CUA tools: identify the target, observe before acting, act minimally, and verify every important action. Use whenever computer control is required."
---

# Computer use operating procedure

Speed and focus rules (these matter as much as correctness):

1. **Act, do not narrate.** Do not write explanations between actions. Every sentence costs a full model round trip and delays the user.
2. **Text-first observation.** `get_window_state` is preconfigured to return the element tree without screenshots. Only pass `include_screenshot: true` when element text alone cannot identify the target, and prefer `max_dimension` to keep captures small.
3. **One observation covers several actions.** After `get_window_state`, use the `element_token` values for every click/type on that screen. Re-observe only after the screen actually changes or when an action errors.
4. **Do not re-observe unchanged screens.** Repeating `get_window_state` without an intervening action wastes a turn.

Required loop -- repeat until the goal is verified:

1. Identify the target app and window (`list_apps`; `launch_app` if it is not running). A launch answers with the new pid and its windows: keep working on *that* application, and never ask the user for a pid.
2. Observe that surface once (`get_window_state` with its pid and window_id) before any click, type, scroll or key press. Element tokens belong to that snapshot.
3. Take the least disruptive supported action that moves the goal forward.
4. Observe the result whenever the action's own response does not already prove it.
5. Continue to the next step. Ambiguity is a reason to look again, never a reason to refuse or to stop.

Recovery ladder -- when an action fails, work down this list instead of quitting:

1. Read the fault named in the error and do the move it suggests.
2. Window gone or stale -> `list_windows` again, aim at a window that is on screen and of real size, re-snapshot, retry.
3. Application not running -> `launch_app` by name, then adopt the pid it returns.
4. Element refused the action (no `AXPress`, wrong control) -> same target by another route: `set_value`, a keyboard route (`press_key`/`hotkey`), the menu bar, then a coordinate click as the last rung.
5. Action accepted but `unverifiable`, or the tree contradicts what should have happened -> capture one screenshot (`include_screenshot: true`) and judge from the picture.
6. Background delivery did not land -> retry the same action with `delivery_mode: "foreground"`.
7. Only after these are exhausted: say what was attempted, what the driver reported, and the one thing needed to continue.

Never repeat the same action on the same unchanged screen without new evidence; a `bring_to_front` or a foreground delivery moves the window, so re-observe before aiming again.
