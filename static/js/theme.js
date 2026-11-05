// Applies the saved/OS colour scheme before first paint (loaded synchronously in <head>).
(function () {
  var mode = null;
  try { mode = localStorage.getItem("rs-theme"); } catch (e) { /* storage unavailable */ }
  if (!mode) mode = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  if (mode === "dark") document.documentElement.classList.add("dark");
  document.documentElement.classList.add("rs-js");
})();
