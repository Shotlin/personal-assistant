import MainApp from "./MainApp";
import { mountApp } from "../lib/boot";

/**
 * `?control-states` shows every computer-control state at once, through the same
 * component the real page uses. The status page was once verified by reading its
 * code, which is how it kept rendering macOS permissions as granted while the
 * runtime sat permanently on `not_authorized` -- so the states get looked at now.
 *
 * Guarded by the absence of Tauri's own bridge as well as the query string: the
 * packaged app can never land here, only a browser session on the dev server.
 */
const GALLERY =
  new URLSearchParams(window.location.search).has("control-states") &&
  !("__TAURI_INTERNALS__" in window);

if (GALLERY) {
  void import("./ComputerControlGallery").then(({ default: Gallery }) =>
    mountApp(<Gallery />, "main"),
  );
} else {
  mountApp(<MainApp />, "main");
}
