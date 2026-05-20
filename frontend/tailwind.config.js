/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "rgb(var(--bg) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        panel2: "rgb(var(--panel2) / <alpha-value>)",
        line: "rgb(var(--line) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        sub: "rgb(var(--sub) / <alpha-value>)",
        accent: "rgb(var(--accent) / <alpha-value>)",
        accent2: "rgb(var(--accent2) / <alpha-value>)",
        // Provenance palette — pinned, used app-wide
        prov: {
          blue: "rgb(var(--prov-blue) / <alpha-value>)",
          purple: "rgb(var(--prov-purple) / <alpha-value>)",
          green: "rgb(var(--prov-green) / <alpha-value>)",
          orange: "rgb(var(--prov-orange) / <alpha-value>)",
          red: "rgb(var(--prov-red) / <alpha-value>)",
        },
      },
      fontFamily: {
        display: ['"Fraunces"', 'ui-serif', 'Georgia', 'serif'],
        sans: ['"IBM Plex Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        soft: "0 1px 2px rgb(0 0 0 / 0.06), 0 6px 24px -8px rgb(0 0 0 / 0.4)",
      },
      borderRadius: {
        xl2: "0.875rem",
      },
    },
  },
  plugins: [],
};
