import { StrictMode, createElement, useEffect } from "react";
import { createRoot } from "react-dom/client";
import type { ReactNode } from "react";
import { emit } from "@tauri-apps/api/event";

/**
 * Called once React has really mounted a window's root (from a child effect,
 * not right after `render()`, which is asynchronous). Hides the inline boot
 * fallback and emits the UI-ready handshake the Rust side records as startup
 * evidence.
 */
export function announceReady(which: string): void {
  const fallback = document.getElementById("boot-fallback");
  if (fallback) fallback.style.display = "none";
  void emit(`sani://${which}-ui-ready`).catch(() => {});
}

function ReadySignal({ which }: { which: string }) {
  useEffect(() => announceReady(which), [which]);
  return null;
}

/**
 * Frontend failures must never be hidden behind glass: every uncaught error,
 * rejected promise and console.error is mirrored to the Rust log, which also
 * covers release builds where the WebView inspector is unavailable.
 */
export function installErrorBridge(which: string): void {
  const report = (message: string) => {
    void emit("sani://ui-error", { label: which, message }).catch(() => {});
  };
  window.addEventListener("error", (event) => {
    report(`${event.message} at ${event.filename}:${event.lineno}:${event.colno}`);
  });
  window.addEventListener("unhandledrejection", (event) => {
    report(`unhandled rejection: ${String(event.reason)}`);
  });
  const nativeError = console.error.bind(console);
  console.error = (...args: unknown[]) => {
    report(args.map((a) => (typeof a === "string" ? a : String(a))).join(" "));
    nativeError(...args);
  };
}

/** Mount one Sani window and prove it came up, or leave the visible fallback. */
export function mountApp(node: ReactNode, which: string): void {
  installErrorBridge(which);
  const container = document.getElementById("root");
  if (!container) {
    void emit("sani://ui-error", {
      label: which,
      message: "missing #root container in the bundled HTML",
    });
    return;
  }
  createRoot(container).render(
    createElement(StrictMode, null, node, createElement(ReadySignal, { which })),
  );
}
