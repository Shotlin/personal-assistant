# Agent Designer - Visual and Interaction Specification

**Version:** 1.0 | **Date:** 2026-09-18
**Purpose:** an implementable design contract, not a screenshot or already-running application.
**Companions:** Requirements defines behavior/security; Implementation Plan defines build order.

## 1. Visual direction

Build a restrained, premium SaaS workspace: predominantly black, white and neutral grays, with color reserved for meaningful states. Prioritize readable configuration, accurate live activity and low visual noise. No decorative gradients, glowing neon canvas, oversized cards, permanent moving particles or emoji-based icons.

Dark is the initial theme; include a complete light theme and a system option. Use the system sans-serif font stack, with a system monospace stack for IDs, durations and code. No downloadable proprietary fonts are required.

Agent Designer is a separate React route served by the gateway. It resembles Open WebUI and provides a return link, but must not present a fake shared sidebar or claim automatic shared authentication. Keep the actual Open WebUI application unchanged.

## 2. Primary layout

Reference desktop: 1440 x 900. These are proposed design dimensions, not an image already produced.

```text
+--------------------------------------------------------------------------+
| < Open WebUI   Agent Designer   [Vion v]    Active v3  Draft v4   Theme     |
| Design | Live | History             Save draft   Validate   Activate     |
+-------------------+------------------------------------+-----------------+
| COMPONENTS        |                                    | PROPERTIES      |
| [Search...]       |            AGENT CANVAS            | CUA Driver      |
|                   |                                    | Source: Gateway |
| Models            | [Model] ---- model --+             | Health: Online  |
| Prompts           | [Prompt] --- prompt -+             | Attached: Yes   |
| Skills            |                     [ Vion ]       |                 |
| Memory            | [Memory] --- memory -+-- tools --->| [Enabled]       |
| Context           | [Context] -- context-+  [CUA]      | Tool selection  |
| Knowledge         |                          [MCP]     | Permissions     |
| Tools             |                                    | Limits          |
| MCP Servers       |                                    | Source details  |
|                   |                                    |                 |
| + Add connection  | [-] 100% [+] Fit   Lock   MiniMap    | Test connection |
+-------------------+------------------------------------+-----------------+
| Diagnostics: 0 errors, 1 warning      Unsaved changes | Runtime unchanged |
+--------------------------------------------------------------------------+
```

Widths: library 248 px (resizable 220-320), inspector 320 px (280-420), canvas fills the remainder. Header 56 px; mode/action bar 44 px; diagnostics strip 32 px. Panel widths persist as a user preference, not agent behavior. A selected node can be edited without hiding its connection handles.

Below 1100 px collapse the library into a drawer and inspector into a right sheet. Below 900 px use click-to-add rather than requiring dragging; allow full-screen canvas. Prioritize desktop editing, but make settings and run inspection usable at 768 px.

## 3. Color and type tokens

```css
:root[data-theme="dark"] {
  --bg: #09090B;
  --panel: #111113;
  --surface: #18181B;
  --border: #3F3F46;
  --text: #FAFAFA;
  --muted: #A1A1AA;
  --blue: #60A5FA;
  --green: #4ADE80;
  --amber: #FBBF24;
  --red: #F87171;
}
:root[data-theme="light"] {
  --bg: #FAFAFA;
  --panel: #FFFFFF;
  --surface: #F4F4F5;
  --border: #71717A;
  --text: #18181B;
  --muted: #52525B;
  --blue: #1D4ED8;
  --green: #15803D;
  --amber: #92400E;
  --red: #B91C1C;
}
```

Use muted backgrounds and borders for everyday cards. Status color may be a 2 px left border, icon, label and subtle tinted surface. Avoid full saturated card fills. Primary buttons are white-on-black in light mode and black-on-white in dark mode.

Typography: page title 20/28 medium; section headings 13/20 semibold; normal text 14/20; metadata 12/18; code 12/18. Use a 4 px spacing scale, 8 px control radius, 12 px panel/card radius and restrained shadows only on overlays.

Status labels always accompany color. Check WCAG contrast on actual backgrounds during implementation; do not assume every low-opacity variant remains readable. Keyboard focus rings must be distinguishable from live-run highlighting.

## 4. State vocabulary

| Meaning | Treatment | Example label |
|---|---|---|
| Neutral/configured | Gray border and icon | Attached, Ready, Not used |
| Running/new information | Blue border plus activity/change icon | Model running, Update available |
| Operation returned successfully | Green check plus text | Tool returned successfully |
| Independently verified result | Green check with explicit evidence label | Verified against displayed result |
| Waiting/warning | Amber icon and label | Needs approval, Budget warning |
| Failure/blocked | Red border and error icon | Connection failed, Policy denied |
| Disabled/disconnected | Dashed neutral edge, reduced emphasis | Disabled, Not attached |
| Unknown/stale | Neutral or amber clock/network icon | Status unavailable, Reconnecting |

