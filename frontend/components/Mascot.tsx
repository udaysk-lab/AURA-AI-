"use client";

/**
 * The assistant's face.
 *
 * A soft blob that shifts with the relationship stage — rounder and wider-eyed
 * early on, steadier and more level once it knows you. Original artwork, drawn
 * from primitives so it scales cleanly and themes with the palette.
 */

export type Colourway = "teal" | "amber" | "rose" | "violet" | "sage";
export type Stage = "stranger" | "acquaintance" | "colleague" | "chief_of_staff";

const FILL: Record<Colourway, string> = {
  teal: "rgb(var(--c-teal))",
  amber: "rgb(var(--c-amber))",
  rose: "rgb(var(--c-rose))",
  violet: "rgb(var(--c-violet))",
  sage: "rgb(var(--c-sage))",
};

// Silhouettes get calmer as the assistant matures.
const SHAPE: Record<Stage, string> = {
  stranger:
    "M50 6c26 0 40 18 40 42 0 26-16 46-40 46S10 74 10 48C10 24 24 6 50 6Z",
  acquaintance:
    "M50 5c27 0 42 17 42 43s-15 46-42 46S8 74 8 48 23 5 50 5Z",
  colleague:
    "M50 7c28 0 44 16 44 41 0 27-17 45-44 45S6 75 6 48C6 23 22 7 50 7Z",
  chief_of_staff:
    "M50 8c29 0 46 15 46 40s-17 44-46 44S4 73 4 48 21 8 50 8Z",
};

const EYE: Record<Stage, { r: number; dy: number; gap: number }> = {
  stranger: { r: 7.5, dy: -2, gap: 15 },
  acquaintance: { r: 7, dy: 0, gap: 15 },
  colleague: { r: 6, dy: 1, gap: 16 },
  chief_of_staff: { r: 5, dy: 2, gap: 17 },
};

export default function Mascot({
  colourway = "teal",
  stage = "stranger",
  size = 64,
  active = false,
  className = "",
}: {
  colourway?: Colourway;
  stage?: Stage;
  size?: number;
  /** Adds a working pulse — use while the heartbeat or agent is running. */
  active?: boolean;
  className?: string;
}) {
  const fill = FILL[colourway] ?? FILL.teal;
  const eye = EYE[stage] ?? EYE.stranger;
  const uid = `${colourway}-${stage}`;

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
        role="img"
        aria-label="Assistant"
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

        <path d={SHAPE[stage] ?? SHAPE.stranger} fill={fill} />
        <path d={SHAPE[stage] ?? SHAPE.stranger} fill={`url(#m-sh-${uid})`} />
        <path d={SHAPE[stage] ?? SHAPE.stranger} fill={`url(#m-hi-${uid})`} />

        <g className="animate-blink" style={{ transformOrigin: "50px 50px" }}>
          <circle cx={50 - eye.gap} cy={48 + eye.dy} r={eye.r} fill={SCLERA} />
          <circle cx={50 + eye.gap} cy={48 + eye.dy} r={eye.r} fill={SCLERA} />
          <circle
            cx={50 - eye.gap + 1.5}
            cy={49 + eye.dy}
            r={eye.r * 0.42}
            fill={PUPIL}
          />
          <circle
            cx={50 + eye.gap + 1.5}
            cy={49 + eye.dy}
            r={eye.r * 0.42}
            fill={PUPIL}
          />
        </g>

        {stage === "chief_of_staff" && (
          <path
            d="M36 68q14 9 28 0"
            stroke={SCLERA}
            strokeWidth="3"
            strokeLinecap="round"
            fill="none"
            opacity="0.85"
          />
        )}
      </svg>
    </span>
  );
}
