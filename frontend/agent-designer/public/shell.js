/* Agent Designer shell integration (injected into Open WebUI pages).
 *
 * Runs on the Open WebUI origin (:3000) via the same-origin reverse
 * proxy. The Designer mounts IN PLACE on the current Open WebUI page:
 * sidebar stays, main pane is replaced by the full Designer React app,
 * and the URL becomes /designer/ via pushState. Open WebUI's SvelteKit
 * router never sees the /designer/ path as a navigation, so its 404 page
 * never appears during normal use.
 *
 * Direct refresh at /designer/ (or a shared link): Open WebUI renders
 * its bare 404 (no sidebar) for the unknown route, so the script bounces
 * through '/' once with an intent flag and re-mounts — the user lands
 * back on the mounted Designer at the /designer/ URL automatically.
 *
 * No second login: the gateway resolves the actor from the verified
 * Open WebUI session (SSO Mode C through the trusted proxy).
 */
(function () {
  "use strict";

  var MOUNT_ID = "agent-designer-shell-root";
  var INTENT_KEY = "agent-designer-intent";
  var state = {
    item: null,
    mounted: false,
    available: null,
    hiddenMains: [],
    wrapper: null,
    assetsLoaded: false,
  };

  function log() {
    try {
      console.debug.apply(console, ["[agent-designer]"].concat([].slice.call(arguments)));
    } catch (e) { /* never break the host app */ }
  }

  function isDesignerRoute() {
    return location.pathname === "/designer" || location.pathname.indexOf("/designer/") === 0;
  }

  function probeDesigner() {
    if (state.available !== null) return Promise.resolve(state.available);
    return fetch("/designer/api/v1/session", {
      method: "GET",
      credentials: "same-origin",
    })
      .then(function (res) {
        // 200 (SSO or standalone session) and 401 (designer on, standalone
        // login flow) both mean the Designer is serving; 404 is the
        // flag-off rollback boundary — no designer routes exist.
        state.available = res.status !== 404;
        return state.available;
      })
      .catch(function () {
        state.available = false;
        return false;
      });
  }

  /* ------------------------------------------------------------------ *
   * Sidebar entry (valid selectors only — scan anchors/buttons for the
   * exact Workspace label and verify it lives in a sidebar-like column).
   * ------------------------------------------------------------------ */

  function findWorkspaceItem() {
    var candidates = document.querySelectorAll("a, button");
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      if ((el.textContent || "").trim() !== "Workspace") continue;
      if (el.getAttribute("data-agent-designer-entry")) continue;
      if (!isSidebarLike(el)) continue;
      return el;
    }
    return null;
  }

  function isSidebarLike(el) {
    var node = el;
    for (var depth = 0; node && depth < 8; depth++) {
      if (node.tagName === "NAV" || node.tagName === "ASIDE") return true;
      node = node.parentElement;
    }
    // Open WebUI's sidebar is a narrow left column; accept an element
    // whose rendered box is sidebar-ish.
    var box = el.getBoundingClientRect();
    return box.width > 0 && box.width <= 340 && box.left <= 60;
  }

  function findSidebarContainer() {
    // Geometry-based: the outermost narrow, tall ancestor of the
    // Workspace item is the sidebar column -- regardless of tag names.
    var workspace = findWorkspaceItem();
    if (!workspace) return null;
    var best = null;
    var node = workspace;
    for (var depth = 0; node && depth < 12; depth++) {
      var box = node.getBoundingClientRect();
      if (
        box.width > 40 &&
        box.width <= 420 &&
        box.height >= window.innerHeight * 0.55
      ) {
        best = node;
      }
      node = node.parentElement;
    }
    return best || workspace.parentElement;
  }

  function relabelClone(clone) {
    // Replace the exact 'Workspace' text node with the Designer label;
    // every class stays native so the item renders identically.
    var walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (node.nodeValue && node.nodeValue.trim() === "Workspace") {
        node.nodeValue = node.nodeValue.replace("Workspace", "Agent Designer");
        return true;
      }
    }
    return false;
  }

  function insertionPoint(workspace) {
    // Insert below the Workspace entry in the nearest VERTICAL container;
    // if the matched element sits in a horizontal row, climb until the
    // wrapping column is found so the entry stacks underneath.
    var parent = workspace.parentElement;
    if (!parent) return { container: workspace, ref: null };
    var box = parent.getBoundingClientRect();
    if (box.height >= box.width) {
      return { container: parent, ref: workspace.nextElementSibling };
    }
    var node = parent;
    for (var depth = 0; node && depth < 6; depth++) {
      var b = node.getBoundingClientRect();
      if (b.height > b.width) {
        return { container: node.parentElement, ref: node.nextElementSibling };
      }
      node = node.parentElement;
    }
    return { container: parent, ref: workspace.nextElementSibling };
  }

  function addSidebarItem() {
    if (state.item && document.contains(state.item)) return;
    var workspace = findWorkspaceItem();
    if (!workspace) return; // retried by the watcher

    // Clone the native item: identical classes and inner structure, so
    // it renders exactly like a first-class sidebar entry and cannot
    // disturb the original item's layout.
    var clone = workspace.cloneNode(true);
    clone.setAttribute("data-agent-designer-entry", "1");
    clone.removeAttribute("id");
    [].slice.call(clone.querySelectorAll("[id]")).forEach(function (n) {
      n.removeAttribute("id");
    });
    if (!relabelClone(clone)) {
      log("clone relabel failed; skipping to avoid a duplicate Workspace");
      return;
    }
    // Capture-phase click: beat any framework navigation the clone
    // inherited from the original item.
    clone.addEventListener(
      "click",
      function (event) {
        event.preventDefault();
        event.stopPropagation();
        try {
          sessionStorage.removeItem(INTENT_KEY);
        } catch (e) { /* ignore */ }
        openDesigner();
      },
      true
    );

    var point = insertionPoint(workspace);
    if (point.container) {
      point.container.insertBefore(clone, point.ref);
      state.item = clone;
      log("sidebar entry cloned below Workspace");
    }
  }

  /* ------------------------------------------------------------------ *
   * In-place mount: replace the host page's main pane with the Designer
   * React app. The sidebar column stays visible; URL becomes /designer/.
   * ------------------------------------------------------------------ */

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.type = "module";
      s.onload = resolve;
      s.onerror = function () { reject(new Error("load failed: " + src)); };
      document.head.appendChild(s);
    });
  }

  function loadStylesheet(href) {
    return new Promise(function (resolve) {
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.onload = resolve;
      link.onerror = resolve; // styles are best-effort
      document.head.appendChild(link);
    });
  }

  function hideMainPanes(sidebar) {
    var parent = sidebar.parentElement;
    if (!parent) return;
    [].slice.call(parent.children).forEach(function (child) {
      if (child === sidebar || child.id === MOUNT_ID) return;
      if (child.getAttribute("data-designer-hidden") === "1") return;
      var position = getComputedStyle(child).position;
      if (position === "fixed" || position === "absolute") return; // overlays
      child.setAttribute("data-designer-hidden", "1");
      child.setAttribute("data-designer-prev-display", child.style.display || "");
      child.style.display = "none";
      state.hiddenMains.push(child);
    });
  }

  function unhideMainPanes() {
    state.hiddenMains.forEach(function (child) {
      child.style.display = child.getAttribute("data-designer-prev-display") || "";
      child.removeAttribute("data-designer-hidden");
      child.removeAttribute("data-designer-prev-display");
    });
    state.hiddenMains = [];
  }

  function mountDesigner() {
    if (state.mounted) return Promise.resolve();
    var sidebar = findSidebarContainer();
    if (!sidebar) return Promise.resolve(); // no shell to mount into
    state.mounted = true;

    // Theme: mirror Open WebUI's dark/light class onto the token root.
    document.documentElement.setAttribute(
      "data-theme",
      document.documentElement.classList.contains("dark") ? "dark" : "light"
    );

    hideMainPanes(sidebar);
    var parent = sidebar.parentElement || document.body;
    var wrapper = document.createElement("div");
    wrapper.id = MOUNT_ID;
    wrapper.style.cssText =
      "flex:1 1 auto;min-width:0;height:100vh;position:relative;" +
      "display:flex;flex-direction:column;overflow:hidden;";
    var root = document.createElement("div");
    root.id = "root"; // the built Designer app mounts on #root
    root.style.cssText = "flex:1 1 auto;display:flex;flex-direction:column;min-height:0;";
    wrapper.appendChild(root);
    parent.appendChild(wrapper);
    state.wrapper = wrapper;
    log("shell mount created; discovering designer assets");

    return loadDesignerAssets().then(function () {
      log("designer mounted inside the Open WebUI shell");
    });
  }

  function loadDesignerAssets() {
    if (state.assetsLoaded) return Promise.resolve();
    state.assetsLoaded = true;
    // Discover the hashed asset URLs from the designer's own index
    // (served by the gateway at /designer/index.html), then load them.
    return fetch("/designer/index.html", { credentials: "same-origin" })
      .then(function (res) { return res.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var styles = [].slice
          .call(doc.querySelectorAll('link[rel="stylesheet"]'))
          .map(function (l) { return l.getAttribute("href"); })
          .filter(Boolean);
        var scripts = [].slice
          .call(doc.querySelectorAll("script[src]"))
          .map(function (s) { return s.getAttribute("src"); })
          .filter(Boolean);
        return styles
          .reduce(function (chain, href) {
            return chain.then(function () { return loadStylesheet(href); });
          }, Promise.resolve())
          .then(function () {
            return scripts.reduce(function (chain, src) {
              return chain.then(function () { return loadScript(src); });
            }, Promise.resolve());
          });
      });
  }

  function unmountDesigner() {
    if (!state.mounted) return;
    state.mounted = false;
    if (state.wrapper) {
      state.wrapper.remove();
      state.wrapper = null;
    }
    unhideMainPanes();
    log("designer unmounted (left the designer route)");
  }

  /* ------------------------------------------------------------------ *
   * Entry points
   * ------------------------------------------------------------------ */

  function openDesigner() {
    // In-place: pushState the designer URL and mount over the current
    // valid Open WebUI page. SvelteKit never routes /designer/.
    if (!isDesignerRoute()) {
      try {
        history.pushState(null, "", "/designer/");
      } catch (e) { /* URL cosmetics only */ }
    }
    mountDesigner();
  }

  function onDesigner404() {
    // The bare Open WebUI 404 for the unknown /designer/ route (direct
    // refresh or shared link): bounce through '/' once and re-mount.
    try {
      sessionStorage.setItem(INTENT_KEY, "1");
    } catch (e) { /* ignore */ }
    location.replace("/");
  }

  function watchRoute() {
    setInterval(function () {
      if (!state.available) return;
      if (isDesignerRoute()) {
        if (!state.mounted) mountDesigner(); // retry until the shell exists
      } else if (state.mounted) {
        unmountDesigner();
      }
      // Keep the sidebar entry alive across Open WebUI re-renders.
      if (!state.item || !document.contains(state.item)) addSidebarItem();
    }, 500);
  }

  function main() {
    probeDesigner().then(function (available) {
      if (!available) {
        log("designer disabled (flag-off rollback boundary); staying native");
        return;
      }

      var hadIntent = false;
      try {
        hadIntent = sessionStorage.getItem(INTENT_KEY) === "1";
      } catch (e) { /* ignore */ }

      if (isDesignerRoute()) {
        // Direct hit on /designer/: Open WebUI renders its bare 404 for
        // the unknown route (no sidebar here), so bounce through '/' and
        // re-mount there.
        onDesigner404();
        return;
      }

      addSidebarItem();
      // Open WebUI renders its sidebar asynchronously and re-renders on
      // SPA navigation: retry until present, then keep it alive.
      var tries = 0;
      var iv = setInterval(function () {
        tries += 1;
        if (state.item && document.contains(state.item)) {
          clearInterval(iv);
          return;
        }
        addSidebarItem();
        if (tries > 60) clearInterval(iv);
      }, 250);
      new MutationObserver(function () {
        if (!state.item || !document.contains(state.item)) addSidebarItem();
      }).observe(document.body, { childList: true, subtree: true });

      if (hadIntent) {
        // Returning from the /designer/ 404 bounce: mount immediately and
        // restore the designer URL.
        try {
          sessionStorage.removeItem(INTENT_KEY);
        } catch (e) { /* ignore */ }
        var mountWhenReady = setInterval(function () {
          if (findSidebarContainer()) {
            clearInterval(mountWhenReady);
            openDesigner();
          }
        }, 250);
        setTimeout(function () { clearInterval(mountWhenReady); }, 15000);
      }

      watchRoute();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