Keep four concepts separate: selection, resource health, graph activation and run outcome. A healthy MCP server is not proof that a particular task succeeded. An attached tool is not necessarily used in the current run.

## 5. Agent list and top controls

The entry page lists accessible agents with name, purpose, active revision, draft state, last run and permitted actions. Search, create, duplicate and archive are available. Do not show other users' inaccessible agents or placeholder agents as installed.

Create Agent asks for name and description, then opens a draft with one Agent root. Provide a clearly named **Use Vion as template** action, copying configuration only. No automatic memory/history/credential copying.

On the canvas, show **Active v3 / Draft v4** separately. Save stores the draft. Validate shows per-node errors. Activate opens an impact dialog and is unavailable when validation is stale or failed. Keyboard Save never activates.

## 6. Components and properties

The library searches display name, description and tags. Group by Model, Prompt, Skill, Memory, Context, Knowledge, Tool and MCP. Each row shows source, compatibility and access. Distinguish Open WebUI-owned, gateway built-in and gateway connection resources.

An unavailable adapter appears in a collapsed **Requires setup** group, not as a working draggable component. Show a concrete reason and the required setup action. Discovering a new tool does not enable it automatically.

A standard node is approximately 220 px wide with icon, title, type/source badge, concise configuration summary, state label and typed handles. A collapsed MCP node shows selected/available tool count; its inspector lists individual tools and schema-change indicators. Treat CUA as a specialized connection with app/action/cursor settings, not an unrelated second execution system.

Inspector sections:

1. **Configuration:** actual editable fields and supported parameter choices.
2. **Access and limits:** read/write, selected tools, timeout, permissions, context allowance.
3. **Source and version:** authoring source, source ID/hash, update status and edit permissions.
4. **Diagnostics:** health, latest error and explicit connection test.

Model keys appear only in a write-only credential dialog. Show a credential label and rotation date afterward, never a reveal button. Unsupported provider settings are not rendered as functioning controls.

Prompt/skill editors use CodeMirror Markdown with a sanitized preview, name/description/tags and source selection. Built-ins are read-only with **Copy as custom skill**. Save writes to Open WebUI for an Open WebUI-owned resource. Dependency updates remain drafts until activation.

## 7. Canvas interactions

Use actual `@xyflow/react` custom nodes and handles. The root has labeled ports matching graph semantics. Connections are selectable, deletable and keyboard-accessible. Invalid links show a reason before dropping; backend validation remains authoritative.

Provide zoom buttons, wheel/pinch zoom, space-drag pan, fit view, selection lock and minimap toggle. Proposed zoom range 0.25-2.0 and snap grid 16 px. Do not force snapping when a user temporarily disables it.

Palette drag/drop needs application code using pointer events and `screenToFlowPosition`; React Flow does not automatically implement external sidebar drag/drop. Supply a click-to-add alternative. Save nodes, edges and viewport; avoid persisting hover/selection into runtime configuration. [D1-D2]

Undo/redo uses a bounded local history, grouping one drag gesture as one action. Never capture credentials or every SSE update in undo history. Disable destructive keyboard shortcuts while typing in an editor. Duplicating a singleton node either disconnects the copy or requests replacement; it cannot create two active models silently.

Use neutral smooth-step/Bezier edges. Animate an edge only when an actual event references its resource. Do not imply that visual left-to-right placement defines the agent's execution sequence.

## 8. Live mode: see real work happen

Live is a first-release feature, not a later placeholder. It must work while the user talks to an agent through Open WebUI and then opens Designer to inspect that run.

```text
+--------------------------------------------------------------------------+
| Vion  | Live - read only | Run selector | Using v3 | New active version v4 |
+-------------------+------------------------------------+-----------------+
| THIS RUN          | Exact graph snapshot for run       | CURRENT STEP    |
| Started 12:41:08   |                                    | Read window     |
| Elapsed 00:04     | [Context] -- [Vion] -- [CUA: active] | Dispatched      |
| Route: Agent      |               |                    | Duration 180 ms |
| Model calls: 1    |         [Model: returned]          | Source: CUA     |
| Tokens: pending   |                                    | Evidence gated  |
+-------------------+------------------------------------+-----------------+
| TIMELINE  Accepted > Context prepared > Model returned > Tool started     |
| 12:41:12  CUA / get_window_state / Running                                |
| 12:41:12  Context prepared / estimate available                            |
|                                        Pause at safe point | Cancel run |
+--------------------------------------------------------------------------+
```

