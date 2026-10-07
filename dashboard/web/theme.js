/* Runs in <head>, before the page paints: the saved theme (light or dark), else the system's. app.js owns the toggle. */
"use strict";
(() => {
  let t = null;
  try { t = localStorage.getItem("lab-console-theme"); } catch (e) {}
  if (t !== "light" && t !== "dark") t = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
})();
