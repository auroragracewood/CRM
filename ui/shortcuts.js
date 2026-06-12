/* Global keyboard shortcuts.
 *
 * Vim/Gmail style: single keys when no input is focused. Press '?' to see
 * the help panel. 'g' starts a navigation chord (g d = dashboard, g c =
 * contacts, etc.) similar to Gmail.
 *
 * Press Esc to close help / cancel chord.
 */
(function () {
  // Don't fire shortcuts while typing in inputs/textareas.
  function isTyping() {
    const a = document.activeElement;
    if (!a) return false;
    const t = a.tagName;
    return t === "INPUT" || t === "TEXTAREA" || t === "SELECT" ||
           a.isContentEditable;
  }

  const NAV = {
    d: { path: "/",            label: "Dashboard"  },
    c: { path: "/contacts",    label: "Contacts"   },
    o: { path: "/companies",   label: "Companies"  },
    p: { path: "/pipelines",   label: "Pipelines"  },
    t: { path: "/tasks",       label: "Tasks"      },
    f: { path: "/forms",       label: "Forms"      },
    s: { path: "/segments",    label: "Segments"   },
    r: { path: "/reports",     label: "Reports"    },
    g: { path: "/tags",        label: "Tags"       },
    n: { path: "/connectors",  label: "Connectors" },
    a: { path: "/audit",       label: "Audit log"  },
    x: { path: "/settings",    label: "Settings"   },
    m: { path: "/me",          label: "My profile" },
  };

  const SINGLE = {
    "/": () => {
      const inp = document.querySelector(".topsearch input[type=search]");
      if (inp) { inp.focus(); inp.select(); }
    },
    "?": showHelp,
  };

  let chordActive = false;
  let chordTimeout = null;

  function showHelp() {
    if (document.querySelector(".kbd-help")) return;
    const overlay = document.createElement("div");
    overlay.className = "kbd-help-overlay";
    overlay.addEventListener("click", hideHelp);
    const panel = document.createElement("div");
    panel.className = "kbd-help";
    panel.innerHTML = (
      '<h3>Keyboard shortcuts</h3>'
      + '<div class="kbd-grid">'
      + '<div class="kbd-group"><h4>Navigate</h4><table>'
      + Object.entries(NAV).map(([k, v]) =>
          `<tr><td><kbd>g</kbd> <kbd>${k}</kbd></td><td>${v.label}</td></tr>`
        ).join("")
      + '</table></div>'
      + '<div class="kbd-group"><h4>Actions</h4><table>'
      + '<tr><td><kbd>/</kbd></td><td>Focus search</td></tr>'
      + '<tr><td><kbd>Ctrl</kbd> <kbd>K</kbd></td><td>Quick search (same)</td></tr>'
      + '<tr><td><kbd>?</kbd></td><td>This panel</td></tr>'
      + '<tr><td><kbd>Esc</kbd></td><td>Close / cancel chord</td></tr>'
      + '</table></div>'
      + '</div>'
      + '<p class="kbd-hint">Shortcuts ignore inputs — typing into a field disables them automatically.</p>'
    );
    document.body.appendChild(overlay);
    document.body.appendChild(panel);
  }

  function hideHelp() {
    document.querySelectorAll(".kbd-help, .kbd-help-overlay").forEach(e => e.remove());
  }

  function clearChord() {
    chordActive = false;
    if (chordTimeout) clearTimeout(chordTimeout);
    chordTimeout = null;
    document.body.classList.remove("kbd-chord-active");
  }

  document.addEventListener("keydown", (e) => {
    if (isTyping()) return;
    // Ignore modified keys (Ctrl/Alt/Meta) unless the shortcut explicitly
    // wants them (Ctrl+K is handled by topnav.js separately).
    if (e.metaKey || e.ctrlKey || e.altKey) return;

    if (e.key === "Escape") {
      hideHelp();
      clearChord();
      return;
    }

    // Help-panel keys
    if (document.querySelector(".kbd-help")) {
      // Any key dismisses the panel.
      hideHelp();
      return;
    }

    if (chordActive) {
      e.preventDefault();
      const target = NAV[e.key.toLowerCase()];
      clearChord();
      if (target) location.href = target.path;
      return;
    }

    if (e.key === "g") {
      e.preventDefault();
      chordActive = true;
      document.body.classList.add("kbd-chord-active");
      chordTimeout = setTimeout(clearChord, 1200);
      return;
    }

    const fn = SINGLE[e.key];
    if (fn) { e.preventDefault(); fn(); }
  });
})();
