/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        border: "var(--border)",
        faint: "var(--border-faint)",
        text: "var(--text)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        "accent-fg": "var(--accent-fg)",
        link: "var(--link)",
        warn: "var(--warn)",
        ok: "var(--ok)",
        error: "var(--error)",
      },
      fontFamily: {
        serif: ["'Source Serif 4'", "Georgia", "serif"],
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "'Segoe UI'", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      fontSize: {
        caption: ["0.6875rem", { lineHeight: "1rem" }],
        ui: ["0.8125rem", { lineHeight: "1.125rem" }],
        "document-subheading": ["1.2rem", { lineHeight: "1.35" }],
        "document-heading": ["1.44rem", { lineHeight: "1.25" }],
        "document-display": ["1.728rem", { lineHeight: "1.2" }],
      },
      borderRadius: {
        card: "7px",
        input: "5px",
      },
      boxShadow: {
        card: "0 1px 2px rgba(28, 46, 50, 0.04)",
        pop: "0 10px 28px rgba(28, 46, 50, 0.14)",
      },
    },
  },
  plugins: [],
};
