/* NG Runner's board: keeps the page current and stops double clicks. No frameworks, nothing
   loaded from outside the app, no inline handlers (the page's CSP refuses them). */
"use strict";

(function () {
  var REFRESH_MS = 60 * 1000;

  // Reload the page each minute so the board stays current, but never while Jim is typing
  // or has a panel open: losing a half-typed answer would be worse than a stale minute.
  function busy() {
    var active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) return true;
    if (document.querySelector("details[open], .btn.is-busy")) return true;
    var fields = document.querySelectorAll("input[type=text], input:not([type])");
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].value) return true;
    }
    return false;
  }

  window.setInterval(function () {
    if (document.visibilityState === "visible" && !busy()) window.location.reload();
  }, REFRESH_MS);

  // One click, one action: a second click on a form already sent is ignored.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (form.dataset.sent) {
      event.preventDefault();
      return;
    }
    form.dataset.sent = "1";
    var button = event.submitter || form.querySelector("button[type=submit], button:not([type])");
    if (button) button.classList.add("is-busy");
  });

  // Coming back to the page with the Back button shows it as it is now, never a stale copy
  // with its buttons still marked as sent.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) window.location.reload();
  });

  // Opening a panel (Add a job) puts the cursor in its box, ready to type.
  document.addEventListener("toggle", function (event) {
    var panel = event.target;
    if (!panel.open) return;
    var field = panel.querySelector("input:not([type=hidden])");
    if (field) field.focus();
  }, true);

  // Escape closes an open panel (Add a job) and puts focus back on its button.
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    var open = document.querySelector("details[open]");
    if (!open) return;
    open.removeAttribute("open");
    var summary = open.querySelector("summary");
    if (summary) summary.focus();
  });
})();
