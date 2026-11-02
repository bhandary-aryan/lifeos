/* Make Chart.js follow the active LifeOS theme (it otherwise uses its own dim grey, unreadable on the dark theme).
   Charts are created once per page load; the theme toggle reloads pages that contain charts, so colours stay in sync.

   DaisyUI exposes colours as oklch(); rather than parse CSS colour syntax by hand, let the browser convert any
   format to plain sRGB by painting one pixel. */
(function () {
  if (!window.Chart) return;
  function toRgb(css) {
    try {
      var c = document.createElement("canvas"); c.width = c.height = 1;
      var g = c.getContext("2d", { willReadFrequently: true });
      g.fillStyle = "#000"; g.fillStyle = css;             // an unparseable value leaves the previous colour in place
      g.fillRect(0, 0, 1, 1);
      var d = g.getImageData(0, 0, 1, 1).data;
      return d[0] + "," + d[1] + "," + d[2];
    } catch (e) { return null; }
  }
  var cs = getComputedStyle(document.body);
  var rgb = toRgb(cs.color);
  if (!rgb) return;                                         // keep Chart.js defaults rather than guess
  Chart.defaults.color = "rgba(" + rgb + ",0.9)";           // tick labels, legends
  Chart.defaults.borderColor = "rgba(" + rgb + ",0.15)";    // grid lines: visible but quiet
  Chart.defaults.font.family = cs.fontFamily;

  // Series colours come from the active theme (the dark theme's primary is much lighter than the light theme's),
  // so bars and lines keep enough contrast against the page in both. Falls back to fixed colours if oklch() is unsupported.
  function token(name, fallback) {
    if (!(window.CSS && CSS.supports && CSS.supports("color", "oklch(0.5 0.1 100)"))) return fallback;
    var raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    var v = raw && toRgb("oklch(" + raw + ")");
    return v ? "rgb(" + v + ")" : fallback;
  }
  window.LifeosChart = {
    colors: { primary: token("--p", "#0f766e"), secondary: token("--s", "#6366f1"), accent: token("--a", "#7c3aed"),
              success: token("--su", "#16a34a"), error: token("--er", "#dc2626") },
    countScale: { y: { beginAtZero: true, ticks: { precision: 0 } } }          // whole numbers for "how many" charts
  };
})();
