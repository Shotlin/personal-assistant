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

Required procedure:

1. Identify the target app/window (list_apps; launch if needed).
2. Observe current state once.
3. Select the least disruptive supported action.
4. Execute one meaningful action.
5. Observe the result only if the outcome is not already clear from the action's response.
6. Continue only if the observed state matches expectations.
7. If the state is ambiguous, capture one more observation (targeted `query` where possible) before retrying.
8. Do not repeatedly click the same coordinate without new evidence.
9. Stop after repeated unsupported/ambiguous results and report the blocker.
