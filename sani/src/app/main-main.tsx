import MainApp from "./MainApp";
import { mountApp } from "../lib/boot";

/**
 * `?control-states` shows every computer-control state at once, through the same
 * component the real page uses. The status page was once verified by reading its
 * code, which is how it kept rendering macOS permissions as granted while the
 * runtime sat permanently on `not_authorized`. So the states get looked at now.
 *
 * `?preview` (dev server only) runs the real window against fixture data so every
 * chat/settings state can be seen without the Rust host. Both are guarded by the
 * absence of Tauri's own bridge, so the packaged app can never land here.
 */
const params = new URLSearchParams(window.location.search);
const inBrowser = !("__TAURI_INTERNALS__" in window);
const GALLERY = params.has("control-states") && inBrowser;
const PREVIEW = import.meta.env.DEV && params.has("preview") && inBrowser;

async function start() {
  if (GALLERY) {
    const { default: Gallery } = await import("./ComputerControlGallery");
    mountApp(<Gallery />, "main");
    return;
  }
  if (PREVIEW) {
    const { install } = await import("../dev/preview-backend");
    install(params.get("scenario") ?? "history");
  }
  mountApp(<MainApp />, "main");
}

void start();
