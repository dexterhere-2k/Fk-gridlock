/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      colors: {
        ink: {
          50:  "#f6f7f9",
          100: "#eceef2",
          200: "#d4d7df",
          300: "#a9b0bc",
          400: "#7c8493",
          500: "#5a6271",
          600: "#3f4756",
          700: "#2b313c",
          800: "#1a1f28",
          900: "#0f1218",
          950: "#070a10",
        },
        accent: {
          50:  "#eef6ff",
          100: "#d8eaff",
          200: "#b6d8ff",
          300: "#85beff",
          400: "#4d9bff",
          500: "#1f7af8",
          600: "#0e5edb",
          700: "#0c4ab0",
          800: "#0e3f8b",
          900: "#11366f",
        },
        good:   { 500: "#16a34a" },
        warn:   { 500: "#f59e0b" },
        bad:    { 500: "#dc2626" },
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.06)",
        glow: "0 0 0 1px rgba(31,122,248,.35), 0 0 24px rgba(31,122,248,.18)",
      },
    },
  },
  plugins: [],
};
