// Copies third-party browser assets into ../static/vendor so production
// serves everything from its own origin (no CDN, strict CSP).
const fs = require("fs");
const path = require("path");
const out = path.join(__dirname, "..", "static", "vendor");
const fonts = path.join(__dirname, "..", "static", "fonts");
fs.mkdirSync(out, { recursive: true });
fs.mkdirSync(fonts, { recursive: true });
const copy = (src, dest) => {
  const from = path.join(__dirname, "node_modules", src);
  if (dest.endsWith(".js")) {
    // Source maps are not shipped; drop the reference so collectstatic's manifest step succeeds.
    fs.writeFileSync(dest, fs.readFileSync(from, "utf8").replace(/\n?\/\/# sourceMappingURL=.*$/m, "\n"));
  } else {
    fs.copyFileSync(from, dest);
  }
};
copy("alpinejs/dist/cdn.min.js", path.join(out, "alpine.min.js"));
copy("chart.js/dist/chart.umd.min.js", path.join(out, "chart.umd.min.js"));
for (const subset of ["latin", "latin-ext", "cyrillic", "cyrillic-ext"]) {
  copy(`@fontsource-variable/inter/files/inter-${subset}-wght-normal.woff2`, path.join(fonts, `inter-${subset}.woff2`));
  for (const w of ["600", "700"]) {
    copy(`@fontsource/source-serif-4/files/source-serif-4-${subset}-${w}-normal.woff2`, path.join(fonts, `source-serif-4-${subset}-${w}.woff2`));
  }
}
console.log("vendored assets copied");
