/* Page theme: accent colour, light/dark mode, background tint.
 * The server renders the signed-in user's saved theme into <html> before first paint. This file
 * (1) applies the browser's own copy on pages where nobody is signed in (e.g. the login page),
 * and (2) powers the Theme menu: apply instantly, remember in this browser, save to the account.
 * The colour maths mirrors app/theme.py exactly; tests/test_theme.py checks that they agree. */
(function (root) {
  "use strict";

  var MODES = ["auto", "light", "dark"];
  var TINTS = [0, 8, 16];
  var DEFAULT = { a: null, m: "auto", t: 0 };
  var KEY = "pokemon.theme";
  var HEX = /^#[0-9a-fA-F]{6}$/;
  var WHITE = [255, 255, 255], BLACK = [0, 0, 0], INK_DARK = [0x11, 0x14, 0x18], DARK_SURFACE = [0x1a, 0x22, 0x2b];

  function rgb(hex) { return [parseInt(hex.substr(1, 2), 16), parseInt(hex.substr(3, 2), 16), parseInt(hex.substr(5, 2), 16)]; }
  function hex(c) { return "#" + c.map(function (v) { return (v < 16 ? "0" : "") + v.toString(16); }).join(""); }
  function channel(c) { c = c / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); }
  function luminance(c) { return 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]); }
  function contrast(a, b) { var hi = Math.max(a, b), lo = Math.min(a, b); return (hi + 0.05) / (lo + 0.05); }
  function mix(c, target, t) {
    return c.map(function (v, i) { return Math.floor(v + (target[i] - v) * t + 0.5); });
  }
  function inkFor(c) {
    var lum = luminance(c);
    return contrast(1.0, lum) >= contrast(lum, luminance(INK_DARK)) ? WHITE : INK_DARK;
  }
  function inkContrast(c) {
    var lum = luminance(c);
    return Math.max(contrast(1.0, lum), contrast(lum, luminance(INK_DARK)));
  }
  function lightVariant(c) {
    var cand = c;
    for (var i = 0; i < 13; i++) {
      cand = mix(c, BLACK, i * 0.05);
      if (contrast(luminance(cand), 1.0) >= 3.0 && inkContrast(cand) >= 4.5) break;
    }
    return cand;
  }
  function darkVariant(c) {
    var target = luminance(DARK_SURFACE), cand = c;
    for (var i = 0; i < 17; i++) {
      cand = mix(c, WHITE, i * 0.05);
      if (contrast(luminance(cand), target) >= 4.5 && inkContrast(cand) >= 4.5) break;
    }
    return cand;
  }
  function derive(accent) {
    var base = rgb(accent), light = lightVariant(base), dark = darkVariant(base);
    return { light: hex(light), light_ink: hex(inkFor(light)), dark: hex(dark), dark_ink: hex(inkFor(dark)) };
  }

  function normalize(raw) {
    var out = { a: DEFAULT.a, m: DEFAULT.m, t: DEFAULT.t };
    if (!raw || typeof raw !== "object") return out;
    if (typeof raw.a === "string" && HEX.test(raw.a)) out.a = raw.a.toLowerCase();
    if (MODES.indexOf(raw.m) >= 0) out.m = raw.m;
    if (typeof raw.t === "number" && raw.t % 1 === 0 && raw.t >= 0 && raw.t < TINTS.length) out.t = raw.t;
    return out;
  }

  var VARS = ["--accent-l", "--accent-ink-l", "--accent-d", "--accent-ink-d", "--tint"];

  function apply(state) {
    var el = document.documentElement;
    el.setAttribute("data-mode", state.m);
    VARS.forEach(function (v) { el.style.removeProperty(v); });
    if (state.a) {
      var c = derive(state.a);
      el.style.setProperty("--accent-l", c.light);
      el.style.setProperty("--accent-ink-l", c.light_ink);
      el.style.setProperty("--accent-d", c.dark);
      el.style.setProperty("--accent-ink-d", c.dark_ink);
    }
    if (TINTS[state.t]) el.style.setProperty("--tint", TINTS[state.t] + "%");
  }

  function load() {
    try { return normalize(JSON.parse(localStorage.getItem(KEY))); } catch (e) { return null; }
  }
  function remember(state) {
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* storage blocked */ }
  }

  var api = { derive: derive, normalize: normalize };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; return; }
  if (typeof document === "undefined") return;

  // Signed out (the server sets data-themed when it has applied an account's theme): use this browser's copy.
  if (!document.documentElement.hasAttribute("data-themed")) {
    var mine = load();
    if (mine) apply(mine);
  }

  // ---- Theme menu ----
  document.addEventListener("DOMContentLoaded", function () {
    var menu = document.querySelector("[data-theme-menu]");
    if (!menu) return;
    var state;
    try { state = normalize(JSON.parse(menu.getAttribute("data-state"))); } catch (e) { state = normalize(null); }
    if (!document.documentElement.hasAttribute("data-themed")) state = load() || state;

    var saveUrl = menu.getAttribute("data-save-url");
    var token = (document.querySelector('meta[name="csrf-token"]') || {}).content;
    var modeBtns = menu.querySelectorAll("[data-mode]");
    var tintBtns = menu.querySelectorAll("[data-tint]");
    var swatches = menu.querySelectorAll("[data-accent]");
    var custom = menu.querySelector("[data-custom-accent]");
    var customLabel = custom ? custom.parentNode : null;
    var timer = null;

    function render() {
      modeBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-mode") === state.m)); });
      tintBtns.forEach(function (b) { b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-tint")) === state.t)); });
      var preset = false;
      swatches.forEach(function (b) {
        var on = (b.getAttribute("data-accent") || null) === state.a;
        preset = preset || on;
        b.setAttribute("aria-pressed", String(on));
      });
      if (customLabel) {
        if (!preset && state.a) { customLabel.setAttribute("data-active", ""); customLabel.style.setProperty("--sw", state.a); custom.value = state.a; }
        else { customLabel.removeAttribute("data-active"); customLabel.style.removeProperty("--sw"); }
      }
    }

    function save() {
      remember(state);
      if (!saveUrl) return;
      clearTimeout(timer);
      timer = setTimeout(function () {
        fetch(saveUrl, {
          method: "POST", credentials: "same-origin", keepalive: true,
          headers: { "Content-Type": "application/json", "X-CSRFToken": token || "" },
          body: JSON.stringify(state)
        }).catch(function () { /* the browser copy still applies */ });
      }, 350);
    }

    function change(patch) {
      state = normalize(Object.assign({}, state, patch));
      apply(state); render(); save();
    }

    modeBtns.forEach(function (b) { b.addEventListener("click", function () { change({ m: b.getAttribute("data-mode") }); }); });
    tintBtns.forEach(function (b) { b.addEventListener("click", function () { change({ t: Number(b.getAttribute("data-tint")) }); }); });
    swatches.forEach(function (b) { b.addEventListener("click", function () { change({ a: b.getAttribute("data-accent") || null }); }); });
    if (custom) custom.addEventListener("input", function () { change({ a: custom.value }); });
    var reset = menu.querySelector("[data-theme-reset]");
    if (reset) reset.addEventListener("click", function () { change({ a: null, m: "auto", t: 0 }); });

    render();
  });
})(this);
