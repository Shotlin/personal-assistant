import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  cancelOverlayPreview,
  overlayEditorState,
  previewOverlayLayout,
  resetOverlayDraft,
  saveOverlayLayout,
  type DisplaySnapshot,
  type LogicalRect,
  type OverlayEditorState,
  type OverlayFrame,
  type OverlayLayout,
  type OverlayLimits,
} from "../lib/tauri";

/** Which overlay the pointer or keyboard is editing. */
type Target = "pill" | "panel";
type DragMode = "move" | "width" | "height";

const MOVE_STEP = 8;
const RESIZE_STEP = 8;
const MAX_CANVAS_HEIGHT = 460;
const LOAD_FAILED = "Sani could not read the display layout. Close and reopen this page to try again.";
const APPLY_FAILED = "Sani could not change the overlay layout. Your saved placement is unchanged.";

const names: Record<Target, string> = { pill: "Voice pill", panel: "Conversation panel" };

const clamp = (value: number, low: number, high: number) => Math.min(Math.max(value, low), high);

/** Mirror of the native clamp, so the canvas shows what Sani will really place.
 *  Native stays authoritative: Preview and Save return its resolved frames. */
function clampRect(
  target: Target,
  rect: LogicalRect,
  display: DisplaySnapshot,
  limits: OverlayLimits,
): LogicalRect {
  const pill = target === "pill";
  const minWidth = pill ? limits.pill_min_width : limits.panel_min_width;
  const maxWidth = pill ? limits.pill_max_width : limits.panel_max_width;
  const minHeight = pill ? limits.pill_height : limits.panel_min_height;
  const maxHeight = pill ? limits.pill_height : limits.panel_max_height;
  const width = Math.min(Math.max(rect.width, minWidth), maxWidth, Math.max(display.work_width, 1));
  const height = Math.min(
    Math.max(rect.height, minHeight),
    maxHeight,
    Math.max(display.work_height, 1),
  );
  return {
    width,
    height,
    x: clamp(rect.x, 0, Math.max(display.work_width - width, 0)),
    y: clamp(rect.y, 0, Math.max(display.work_height - height, 0)),
  };
}

function rectOf(
  target: Target,
  frame: OverlayFrame,
  display: DisplaySnapshot,
  limits: OverlayLimits,
): LogicalRect {
  return clampRect(
    target,
    {
      x: frame.x_ratio * display.work_width,
      y: frame.y_ratio * display.work_height,
      width: frame.width,
      height: target === "pill" ? limits.pill_height : frame.height,
    },
    display,
    limits,
  );
}

function withRect(
  layout: OverlayLayout,
  target: Target,
  rect: LogicalRect,
  display: DisplaySnapshot,
): OverlayLayout {
  const frame: OverlayFrame = {
    x_ratio: display.work_width > 0 ? rect.x / display.work_width : 0,
    y_ratio: display.work_height > 0 ? rect.y / display.work_height : 0,
    width: rect.width,
    height: rect.height,
  };
  return { ...layout, [target]: frame };
}

/** Fold native's resolved frames back into a draft, so the canvas never keeps
 *  showing a position native just clamped. */
function layoutFrom(state: OverlayEditorState, display: DisplaySnapshot | null): OverlayLayout {
  if (!display || display.work_width <= 0 || display.work_height <= 0) return state.committed;
  const frame = (rect: LogicalRect): OverlayFrame => ({
    x_ratio: rect.x / display.work_width,
    y_ratio: rect.y / display.work_height,
    width: rect.width,
    height: rect.height,
  });
  return {
    display_affinity: state.active_display ?? state.committed.display_affinity,
    pill: frame(state.pill),
    panel: frame(state.panel),
  };
}

/** The menu-bar/notch and Dock strips: real insets from native, never guessed. */
function exclusions(display: DisplaySnapshot) {
  const right = display.work_x + display.work_width;
  const bottom = display.work_y + display.work_height;
  return [
    { key: "top", x: 0, y: 0, width: display.screen_width, height: display.work_y },
    {
      key: "bottom",
      x: 0,
      y: bottom,
      width: display.screen_width,
      height: display.screen_height - bottom,
    },
    { key: "left", x: 0, y: display.work_y, width: display.work_x, height: display.work_height },
    {
      key: "right",
      x: right,
      y: display.work_y,
      width: display.screen_width - right,
      height: display.work_height,
    },
  ].filter((strip) => strip.width > 1 && strip.height > 1);
}

const displayLabel = (display: DisplaySnapshot) => {
  const name = display.id.slice(display.id.indexOf(":") + 1);
  return name || display.id;
};

const message = (error: unknown, fallback: string) =>
  typeof error === "string" && error.trim() ? error : fallback;