The times above are illustrative UI copy, not benchmark results. Actual values must come from events.

Show a pinned run revision, current stage, elapsed time, route (local recipe/compact planner/agent), model attempts, selected tool, actual/estimated usage badges and ordered timeline. Display model wait as **Awaiting model**, not fabricated reasoning text.

Timeline rows expand into sanitized metadata and authorized evidence links. A server-side policy check controls screenshots and file excerpts. Never automatically upload full desktop images just to animate this view.

A different active configuration must not redraw a running task as though it used that new configuration. Show **This run uses v3. New runs use v4.** Provide **Edit draft** as a separate navigation action.

SSE loss displays **Reconnecting; last event at ...** with elapsed age. Resume by event sequence or load a snapshot after an explicit gap. Do not silently reset counters, restart tasks or paint missing events green. Two monitoring tabs observe one run without creating duplicate inference or tools.

Pause means no new dispatch at the next safe boundary; Cancel requests termination and shows when the request was acknowledged. Already dispatched or unknown-effect operations stay visible. A completed side effect is not undone by a red Cancel button.

## 9. Safe change and error dialogs

Activation dialog shows:

- Active revision and candidate revision.
- Added/removed models, sources, tools and scopes.
- Credential-reference changes without values.
- Input-context estimate delta, labeled estimated.
- Compatibility failures and new permissions requiring operator approval.
- Effect: **New runs only; existing runs retain their version**.

Failure copy: **Could not activate v4. V3 remains active.** Include the failed node and safe corrective action. Preserve the draft.

Immediate disable is an explicit red action: **Revoke this capability for queued and active runs**. Explain that already performed actions cannot be undone. This action must call the revocation endpoint, not simply delete an edge.

Cover empty catalog, no permissions, expired credentials, unavailable model, missing skill, deleted source, invalid graph, source update conflict, offline MCP, schema drift, context over budget, no active revision, terminal unavailable and live-stream gaps. No generic spinner that hides a blocked task indefinitely.

## 10. React Flow attribution

Use the documented API, not a CSS selector or patched library source:

```tsx
<ReactFlow
  nodes={nodes}
  edges={edges}
  proOptions={{ hideAttribution: true }}
/>
```

This is the relevant configuration fragment, not the full canvas component. Retain React Flow/xyflow copyright and MIT notices in the distribution and About/Licenses screen. Do not purchase or copy Pro examples merely to implement ordinary graph interactions.

The current React Flow API explicitly allows attribution removal by non-Pro users; its separate marketing attribution page uses stronger subscription wording. Record the selected package license and this documentation discrepancy during dependency review. Apply this only to the React Flow canvas badge. Do not remove Open WebUI branding or unrelated content watermarks. [D3-D4]

## 11. Performance and accessibility acceptance

Memoize custom node components and stable callbacks, use narrow Zustand selectors, and keep the live reducer separate from the draft graph. Batch progress rendering, virtualize long timelines and stop expensive animation in background tabs. Do not update the database on each pointer pixel or use the LLM to generate monitoring UI. React Flow documents avoiding unnecessary re-renders as a key performance practice. [D5]

Test 100 nodes and 10 run events per second on the owner's machine. Proposed targets are sub-100 ms local editor feedback and event-to-view p95 below 500 ms, excluding provider latency; record actual measurements and failures.

Keyboard-only use must support adding a node, selecting/editing it, connecting through an accessible control, validating and saving. Honor reduced-motion preferences. Never use color as the only status channel. Test contrast, readable focus indicators, 200% zoom, long names, empty states and both themes. Use Playwright and axe-core plus manual inspection of dynamic canvas behavior.

## 12. Sources

- **D1** [React Flow drag/drop](https://reactflow.dev/examples/interaction/drag-and-drop)
- **D2** [React Flow save/restore](https://reactflow.dev/examples/interaction/save-and-restore)
- **D3** [ReactFlow API including proOptions](https://reactflow.dev/api-reference/react-flow) and [ProOptions](https://reactflow.dev/api-reference/types/pro-options)
- **D4** [xyflow MIT license](https://github.com/xyflow/xyflow/blob/main/LICENSE) and [attribution page](https://reactflow.dev/remove-attribution)
- **D5** [React Flow performance guidance](https://reactflow.dev/learn/advanced-use/performance)

The layout, tokens, status vocabulary and measurements are proposed design decisions derived from the user's brief, not claims about built-in Open WebUI behavior.
