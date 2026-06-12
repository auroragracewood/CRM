/* Theme toggle.
 *
 * 3-state cycle:  auto (OS pref)  ->  light  ->  dark  ->  auto
 *
 * Stored in localStorage as `crm_theme = "auto" | "light" | "dark"`.
 * `auto` removes the body data-theme attribute, letting the
 * @media (prefers-color-scheme) rules in styles.css decide.
 * `light` / `dark` set `body[data-theme]` to override.
 *
 * The button label shows the *target* of the next press, not the current
 * mode — so "switch to dark" means "you're currently auto or light, click
 * me to flip to dark."
 */
(function () {
  const KEY = "crm_theme";
  const STATES = ["auto", "light", "dark"];
  const ICONS = { auto: "◐", light: "☀", dark: "☾" };
  const TITLES = {
    auto:  "Theme: auto (matches your OS). Click for light.",
    light: "Theme: light. Click for dark.",
    dark:  "Theme: dark. Click for auto.",
  };

  function apply(mode) {
    if (mode === "light" || mode === "dark") {
      document.body.setAttribute("data-theme", mode);
    } else {
      document.body.removeAttribute("data-theme");
    }
    const btn = document.querySelector(".theme-toggle");
    if (btn) {
      btn.textContent = ICONS[mode] || ICONS.auto;
      btn.title = TITLES[mode] || TITLES.auto;
      btn.setAttribute("data-theme-state", mode);
    }
  }

  function cycle() {
    const cur = localStorage.getItem(KEY) || "auto";
    const idx = STATES.indexOf(cur);
    const next = STATES[(idx + 1) % STATES.length];
    localStorage.setItem(KEY, next);
    apply(next);
  }

  // Initial: apply stored mode (set this as early as possible so there's
  // no flash of wrong-theme content).
  apply(localStorage.getItem(KEY) || "auto");

  document.addEventListener("click", (e) => {
    if (e.target.closest(".theme-toggle")) {
      e.preventDefault();
      cycle();
    }
  });
})();
