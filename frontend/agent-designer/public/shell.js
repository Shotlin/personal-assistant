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
    var workspace = findWorkspaceItem();
    if (!workspace) return null;
    var node = workspace;
    for (var depth = 0; node && depth < 10; depth++) {
      if (node.tagName === "NAV" || node.tagName === "ASIDE") return node;
      node = node.parentElement;
    }
    return null;
  }

  function addSidebarItem() {
    if (state.item && document.contains(state.item)) return;
    var workspace = findWorkspaceItem();
    if (!workspace) return; // retried by the watcher

    var item = document.createElement(workspace.tagName === "A" ? "a" : "button");
    item.type = "button";
    item.setAttribute("data-agent-designer-entry", "1");
    item.className = workspace.className; // native styling
    item.style.cursor = "pointer";
    item.style.width = "100%";
    item.style.textAlign = "inherit";
    item.style.background = "transparent";
    item.style.border = "0";
    item.title = "Agent Designer";
    // Mirror the native item's inner structure (icon + label) with a
    // neutral inline icon; the text label always accompanies the icon.
    var svg = workspace.querySelector("svg");
    var iconClass = svg ? svg.getAttribute("class") || "" : "";
    var iconStyle = svg ? svg.getAttribute("style") || "" : "";
    item.innerHTML =
      '<span class="' + iconClass + '" style="display:inline-flex;margin-right:0.5rem;' +
      iconStyle + '">' +
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round" width="16" height="16" aria-hidden="true">' +
      '<rect x="3" y="3" width="7" height="7" rx="1"></rect>' +
      '<rect x="14" y="3" width="7" height="7" rx="1"></rect>' +
      '<rect x="3" y="14" width="7" height="7" rx="1"></rect>' +
      '<path d="M17.5 14v7M14 17.5h7"></path></svg></span>' +
      '<span>Agent Designer</span>';
    item.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      try {
        sessionStorage.removeItem(INTENT_KEY);
      } catch (e) { /* ignore */ }
      openDesigner();
    });
    // Insert directly below the Workspace entry.
    if (workspace.parentElement) {
      workspace.parentElement.insertBefore(item, workspace.nextElementSibling);
    }
    state.item = item;
    log("sidebar entry added below Workspace");
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
      if (!isDesignerRoute()) {
        if (state.mounted) unmountDesigner();
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
