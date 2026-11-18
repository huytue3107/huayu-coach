/* Optional focus timer for the daily task (only rendered when the plan has focus blocks).
   It lives in the browser only: it keeps its state in sessionStorage so it survives the page change between
   questions, sends nothing anywhere, and never blocks the exercises. */
(function () {
  "use strict";
  var root = document.getElementById("focus-timer");
  if (!root) { return; }
  var minutes = parseInt(root.getAttribute("data-minutes"), 10);
  if (!(minutes > 0)) { return; }
  var total = minutes * 60000;
  var KEY = "huayu.focus.v1";
  var timeEl = root.querySelector("[data-role=time]");
  var toggle = root.querySelector("[data-role=toggle]");
  var reset = root.querySelector("[data-role=reset]");
  var note = root.querySelector("[data-role=note]");
  var announce = root.querySelector("[data-role=announce]");   // always in the page, so a screen reader reliably reads what is put into it
  var ticker = null;

  function load() {
    try { var raw = window.sessionStorage.getItem(KEY); return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function save(state) {
    try {
      if (state) { window.sessionStorage.setItem(KEY, JSON.stringify(state)); } else { window.sessionStorage.removeItem(KEY); }
    } catch (e) { /* storage blocked: the timer still works on this page */ }
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function show(ms) {
    var s = Math.max(0, Math.ceil(ms / 1000));
    timeEl.textContent = pad(Math.floor(s / 60)) + ":" + pad(s % 60);
  }
  function stopTicker() { if (ticker) { window.clearInterval(ticker); ticker = null; } }

  // state: null (idle) | {end: epoch ms} (running) | {left: ms} (paused)
  var state = load();
  if (state && typeof state !== "object") { state = null; }

  function render() {
    note.hidden = true;
    if (announce) { announce.textContent = ""; }
    if (state && typeof state.end === "number") {
      var left = state.end - Date.now();
      if (left <= 0) { finish(); return; }
      show(left);
      toggle.textContent = toggle.getAttribute("data-pause");
      reset.hidden = false;
      if (!ticker) { ticker = window.setInterval(render, 1000); }
    } else if (state && typeof state.left === "number") {
      stopTicker();
      show(state.left);
      toggle.textContent = toggle.getAttribute("data-start");
      reset.hidden = false;
    } else {
      stopTicker();
      show(total);
      toggle.textContent = toggle.getAttribute("data-start");
      reset.hidden = true;
    }
  }
  function finish() {
    stopTicker();
    state = null;
    save(null);
    show(total);
    toggle.textContent = toggle.getAttribute("data-start");
    reset.hidden = true;
    note.hidden = false;
    if (announce) { announce.textContent = note.textContent; }
  }

  toggle.addEventListener("click", function () {
    if (state && typeof state.end === "number") {
      state = { left: Math.max(0, state.end - Date.now()) };
    } else if (state && typeof state.left === "number") {
      state = { end: Date.now() + state.left };
    } else {
      state = { end: Date.now() + total };
    }
    save(state);
    render();
  });
  reset.addEventListener("click", function () {
    state = null;
    save(null);
    render();
  });
  render();
})();
