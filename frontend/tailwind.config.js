/** @type {import('tailwindcss').Config} */
const v = (name) => `rgb(var(--rs-${name}) / <alpha-value>)`;
module.exports = {
  darkMode: "class",
  content: ["../templates/**/*.html", "../static/js/**/*.js", "../**/templatetags/*.py", "../**/forms.py"],
  theme: {
    extend: {
      colors: {
        rs: {
          navy: v("navy"), royal: v("royal"), gold: v("gold"), bg: v("bg"), surface: v("surface"),
          raised: v("raised"), text: v("text"), muted: v("muted"), border: v("border"),
          success: v("success"), warning: v("warning"), danger: v("danger"), accent: v("accent"),
        },
      },
      fontFamily: {
        sans: ["Inter Variable", "Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        serif: ['"Source Serif 4"', "Georgia", "Cambria", "Times New Roman", "serif"],
      },
      boxShadow: {
        card: "0 1px 2px rgb(16 42 67 / 0.04), 0 4px 16px -4px rgb(16 42 67 / 0.08)",
        lift: "0 2px 4px rgb(16 42 67 / 0.06), 0 12px 32px -8px rgb(16 42 67 / 0.18)",
      },
      borderRadius: { xl: "0.875rem", "2xl": "1.125rem" },
    },
  },
  plugins: [require("@tailwindcss/forms")({ strategy: "class" })],
};
