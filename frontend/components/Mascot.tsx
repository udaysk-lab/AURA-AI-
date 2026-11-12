"use client";

import { useId } from "react";

/**
 * The assistant's face.
 *
 * A single static soft blob in the chosen colourway. Original artwork, drawn
 * from primitives so it scales cleanly and themes with the palette.
 */

export type Colourway = "teal" | "amber" | "rose" | "violet" | "sage";

const FILL: Record<Colourway, string> = {
  teal: "rgb(var(--c-teal))",
  amber: "rgb(var(--c-amber))",
  rose: "rgb(var(--c-rose))",
  violet: "rgb(var(--c-violet))",
  sage: "rgb(var(--c-sage))",
};

const SHAPE = "M50 7c28 0 44 16 44 41 0 27-17 45-44 45S6 75 6 48C6 23 22 7 50 7Z";
const EYE = { r: 6, dy: 1, gap: 16 };

export default function Mascot({
  colourway = "teal",
  size = 64,
  active = false,
  className = "",
  label,
}: {
  colourway?: Colourway;
  size?: number;
  /** Adds a working pulse — use while the heartbeat or agent is running. */
  active?: boolean;
  className?: string;
  /** Accessible name. Without one the mascot is decorative and hidden from screen readers. */
  label?: string;
}) {
  const fill = FILL[colourway] ?? FILL.teal;
  const eye = EYE;
  // Unique per instance: url(#id) resolves to the first element with that id,
  // and when that one is hidden (display:none on mobile) the others would
  // lose their shading.
  const uid = useId().replace(/:/g, "");

  // Fixed rather than themed: the face sits on a saturated blob, so the sclera
  // needs to stay light and the pupil dark in every palette.
  const SCLERA = "rgb(252 252 255 / 0.94)";
  const PUPIL = "rgb(14 12 22)";

  return (
    <span
      className={`relative inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
    >
      {/* Ambient bloom — makes the mascot sit in the aurora rather than on it */}
      <span
        className="pointer-events-none absolute inset-[-18%] rounded-full"
        style={{ background: fill, opacity: 0.22, filter: `blur(${size * 0.22}px)` }}
      />
      {active && (
        <span
          className="absolute inset-0 animate-pulse-ring rounded-full"
          style={{ background: fill, opacity: 0.35 }}
        />
      )}
      <svg
        viewBox="0 0 100 100"
        width={size}
        height={size}
        className="relative animate-breathe"
        {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
      >
        <defs>
          <radialGradient id={`m-hi-${uid}`} cx="32%" cy="22%" r="80%">
            <stop offset="0%" stopColor="#fff" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#fff" stopOpacity="0" />
          </radialGradient>
          <linearGradient id={`m-sh-${uid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#fff" stopOpacity="0" />
            <stop offset="100%" stopColor="#000" stopOpacity="0.28" />
          </linearGradient>
        </defs>

        <path d={SHAPE} fill={fill} />
        <path d={SHAPE} fill={`url(#m-sh-${uid})`} />
        <path d={SHAPE} fill={`url(#m-hi-${uid})`} />

        <g className="animate-blink" style={{ transformOrigin: "50px 50px" }}>
          <circle cx={50 - eye.gap} cy={48 + eye.dy} r={eye.r} fill={SCLERA} />
          <circle cx={50 + eye.gap} cy={48 + eye.dy} r={eye.r} fill={SCLERA} />
          <circle cx={50 - eye.gap + 1.5} cy={49 + eye.dy} r={eye.r * 0.42} fill={PUPIL} />
          <circle cx={50 + eye.gap + 1.5} cy={49 + eye.dy} r={eye.r * 0.42} fill={PUPIL} />
        </g>
      </svg>
    </span>
  );
}
