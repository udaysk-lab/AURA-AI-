import type { Config } from "tailwindcss";

/**
 * Colours are driven by CSS variables (raw RGB channels) declared in
 * globals.css. Dark aurora is the root palette; `.warm` swaps the whole thing
 * without duplicating classes, and Tailwind's `/opacity` modifiers keep working.
 */
const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "rgb(var(--canvas) / <alpha-value>)",
        panel: "rgb(var(--panel) / <alpha-value>)",
        raised: "rgb(var(--raised) / <alpha-value>)",
        ink: "rgb(var(--ink) / <alpha-value>)",
        muted: "rgb(var(--muted) / <alpha-value>)",
        faint: "rgb(var(--faint) / <alpha-value>)",
        line: "rgb(var(--hairline) / calc(var(--hairline-a) * 1.9))",
        accent: {
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          soft: "rgb(var(--accent-soft) / <alpha-value>)",
          dim: "rgb(var(--accent) / 0.14)",
        },
        // Legacy neutral ramp — see the note in globals.css. Kept so screens
        // written against the original theme retint with the palette.
        zinc: {
          50: "rgb(var(--z100) / <alpha-value>)",
          100: "rgb(var(--z100) / <alpha-value>)",
          200: "rgb(var(--z200) / <alpha-value>)",
          300: "rgb(var(--z300) / <alpha-value>)",
          400: "rgb(var(--z400) / <alpha-value>)",
          500: "rgb(var(--z500) / <alpha-value>)",
          600: "rgb(var(--z600) / <alpha-value>)",
          700: "rgb(var(--z700) / <alpha-value>)",
          800: "rgb(var(--z700) / <alpha-value>)",
          900: "rgb(var(--z700) / <alpha-value>)",
        },
        // Mascot / category colourways
        teal: "rgb(var(--c-teal) / <alpha-value>)",
        amber: "rgb(var(--c-amber) / <alpha-value>)",
        rose: "rgb(var(--c-rose) / <alpha-value>)",
        violet: "rgb(var(--c-violet) / <alpha-value>)",
        sage: "rgb(var(--c-sage) / <alpha-value>)",
        azure: "rgb(var(--c-azure) / <alpha-value>)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      borderRadius: {
        lg: "0.75rem",
        xl: "1rem",
        "2xl": "1.35rem",
        "3xl": "1.75rem",
      },
      boxShadow: {
        soft: "0 1px 2px rgb(0 0 0 / 0.3), 0 16px 44px -26px rgb(0 0 0 / 0.75)",
        lift: "0 2px 8px rgb(0 0 0 / 0.35), 0 32px 70px -30px rgb(0 0 0 / 0.9)",
        glow: "0 0 0 1px rgb(var(--accent) / 0.35), 0 16px 48px -14px rgb(var(--accent) / 0.6)",
        "glow-sm": "0 0 24px -6px rgb(var(--accent) / 0.55)",
      },
      backgroundImage: {
        "grid-fade":
          "linear-gradient(rgb(var(--hairline) / 0.045) 1px, transparent 1px), linear-gradient(90deg, rgb(var(--hairline) / 0.045) 1px, transparent 1px)",
      },
      backgroundSize: {
        grid: "56px 56px",
      },
      keyframes: {
        "fade-up": {
          from: { opacity: "0", transform: "translateY(14px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "pop-in": {
          "0%": { opacity: "0", transform: "scale(0.94)" },
          "60%": { transform: "scale(1.02)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        breathe: {
          "0%, 100%": { transform: "translateY(0) scale(1)" },
          "50%": { transform: "translateY(-3px) scale(1.015)" },
        },
        blink: {
          "0%, 92%, 100%": { transform: "scaleY(1)" },
          "96%": { transform: "scaleY(0.1)" },
        },
        "log-scroll": {
          from: { transform: "translateY(0)" },
          to: { transform: "translateY(-50%)" },
        },
        "pulse-ring": {
          "0%": { transform: "scale(0.9)", opacity: "0.7" },
          "100%": { transform: "scale(1.6)", opacity: "0" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        },
        shimmer: {
          from: { backgroundPosition: "-180% 0" },
          to: { backgroundPosition: "180% 0" },
        },
        "glow-pulse": {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.6s cubic-bezier(0.22,1,0.36,1) both",
        "pop-in": "pop-in 0.35s cubic-bezier(0.22,1,0.36,1) both",
        breathe: "breathe 5s ease-in-out infinite",
        blink: "blink 6s ease-in-out infinite",
        "log-scroll": "log-scroll 40s linear infinite",
        "pulse-ring": "pulse-ring 2.4s cubic-bezier(0.22,1,0.36,1) infinite",
        float: "float 7s ease-in-out infinite",
        shimmer: "shimmer 3.4s linear infinite",
        "glow-pulse": "glow-pulse 4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
