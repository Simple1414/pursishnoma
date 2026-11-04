/* ResearchSurvey front-end behaviour (Alpine components + Chart.js rendering).
 * Loaded with `defer` before Alpine so components register on `alpine:init`.
 * No inline scripts are used (strict Content-Security-Policy).
 */
(function () {
  "use strict";

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--rs-${name}`).trim();
  const rgb = (name, a = 1) => `rgba(${css(name).split(/\s+/).join(",")},${a})`;
  const csrf = () => {
    const el = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (el) return el.value;
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  };

  /* ---------------------------------------------------------------- theme */
  function setTheme(mode) {
    document.documentElement.classList.toggle("dark", mode === "dark");
    try { localStorage.setItem("rs-theme", mode); } catch (e) { /* ignore */ }
    document.dispatchEvent(new CustomEvent("rs:theme"));
  }

  /* ---------------------------------------------------------------- charts */
  const palette = () => [rgb("royal"), rgb("gold"), rgb("success"), rgb("navy"), rgb("warning"), rgb("danger"), "#7C93C3", "#A58A4F", "#3C6E71", "#9A6FB0"];
  const registry = new Map();

  function buildConfig(spec) {
    const colors = palette();
    const grid = rgb("border");
    const text = rgb("muted");
    const base = {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 450 },
      plugins: {
        legend: { display: false, labels: { color: text, boxWidth: 12, font: { family: "Inter Variable, Inter, system-ui" } } },
        tooltip: { backgroundColor: "rgba(16,42,67,0.95)", padding: 10, cornerRadius: 8, titleFont: { weight: "600" } },
      },
      scales: {
        x: { grid: { color: grid, drawBorder: false }, ticks: { color: text, font: { size: 11 } } },
        y: { grid: { color: grid, drawBorder: false }, ticks: { color: text, font: { size: 11 }, precision: 0 }, beginAtZero: true },
      },
    };
    const wrap = (label) => (typeof label === "string" && label.length > 38 ? label.slice(0, 36) + "…" : label);
    const labels = (spec.labels || []).map(wrap);
    let type = spec.type;
    let datasets;
    if (type === "pie") {
      datasets = [{ data: spec.values, backgroundColor: colors, borderColor: rgb("surface"), borderWidth: 2 }];
      base.plugins.legend.display = true;
      base.plugins.legend.position = "bottom";
      delete base.scales;
      type = "doughnut";
      base.cutout = "58%";
    } else if (type === "stacked" || type === "grouped") {
      datasets = (spec.datasets || []).map((d, i) => ({ label: wrap(d.label), data: d.values, backgroundColor: colors[i % colors.length], borderRadius: 4, maxBarThickness: 36 }));
      base.plugins.legend.display = true;
      base.plugins.legend.position = "bottom";
      if (type === "stacked") {
        base.indexAxis = "y";
        base.scales.x.stacked = true;
        base.scales.y.stacked = true;
      }
      type = "bar";
    } else if (type === "line") {
      datasets = [{ data: spec.values, label: spec.dataset_label || "", borderColor: rgb("royal"), backgroundColor: rgb("royal", 0.12), fill: true, tension: 0.3, pointRadius: 3 }];
    } else {
      const horizontal = !!spec.horizontal;
      datasets = [{ data: spec.values, label: spec.dataset_label || "n", backgroundColor: type === "histogram" ? rgb("gold", 0.85) : rgb("royal", 0.88), hoverBackgroundColor: rgb("gold"), borderRadius: 6, maxBarThickness: 44, categoryPercentage: type === "histogram" ? 1 : 0.8, barPercentage: type === "histogram" ? 0.98 : 0.8 }];
      if (horizontal) base.indexAxis = "y";
      type = "bar";
    }
    return { type, data: { labels, datasets }, options: base };
  }

  function renderCharts(root = document) {
    if (!window.Chart) return;
    const source = root.querySelector("#rs-charts");
    if (!source) return;
    let specs = [];
    try { specs = JSON.parse(source.textContent); } catch (e) { return; }
    specs.forEach((spec) => {
      const canvas = document.getElementById(spec.id);
      if (!canvas) return;
      if (registry.has(spec.id)) registry.get(spec.id).destroy();
      registry.set(spec.id, new window.Chart(canvas, buildConfig(spec)));
    });
  }

  function exportChart(id, filename) {
    const chart = registry.get(id);
    if (!chart) return;
    const link = document.createElement("a");
    link.download = (filename || id) + ".png";
    // white background for documents
    const c = chart.canvas;
    const tmp = document.createElement("canvas");
    tmp.width = c.width; tmp.height = c.height;
    const ctx = tmp.getContext("2d");
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, tmp.width, tmp.height);
    ctx.drawImage(c, 0, 0);
    link.href = tmp.toDataURL("image/png");
    link.click();
  }

  document.addEventListener("DOMContentLoaded", () => renderCharts());
  document.addEventListener("rs:theme", () => renderCharts());

  /* ---------------------------------------------------------------- Alpine components */
  document.addEventListener("alpine:init", () => {
    const Alpine = window.Alpine;

    Alpine.store("ui", {
      sidebar: false,
      dark: document.documentElement.classList.contains("dark"),
      toggleTheme() { this.dark = !this.dark; setTheme(this.dark ? "dark" : "light"); },
    });

    Alpine.data("toasts", () => ({
      items: [],
      init() {
        const src = document.getElementById("rs-messages");
        if (!src) return;
        let list = [];
        try { list = JSON.parse(src.textContent); } catch (e) { list = []; }
        list.forEach((m, i) => setTimeout(() => this.push(m.text, m.level), 120 * i));
      },
      push(text, level = "info") {
        const id = Date.now() + Math.random();
        this.items.push({ id, text, level, show: true });
        setTimeout(() => this.dismiss(id), level === "error" ? 9000 : 5500);
      },
      dismiss(id) {
        const t = this.items.find((x) => x.id === id);
        if (t) t.show = false;
        setTimeout(() => { this.items = this.items.filter((x) => x.id !== id); }, 300);
      },
    }));

    Alpine.data("copyText", (text) => ({
      copied: false,
      async copy() {
        try { await navigator.clipboard.writeText(text); this.copied = true; setTimeout(() => (this.copied = false), 1800); }
        catch (e) { window.prompt("Copy:", text); }
      },
    }));

    Alpine.data("chartExport", () => ({ save(id, name) { exportChart(id, name); } }));

    Alpine.data("selection", () => ({
      ids: [],
      toggle(id, on) { id = String(id); if (on) { if (!this.ids.includes(id)) this.ids.push(id); } else { this.ids = this.ids.filter((x) => x !== id); } },
      query() { return this.ids.map((id) => "ids=" + encodeURIComponent(id)).join("&"); },
    }));

    Alpine.data("confirmSubmit", (message) => ({
      ask(e) { if (!window.confirm(message)) e.preventDefault(); },
    }));

    /* Survey stepper: one section per step, server autosave on every step. */
    Alpine.data("survey", (cfg) => ({
      step: 0,
      total: cfg.total || 1,
      saving: false,
      saved: false,
      offline: false,
      submitting: false,
      answered: 0,
      questions: cfg.questions || 0,
      errorStep: cfg.errorStep ?? null,
      init() {
        if (this.errorStep !== null && this.errorStep >= 0) this.step = this.errorStep;
        this.countAnswered();
        this.$el.addEventListener("change", () => { this.countAnswered(); this.saved = false; });
        this.$el.addEventListener("input", () => { this.saved = false; });
        window.addEventListener("online", () => { this.offline = false; this.save(); });
        window.addEventListener("offline", () => { this.offline = true; });
        this._timer = setInterval(() => { if (!this.saved && !cfg.preview) this.save(); }, 45000);
      },
      get progress() { return this.questions ? Math.round((100 * this.answered) / this.questions) : 0; },
      countAnswered() {
        const names = new Set();
        this.$el.querySelectorAll("input, textarea, select").forEach((el) => {
          if (!el.name || !el.name.startsWith("q_")) return;
          const qid = el.name.split("_")[1];
          if (((el.type === "radio" || el.type === "checkbox") && el.checked) || ((el.type !== "radio" && el.type !== "checkbox") && el.value && el.value.trim() !== "")) names.add(qid);
        });
        this.answered = names.size;
      },
      async save() {
        if (cfg.preview || this.saving) return true;
        this.saving = true;
        try {
          const data = new FormData(this.$refs.form);
          const res = await fetch(cfg.saveUrl, { method: "POST", body: data, headers: { "X-CSRFToken": csrf() }, credentials: "same-origin" });
          this.saved = res.ok;
          this.offline = false;
          return res.ok;
        } catch (e) {
          this.offline = true;
          return false;
        } finally {
          this.saving = false;
        }
      },
      validateStep() {
        const section = this.$el.querySelector(`[data-step="${this.step}"]`);
        if (!section) return true;
        let firstInvalid = null;
        section.querySelectorAll("[data-required='true']").forEach((block) => {
          const choices = block.querySelectorAll("input[type=radio], input[type=checkbox]");
          let ok = false;
          if (choices.length) {
            ok = [...choices].some((el) => el.checked);
          } else {
            ok = [...block.querySelectorAll("input, textarea, select")].some((el) => el.value && el.value.trim() !== "");
          }
          if (block.dataset.kind === "matrix") {
            const rows = new Set([...block.querySelectorAll("input[type=radio]")].map((r) => r.name).filter((n) => !n.endsWith("_m")));
            ok = [...rows].every((n) => block.querySelector(`input[name="${n}"]:checked`));
          }
          block.classList.toggle("ring-2", !ok);
          block.classList.toggle("ring-rs-danger/50", !ok);
          const msg = block.querySelector("[data-client-error]");
          if (msg) msg.hidden = ok;
          if (!ok && !firstInvalid) firstInvalid = block;
        });
        if (firstInvalid) { firstInvalid.scrollIntoView({ behavior: "smooth", block: "center" }); return false; }
        return true;
      },
      async next() {
        if (!this.validateStep()) return;
        this.save();
        if (this.step < this.total - 1) { this.step++; this.top(); }
      },
      prev() { if (this.step > 0) { this.step--; this.top(); } },
      top() { this.$nextTick(() => this.$refs.top && this.$refs.top.scrollIntoView({ behavior: "smooth", block: "start" })); },
      async submit(e) {
        if (cfg.preview) { e.preventDefault(); return; }
        if (!this.validateStep()) { e.preventDefault(); return; }
        if (this.submitting) { e.preventDefault(); return; }
        this.submitting = true;  // duplicate-submission guard; the server is idempotent too
      },
    }));

    /* Ranking: keeps rank numbers unique within one question. */
    Alpine.data("ranking", () => ({
      fix(changed) {
        const selects = [...this.$el.querySelectorAll("select")];
        const value = changed.value;
        if (!value) return;
        selects.forEach((s) => { if (s !== changed && s.value === value) s.value = ""; });
      },
    }));
  });

  window.RS = { renderCharts, exportChart, setTheme };
})();