export default function OverlayLayoutEditor() {
  const [state, setState] = useState<OverlayEditorState | null>(null);
  const [draft, setDraft] = useState<OverlayLayout | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Target>("pill");
  const [canvasWidth, setCanvasWidth] = useState(0);
  const host = useRef<HTMLDivElement | null>(null);
  const previewing = useRef(false);

  const display = state?.displays.find((entry) => entry.id === state.active_display) ?? null;
  const limits = state?.limits ?? null;
  const editable = state?.active_display != null && Boolean(display && limits && draft);

  const adopt = (next: OverlayEditorState, note: string) => {
    const resolved = next.displays.find((entry) => entry.id === next.active_display) ?? null;
    setState(next);
    previewing.current = next.preview_active;
    setDraft(layoutFrom(next, resolved));
    setDirty(false);
    setError("");
    setNotice([note, ...next.adjustments].filter(Boolean).join(" "));
  };

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const initial = await overlayEditorState();
        if (mounted) adopt(initial, "");
      } catch {
        if (mounted) setError(LOAD_FAILED);
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  // Leaving the page while a preview is live must never strand the real
  // overlays at an unsaved position.
  useEffect(
    () => () => {
      if (previewing.current) void cancelOverlayPreview().catch(() => undefined);
    },
    [],
  );

  useLayoutEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      setCanvasWidth(entries[0]?.contentRect.width ?? 0);
    });
    observer.observe(element);
    setCanvasWidth(element.getBoundingClientRect().width);
    return () => observer.disconnect();
  }, []);

  const scale =
    display && canvasWidth > 0
      ? Math.min(canvasWidth / display.screen_width, MAX_CANVAS_HEIGHT / display.screen_height)
      : 0;

  // The transform runs inside the state updater: key auto-repeat and pointer
  // moves arrive faster than React re-renders, so reading `draft` here would
  // drop every event but the last of a burst.
  const edit = (target: Target, transform: (rect: LogicalRect) => LogicalRect) => {
    if (!display || !limits) return;
    setDraft((current) => {
      if (!current) return current;
      const rect = clampRect(
        target,
        transform(rectOf(target, current[target], display, limits)),
        display,
        limits,
      );
      return withRect(current, target, rect, display);
    });
    setDirty(true);
  };

  const beginDrag = (event: ReactPointerEvent, target: Target, mode: DragMode) => {
    event.preventDefault();
    event.stopPropagation();
    setSelected(target);
    if (!display || !limits || !draft || scale <= 0) return;
    const origin = rectOf(target, draft[target], display, limits);
    const startX = event.clientX;
    const startY = event.clientY;
    const move = (moved: PointerEvent) => {
      const dx = (moved.clientX - startX) / scale;
      const dy = (moved.clientY - startY) / scale;
      edit(target, () =>
        mode === "move"
          ? { ...origin, x: origin.x + dx, y: origin.y + dy }
          : mode === "width"
            ? { ...origin, width: origin.width + dx }
            : { ...origin, height: origin.height + dy },
      );
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  };

  const directions: Record<string, [number, number]> = {
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0],
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
  };

  const onKey = (event: ReactKeyboardEvent, target: Target) => {
    const direction = directions[event.key];
    if (!direction || !display || !limits || !draft) return;
    event.preventDefault();
    setSelected(target);
    const step = event.shiftKey ? RESIZE_STEP : MOVE_STEP;
    const [dx, dy] = direction;
    edit(target, (rect) =>
      event.shiftKey
        ? { ...rect, width: rect.width + dx * step, height: rect.height + dy * step }
        : { ...rect, x: rect.x + dx * step, y: rect.y + dy * step },
    );
  };

  const outgoing = (): OverlayLayout | null =>
    draft && state ? { ...draft, display_affinity: state.active_display ?? "" } : null;

  const run = async (action: (layout: OverlayLayout) => Promise<OverlayEditorState>, note: string) => {
    const layout = outgoing();
    if (!layout) return;
    setBusy(true);
    setError("");
    try {
      adopt(await action(layout), note);
    } catch (caught) {
      setError(message(caught, APPLY_FAILED));
    } finally {
      setBusy(false);
    }
  };

  const onCancel = async () => {
    setBusy(true);
    setError("");
    try {
      adopt(await cancelOverlayPreview(), "Preview ended — your saved placement is back.");
    } catch (caught) {
      setError(message(caught, APPLY_FAILED));
    } finally {
      setBusy(false);
    }
  };

  const onReset = async () => {
    setBusy(true);
    setError("");
    try {
      const defaults = await resetOverlayDraft();
      setDraft({ ...defaults, display_affinity: state?.active_display ?? "" });
      setDirty(true);
      setNotice(
        "Draft reset to Sani's defaults. Nothing moved yet — Preview to see it, Save to keep it.",
      );
    } catch (caught) {
      setError(message(caught, APPLY_FAILED));
    } finally {
      setBusy(false);
    }
  };

  const shape = (target: Target) => {
    if (!display || !limits || !draft || scale <= 0) return null;
    const rect = rectOf(target, draft[target], display, limits);
    const box = {
      left: (display.work_x + rect.x) * scale,
      top: (display.work_y + rect.y) * scale,
      width: rect.width * scale,
      height: rect.height * scale,
    };
    return (
      <div
        className={`layout-shape ${target}${selected === target ? " selected" : ""}`}
        style={box}
        role="button"
        tabIndex={0}
        aria-pressed={selected === target}
        aria-describedby="layout-keyboard-hint"
        aria-label={`${names[target]}, ${Math.round(rect.width)} by ${Math.round(
          rect.height,
        )} points, ${Math.round(rect.x)} from the left and ${Math.round(
          rect.y,
        )} from the top of the usable work area`}
        onPointerDown={(event) => beginDrag(event, target, "move")}
        onKeyDown={(event) => onKey(event, target)}
      >
        <span className="layout-shape-name">{names[target]}</span>
        <span className="layout-readout">
          {Math.round(rect.width)} × {Math.round(rect.height)} pt
        </span>
        <span
          className="layout-handle side"
          aria-hidden="true"
          onPointerDown={(event) => beginDrag(event, target, "width")}
        />
        {target === "panel" && (
          <span
            className="layout-handle bottom"
            aria-hidden="true"
            onPointerDown={(event) => beginDrag(event, target, "height")}
          />
        )}
      </div>
    );
  };

  return (
    <section className="layout-editor">
      <header className="conversation-header">
        <div>
          <h1>Layout</h1>
          <p>Place and size Sani's voice pill and conversation panel.</p>
        </div>
        <span className="state-chip">
          {state?.preview_active ? "Previewing" : dirty ? "Unsaved draft" : "Saved"}
        </span>
      </header>

      <div className="layout-body">
        <div className="layout-canvas-host" ref={host}>
          {display && scale > 0 ? (
            <div
              className="layout-canvas"
              style={{
                width: display.screen_width * scale,
                height: display.screen_height * scale,
              }}
              role="group"
              aria-label={`${displayLabel(display)}, usable work area ${Math.round(
                display.work_width,
              )} by ${Math.round(display.work_height)} points`}
            >
              {exclusions(display).map((strip) => (
                <div
                  key={strip.key}
                  className={`layout-excluded ${strip.key}`}
                  aria-hidden="true"
                  title={strip.key === "top" ? "Menu bar" : "Dock"}
                  style={{
                    left: strip.x * scale,
                    top: strip.y * scale,
                    width: strip.width * scale,
                    height: strip.height * scale,
                  }}
                />
              ))}
              <div
                className="layout-work-area"
                aria-hidden="true"
                style={{
                  left: display.work_x * scale,
                  top: display.work_y * scale,
                  width: display.work_width * scale,
                  height: display.work_height * scale,
                }}
              />
              {shape("panel")}
              {shape("pill")}
            </div>
          ) : (
            <p className="layout-empty">
              {state
                ? "Sani cannot see a display right now, so your saved placement is unchanged."
                : "Reading your display…"}
            </p>
          )}
        </div>

        <div className="layout-controls">
          <p className="layout-hint" id="layout-keyboard-hint">
            Drag a shape, or focus it and use arrow keys to move it and shift + arrow keys to
            resize it. Striped edges are the menu bar and Dock — Sani never places an overlay
            there.
          </p>
          {display && (
            <p className="layout-display">
              Editing <strong>{displayLabel(display)}</strong> at {Math.round(display.scale_factor * 100) / 100}
              × scaling
              {display.is_main_window_display ? " · holds the Sani window" : ""}
            </p>
          )}
          {state?.preview_active && (
            <p className="layout-preview">
              Previewing — the real voice pill and conversation panel are on screen at these
              positions.
            </p>
          )}
          <div className="layout-actions">
            <button
              className="layout-primary"
              onClick={() =>
                void run(previewOverlayLayout, "Previewing on your real overlays.")
              }
              disabled={!editable || busy}
            >
              Preview
            </button>
            <button
              onClick={() => void run(saveOverlayLayout, "Saved — your overlays keep this placement.")}
              disabled={!editable || busy}
            >
              Save
            </button>
            <button
              onClick={() => void onCancel()}
              disabled={busy || (!state?.preview_active && !dirty)}
            >
              Cancel
            </button>
            <button onClick={() => void onReset()} disabled={busy || !draft}>
              Reset
            </button>
          </div>
          <p className="layout-notice" role="status">
            {error || notice}
          </p>
        </div>
      </div>
    </section>
  );
}
