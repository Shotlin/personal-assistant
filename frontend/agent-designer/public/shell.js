/* Agent Designer shell integration (injected into Open WebUI pages).
 *
 * Runs on the Open WebUI origin (:3000) via the same-origin reverse
 * proxy. Responsibilities:
 *  1. Add an "Agent Designer" entry to Open WebUI's sidebar, directly
 *     after the Workspace entry — styled to match the native items.
 *  2. On /designer/ routes, mount the existing full Agent Designer React
 *     application inside the Open WebUI application shell (sidebar and
 *     header stay visible; no iframe; no redirect to :8787).
 *  3. Stay out of the way when the Designer is disabled (flag-off
 *     rollback): the sidebar item hides itself when the API is absent.
 *
 * No second login: the gateway resolves the actor from the verified
 * Open WebUI session (SSO Mode C through the trusted proxy).
 */
(function () {
  "use strict";

  var MOUNT_ID = "agent-designer-shell-root";
  var HIDDEN_ATTR = "data-designer-hidden";
  var state = { sidebarItem: null, mounted: false, designerAvailable: null };

  function log() {
    try {
      console.debug.apply(console, ["[agent-designer]"].concat([].slice.call(arguments)));
    } catch (e) { /* never break the host app */ }
  }

  function isDesignerRoute() {
    return location.pathname === "/designer" || location.pathname.indexOf("/designer/") === 0;
  }

  function probeDesigner() {
    if (state.designerAvailable !== null) return Promise.resolve(state.designerAvailable);
    return fetch("/designer/api/v1/session", {
      method: "GET",
      credentials: "same-origin",
    })
      .then(function (res) {
        // 200 (SSO/standalone session) and 401 (designer on, standalone
        // login flow) both mean the Designer is serving; 404 means the
        // flag-off rollback boundary — no designer routes exist.
        state.designerAvailable = res.status !== 404;
        return state.designerAvailable;
      })
      .catch(function () {
        state.designerAvailable = false;
        return false;
      });
  }

  /* ------------------------------------------------------------------ *
   * Sidebar entry
   * ------------------------------------------------------------------ */

  function findWorkspaceItem() {
    var candidates = document.querySelectorAll(
      "nav a, nav button, aside a, aside button, [data-sveltekit- prefetch] a"
    );
    for (var i = 0; i < candidates.length; i++) {
      var el = candidates[i];
      var text = (el.textContent || "").trim();
      if (text === "Workspace" || text === "Knowledge" || text === "Skills") {
        return el;
      }
    }
    return null;
  }

  function findSidebarContainer(item) {
    var el = item;
    while (el && el.parentElement) {
      var parent = el.parentElement;
      if (parent.tagName === "NAV" || parent.tagName === "ASIDE") return parent;
      el = parent;
    }
    return null;
  }

  function addSidebarItem() {
    if (state.sidebarItem && document.contains(state.sidebarItem)) return;
    var workspace = findWorkspaceItem();
    if (!workspace) return; // retried by the observer
    var sidebar = findSidebarContainer(workspace);
    if (!sidebar) return;

    var item = document.createElement(workspace.tagName === "A" ? "a" : "button");
    item.type = "button";
    item.setAttribute("data-agent-designer-entry", "1");
    item.className = workspace.className; // native styling
    item.style.cursor = "pointer";
    item.style.width = "100%";
    item.style.textAlign = "inherit";
    // Mirror the native item's inner structure (icon + label) with a
    // neutral inline icon; text label always accompanies the icon.
    var iconClass = "";
    var svg = workspace.querySelector("svg");
    if (svg) iconClass = svg.getAttribute("class") || "";
    item.innerHTML =
      '<span class="' + iconClass + '" style="display:inline-flex;margin-right:0.5rem;">' +
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
      location.assign("/designer/");
    });
    // Insert directly after the Workspace entry (preferred navigation
    // position: ... Workspace, Agent Designer).
    var anchor = workspace;
    var list = workspace.parentElement;
    while (anchor && list && anchor.nextElementSibling) {
      var nextText = (anchor.nextElementSibling.textContent || "").trim();
      if (nextText === "Knowledge" || nextText === "Prompts" || nextText === "Skills" ||
          nextText === "Models" || nextText === "Notes") {
        anchor = anchor.nextElementSibling;
      } else break;
    }
    if (anchor && anchor.parentElement) {
      anchor.parentElement.insertBefore(item, anchor.nextElementSibling);
    } else if (sidebar) {
      sidebar.appendChild(item);
    }
    state.sidebarItem = item;
    log("sidebar entry added");
  }

  /* ------------------------------------------------------------------ *
   * In-shell mount: render the Designer React app inside the Open WebUI
   * layout (sidebar + header visible; no iframe).
   * ------------------------------------------------------------------ */

  function findMainContentSibling(sidebar) {
    var layout = sidebar.parentElement;
    while (layout && layout !== document.body) {
      var children = [].slice.call(layout.children);
      if (children.indexOf(sidebar) !== -1 && children.length > 1) {
        for (var i = 0; i < children.length; i++) {
          if (children[i] !== sidebar) return { layout: layout, main: children[i] };
        }
      }
      layout = layout.parentElement;
    }
    return null;
  }

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

  function mountDesigner() {
    if (state.mounted) return Promise.resolve();
    state.mounted = true;
    document.documentElement.setAttribute("data-designer-route", "1");

    return fetch("/designer/", { credentials: "same-origin" })
      .then(function (res) { return res.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var styles = [].slice
          .call(doc.querySelectorAll('link[rel="stylesheet"]'))
          .map(function (l) { return l.getAttribute("href"); })
          .filter(Boolean);
        var scripts = [].slice
          .call(doc.querySelectorAll("script[type=module][src], script[src]"))
          .map(function (s) { return s.getAttribute("src"); })
          .filter(Boolean);
        var theme = document.documentElement.classList.contains("dark")
          ? "dark"
          : "light";
        document.documentElement.setAttribute("data-theme", theme);

        return Promise.all(styles.map(loadStylesheet)).then(function () {
          // Hide Open WebUI's main content, keep sidebar + header.
          var nav =
            document.querySelector("nav") ||
            document.querySelector("aside");
          var target = null;
          if (nav) {
            var found = findMainContentSibling(nav);
            if (found && found.main) {
              found.main.setAttribute(HIDDEN_ATTR, "1");
              found.main.style.display = "none";
              target = found.layout;
            }
          }
          var wrapper = document.createElement("div");
          wrapper.id = MOUNT_ID;
          wrapper.style.cssText =
            "flex:1 1 auto;min-width:0;min-height:100vh;position:relative;" +
            "display:flex;flex-direction:column;background:var(--bg,#09090B);";
          var root = document.createElement("div");
          root.id = "root"; // the built Designer app mounts on #root
          root.style.cssText = "flex:1 1 auto;display:flex;flex-direction:column;min-height:0;";
          wrapper.appendChild(root);
          if (target) {
            target.appendChild(wrapper);
          } else {
            // Fallback: full-viewport mount (same origin, still no iframe).
            document.body.appendChild(wrapper);
          }
          log("designer shell mounted; loading", scripts.length, "asset(s)");
          // Asset URLs in the built index are absolute /designer/... paths
          // (vite base), so they resolve on this origin via the proxy.
          return scripts
            .reduce(function (chain, src) {
              return chain.then(function () { return loadScript(src); });
            }, Promise.resolve())
            .catch(function (err) { log("asset load error", err && err.message); });
        });
      });
  }

  /* ------------------------------------------------------------------ */

  function main() {
    probeDesigner().then(function (available) {
      if (!available) {
        log("designer disabled (flag-off rollback boundary); staying native");
        return;
      }
      if (isDesignerRoute()) {
        mountDesigner();
      } else {
        addSidebarItem();
        // Open WebUI re-renders its sidebar (SPA navigation): re-add
        // whenever the workspace item appears without ours.
        var observer = new MutationObserver(function () {
          if (!state.sidebarItem || !document.contains(state.sidebarItem)) {
            addSidebarItem();
          }
        });
        observer.observe(document.body, { childList: true, subtree: true });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", main);
  } else {
    main();
  }
})();
