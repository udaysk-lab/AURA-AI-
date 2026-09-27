"use client";

import { ReactNode, useEffect, useId, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

/**
 * Parse an API timestamp. They are all UTC, but SQLite drops the offset on the
 * way out ("2026-09-26T17:14:40"), and a bare ISO time is read as *local* by
 * the browser — so a message sent a minute ago in India said "6h ago".
 */
export function toDate(iso: string) {
  const bare = /T\d/.test(iso) && !/(?:[zZ]|[+-]\d\d:?\d\d)$/.test(iso);
  return new Date(bare ? `${iso}Z` : iso);
}

export function fmtTime(iso: string | null | undefined) {
  if (!iso) return "";
  // "numeric", not "2-digit": in a 12-hour locale 2-digit renders "02:30 PM".
  return toDate(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "Today", "Tomorrow", "Yesterday", or "Mon, 28 Sep". */
export function fmtDayLabel(iso: string | null | undefined) {
  if (!iso) return "";
  const d = toDate(iso);
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOf(d) - startOf(new Date())) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  return fmtDay(iso);
}

export function fmtDay(iso: string | null | undefined) {
  if (!iso) return "";
  return toDate(iso).toLocaleDateString([], {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

export function fmtRelative(iso: string | null | undefined) {
  if (!iso) return "";
  const diff = Date.now() - toDate(iso).getTime();
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "just now";
  if (Math.abs(mins) < 60) return `${mins > 0 ? mins : -mins}m ${mins > 0 ? "ago" : "from now"}`;
  const hrs = Math.round(mins / 60);
  if (Math.abs(hrs) < 24) return `${hrs > 0 ? hrs : -hrs}h ${hrs > 0 ? "ago" : "from now"}`;
  const days = Math.round(hrs / 24);
  if (Math.abs(days) < 7) return `${days > 0 ? days : -days}d ${days > 0 ? "ago" : "from now"}`;
  return fmtDay(iso);
}

export function initials(name: string, fallback = "?") {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return fallback;
  return (parts[0][0] + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** What's still missing before the next relationship stage, in words. */
export function progressHint(progress: {
  next_label: string;
  needs: { interactions: number; memories: number; actions: number };
}) {
  const { interactions, memories, actions } = progress.needs;
  const bits = [
    interactions > 0 && `${interactions} more conversation${interactions === 1 ? "" : "s"}`,
    memories > 0 && `${memories} more thing${memories === 1 ? "" : "s"} to learn about you`,
    actions > 0 && `${actions} more action${actions === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return bits.length ? `${bits.join(" · ")} to reach ${progress.next_label}` : "";
}

/**
 * Screens that change what the sidebar badges count (approving an action,
 * marking notifications read) announce it with this event, so the sidebar
 * doesn't sit on a stale "3 waiting on you" until its next poll.
 */
export const BADGES_EVENT = "aura:refresh-badges";
export function refreshBadges() {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(BADGES_EVENT));
}

/** "⌘K" on a Mac, "Ctrl K" everywhere else. */
export function useShortcutLabel(key: string) {
  const [mac, setMac] = useState(true);
  useEffect(() => {
    const platform =
      (navigator as any).userAgentData?.platform || navigator.platform || navigator.userAgent;
    setMac(/mac|iphone|ipad/i.test(platform));
  }, []);
  return mac ? `⌘${key}` : `Ctrl ${key}`;
}

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

export function Card({
  children,
  className = "",
  id,
  /** Lifts and picks up an accent edge on hover — for cards that are clickable. */
  interactive = false,
}: {
  children: ReactNode;
  className?: string;
  id?: string;
  interactive?: boolean;
}) {
  return (
    <div
      id={id}
      className={`panel p-5 ${
        interactive
          ? "transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-lift"
          : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

/**
 * The standard screen header: gradient title, optional blurb, an aurora bloom
 * behind it, and a slot for the primary action. Every top-level screen uses
 * this so the app doesn't drift into four different heading styles.
 */
export function PageHeader({
  title,
  blurb,
  action,
  /** Tints the bloom — use a category colour when the screen has one. */
  glow = "accent",
}: {
  title: string;
  blurb?: ReactNode;
  action?: ReactNode;
  glow?: "accent" | "teal" | "violet" | "amber" | "azure";
}) {
  const tint = {
    accent: "rgb(var(--accent) / 0.26)",
    teal: "rgb(var(--c-teal) / 0.22)",
    violet: "rgb(var(--c-violet) / 0.24)",
    amber: "rgb(var(--c-amber) / 0.2)",
    azure: "rgb(var(--c-azure) / 0.24)",
  }[glow];

  return (
    <div className="relative">
      <div
        className="aurora-spot -left-20 -top-24 h-56 w-[420px]"
        style={{ background: tint }}
      />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="display text-shine text-[26px]">{title}</h1>
          {blurb && (
            <p className="mt-1.5 max-w-2xl text-[13.5px] leading-relaxed text-muted">
              {blurb}
            </p>
          )}
        </div>
        {action}
      </div>
    </div>
  );
}

/** A headline number with the gradient treatment — for stat tiles. */
export function Stat({
  value,
  label,
  small = false,
}: {
  value: ReactNode;
  label: string;
  small?: boolean;
}) {
  return (
    <>
      <div className={`display text-shine ${small ? "text-[16px]" : "text-[30px]"}`}>
        {value}
      </div>
      <div className="mt-2 text-[12px] text-muted">{label}</div>
    </>
  );
}

export function SectionTitle({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3.5 flex items-center justify-between gap-3">
      <h2 className="label">{children}</h2>
      {action}
    </div>
  );
}

const TONES: Record<string, string> = {
  urgent: "border-rose/30 bg-rose/12 text-rose",
  high: "border-amber/35 bg-amber/14 text-amber",
  medium: "border-teal/30 bg-teal/12 text-teal",
  normal: "border-line bg-raised/60 text-muted",
  low: "border-line bg-raised/50 text-faint",
  success: "border-sage/35 bg-sage/14 text-sage",
  accent: "border-accent/35 bg-accent-dim text-accent-soft",
  info: "border-line bg-raised/60 text-muted",
  warning: "border-amber/35 bg-amber/14 text-amber",
  violet: "border-violet/30 bg-violet/12 text-violet",
  azure: "border-azure/30 bg-azure/12 text-azure",
};

export function Badge({
  children,
  tone = "normal",
}: {
  children: ReactNode;
  tone?: keyof typeof TONES | string;
}) {
  return <span className={`chip ${TONES[tone] ?? TONES.normal}`}>{children}</span>;
}

export function Spinner({ className = "" }: { className?: string }) {
  // Uses currentColor so it reads correctly on both filled and ghost buttons.
  return (
    <span
      className={`inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-b-transparent border-r-transparent ${className}`}
    />
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  // Sheen rather than a flat pulse — reads better against the glass surfaces.
  return (
    <div
      className={`animate-shimmer rounded-xl bg-raised/70 ${className}`}
      style={{
        backgroundImage:
          "linear-gradient(90deg, transparent 0%, rgb(var(--hairline) / 0.09) 50%, transparent 100%)",
        backgroundSize: "220% 100%",
      }}
    />
  );
}

export function EmptyState({
  title,
  hint,
  icon,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-line bg-raised/25 px-6 py-10 text-center backdrop-blur-sm">
      {icon && <div className="text-faint">{icon}</div>}
      <p className="text-sm font-medium text-muted">{title}</p>
      {hint && <p className="max-w-sm text-xs text-faint">{hint}</p>}
    </div>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
}) {
  const titleId = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Escape closes, as every dialog should; this one used to ignore it.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-canvas/75 p-4 pt-[9vh] backdrop-blur-md"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`glass w-full ${width} mb-8 animate-pop-in p-5 shadow-lift`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 id={titleId} className="min-w-0 truncate text-sm font-semibold">
            {title}
          </h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="btn-quiet shrink-0 px-2.5 py-1 text-lg leading-none"
          >
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Progress({
  value,
  tone = "accent",
}: {
  value: number;
  tone?: "accent" | "teal" | "sage";
}) {
  const bg = {
    accent: "bg-gradient-to-r from-accent to-accent-soft shadow-glow-sm",
    teal: "bg-gradient-to-r from-teal/70 to-teal",
    sage: "bg-gradient-to-r from-sage/70 to-sage",
  }[tone];
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full border border-line bg-raised/60">
      <div
        className={`h-full rounded-full ${bg} transition-all duration-700`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled = false,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 shrink-0 rounded-full border border-line transition disabled:opacity-50 ${
        checked ? "bg-accent shadow-glow-sm" : "bg-raised"
      }`}
    >
      <span
        className={`absolute top-[1px] h-4 w-4 rounded-full bg-white shadow-sm transition-all ${
          checked ? "left-[18px]" : "left-0.5"
        }`}
      />
    </button>
  );
}

// ---------------------------------------------------------------------------
// Skill activity
// ---------------------------------------------------------------------------

/** One line of the terminal-style skill log: [SKILL·EM01] Processed 47 emails… */
export function SkillLine({
  code,
  summary,
  meta,
  ok = true,
}: {
  code: string;
  summary: string;
  meta?: string;
  ok?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2 py-1">
      <span className={`skill-line shrink-0 ${ok ? "skill-code" : "text-rose"}`}>
        [SKILL·{code}]
      </span>
      <span className="skill-line min-w-0 flex-1 text-ink/80">{summary}</span>
      {meta && <span className="skill-line shrink-0 text-faint">{meta}</span>}
    </div>
  );
}

export function SkillBadge({ code, name }: { code: string; name?: string }) {
  return (
    <span className="chip border-accent/25 bg-accent-dim text-accent-soft">
      <span className="font-mono text-[10px] opacity-80">{code}</span>
      {name && <span>{name}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Minimal markdown renderer
// ---------------------------------------------------------------------------

/**
 * Handles the subset the assistant actually emits: headings, bold, italics,
 * inline code, fenced code, links, bullet and numbered lists. Deliberately not
 * a full parser — swap in react-markdown if you need tables and footnotes.
 *
 * The output goes through dangerouslySetInnerHTML, and the text can carry
 * words from an email someone else wrote, so it is escaped completely —
 * quotes included — and links keep only http(s) and mailto targets. Before,
 * `[x](javascript:…)` became a live script link and a quote in a URL could
 * break out of the href into an event handler: from there, the session token
 * in localStorage was one click away.
 */
export function Markdown({ text }: { text: string }) {
  const escape = (s: string) =>
    s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const link = (raw: string, label: string) => {
    // `raw` is already escaped; undo only what a URL legitimately contains.
    const url = raw.replace(/&amp;/g, "&").trim();
    if (/^(https?:\/\/|mailto:)/i.test(url)) {
      return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }
    // A path inside the app ("/tasks") is safe and opens in place.
    if (/^\/(?!\/)[\w\-./?=&#%]*$/.test(url)) return `<a href="${escape(url)}">${label}</a>`;
    return null;
  };

  const inline = (s: string) => {
    // Code spans first, and parked, so nothing inside them is formatted.
    const codes: string[] = [];
    let out = escape(s).replace(/`([^`]+)`/g, (_, code) => {
      codes.push(`<code>${code}</code>`);
      return `\u0000${codes.length - 1}\u0000`;
    });
    // Links next, parked too, so emphasis rules can't reach into a URL.
    const links: string[] = [];
    out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_whole, label, href) => {
      const html = link(href, label);
      if (!html) return label;
      links.push(html);
      return `\u0001${links.length - 1}\u0001`;
    });
    out = out
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      // No space just inside the markers, so "2 * 3 * 4" stays arithmetic.
      .replace(/(^|[^*\w])\*([^*\s](?:[^*\n]*[^*\s])?)\*(?!\w)/g, "$1<em>$2</em>")
      // Underscores only at word edges, so send_email_draft stays as written.
      .replace(/(^|[^\w])_([^_\s](?:[^_\n]*[^_\s])?)_(?!\w)/g, "$1<em>$2</em>");
    return out
      .replace(/\u0001(\d+)\u0001/g, (_, i) => links[Number(i)])
      .replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
  };

  const blocks: string[] = [];
  const segments = text.split(/```/);

  segments.forEach((segment, i) => {
    if (i % 2 === 1) {
      const body = segment.replace(/^[a-z0-9_+-]*\n/i, "");
      blocks.push(`<pre><code>${escape(body)}</code></pre>`);
      return;
    }
    let list: string[] = [];
    let ordered = false;

    const flush = () => {
      if (!list.length) return;
      blocks.push(`<${ordered ? "ol" : "ul"}>${list.join("")}</${ordered ? "ol" : "ul"}>`);
      list = [];
    };

    for (const raw of segment.split("\n")) {
      const line = raw.trimEnd();
      const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (heading) {
        flush();
        blocks.push(`<p><strong>${inline(heading[1])}</strong></p>`);
      } else if (bullet) {
        if (ordered) flush();
        ordered = false;
        list.push(`<li>${inline(bullet[1])}</li>`);
      } else if (numbered) {
        if (!ordered) flush();
        ordered = true;
        list.push(`<li>${inline(numbered[1])}</li>`);
      } else {
        flush();
        if (line.trim()) blocks.push(`<p>${inline(line)}</p>`);
      }
    }
    flush();
  });

  return (
    <div
      className="prose-chat break-words text-[13.5px] leading-relaxed text-ink/85"
      dangerouslySetInnerHTML={{ __html: blocks.join("") }}
    />
  );
}
