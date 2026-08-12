"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Bell,
  Calendar,
  CheckSquare,
  FileText,
  LayoutDashboard,
  LogOut,
  Mail,
  MessageSquare,
  Puzzle,
  Radio,
  Search,
  Settings,
  Sparkles,
  Timer,
  Wand2,
  Workflow,
  X,
} from "lucide-react";
import { PendingAction, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";
import Mascot from "./Mascot";
import { Progress, initials } from "./ui";

const NAV = [
  {
    group: "",
    items: [
      { href: "/dashboard", label: "Today", icon: LayoutDashboard },
      { href: "/chat", label: "Chat", icon: MessageSquare },
    ],
  },
  {
    group: "Work",
    items: [
      { href: "/emails", label: "Email", icon: Mail },
      { href: "/calendar", label: "Calendar", icon: Calendar },
      { href: "/tasks", label: "Tasks", icon: CheckSquare },
      { href: "/documents", label: "Documents", icon: FileText },
    ],
  },
  {
    group: "Assistant",
    items: [
      { href: "/skills", label: "Skills", icon: Wand2 },
      { href: "/hub", label: "Plugin hub", icon: Puzzle },
      { href: "/memory", label: "Memory", icon: Sparkles },
      { href: "/channels", label: "Channels", icon: Radio },
    ],
  },
  {
    group: "Running",
    items: [
      { href: "/schedules", label: "Schedules", icon: Timer },
      { href: "/automations", label: "Automations", icon: Workflow },
      { href: "/notifications", label: "Notifications", icon: Bell },
    ],
  },
];

export default function Sidebar({
  onOpenPalette,
  onNavigate,
  className = "",
}: {
  onOpenPalette?: () => void;
  onNavigate?: () => void;
  className?: string;
}) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { assistant } = useAssistant();
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    const load = async () => {
      try {
        const [actions, notes] = await Promise.all([
          api.pendingActions(),
          api.notifications().catch(() => []),
        ]);
        setPending(actions);
        setUnread(notes.filter((n) => !n.read).length);
      } catch {
        /* not fatal — badges just stay empty */
      }
    };
    void load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [pathname]);

  return (
    <aside
      className={`relative flex h-screen w-[246px] shrink-0 flex-col border-r border-line bg-panel/45 px-3 py-4 backdrop-blur-2xl ${className}`}
    >
      {/* Aurora bleeds into the rail so it reads as one surface with the app */}
      <div
        className="aurora-spot -left-16 top-0 h-64 w-64"
        style={{ background: "rgb(var(--accent) / 0.28)" }}
      />

      <Link
        href="/settings#assistant"
        onClick={onNavigate}
        className="relative mb-4 flex items-center gap-3 rounded-2xl px-2 py-2 transition hover:bg-raised/70"
      >
        <Mascot
          colourway={assistant?.avatar ?? "teal"}
          stage={assistant?.stage ?? "stranger"}
          size={38}
        />
        <div className="min-w-0 leading-tight">
          <div className="truncate text-[15px] font-semibold tracking-tight">
            {assistant?.name ?? "Aura"}
          </div>
          <div className="truncate text-[10.5px] uppercase tracking-[0.14em] text-faint">
            {assistant?.stage_label ?? "Assistant"}
          </div>
        </div>
      </Link>

      {assistant?.progress && (
        <div className="relative mb-4 px-2">
          <Progress value={assistant.progress.percent} />
          <div className="mt-1.5 text-[10.5px] text-faint">
            {assistant.progress.percent}% to {assistant.progress.next_label}
          </div>
        </div>
      )}

      {onOpenPalette && (
        <button
          onClick={onOpenPalette}
          className="relative mb-3 flex items-center gap-2 rounded-xl border border-line bg-raised/40 px-2.5 py-2 text-[12.5px] text-faint transition hover:border-accent/40 hover:bg-raised/70 hover:text-ink"
        >
          <Search size={13} />
          <span className="flex-1 text-left">Search…</span>
          <kbd className="rounded border border-line px-1 text-[10px]">⌘K</kbd>
        </button>
      )}

      <nav className="relative flex flex-1 flex-col gap-0.5 overflow-y-auto">
        {NAV.map((section) => (
          <div key={section.group || "main"}>
            {section.group && <div className="label px-2.5 pb-1 pt-3">{section.group}</div>}
            {section.items.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname.startsWith(`${href}/`);
              const badge =
                href === "/notifications" && unread ? unread : undefined;
              return (
                <Link
                  key={href}
                  href={href}
                  onClick={onNavigate}
                  className={`group relative flex items-center gap-2.5 rounded-xl px-2.5 py-2 text-[13px] transition-all ${
                    active
                      ? "border border-accent/25 bg-accent/12 font-medium text-ink shadow-glow-sm"
                      : "border border-transparent text-muted hover:bg-raised/60 hover:text-ink"
                  }`}
                >
                  {active && (
                    <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent-soft" />
                  )}
                  <Icon size={15} className={active ? "text-accent-soft" : ""} />
                  <span className="flex-1">{label}</span>
                  {badge ? (
                    <span className="rounded-full bg-accent px-1.5 text-[10px] font-semibold text-white shadow-glow-sm">
                      {badge}
                    </span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      {pending.length > 0 && (
        <Link
          href="/dashboard#approvals"
          onClick={onNavigate}
          className="relative mb-3 mt-3 flex items-center gap-2 rounded-xl border border-amber/35 bg-amber/12 px-2.5 py-2 text-[12px] font-medium text-amber backdrop-blur-md transition hover:bg-amber/20"
        >
          <Bell size={13} />
          {pending.length} waiting on you
        </Link>
      )}

      <div className="relative border-t border-line pt-3">
        <div className="flex items-center gap-2.5 px-1">
          <div className="flex h-8 w-8 items-center justify-center rounded-full border border-line bg-raised/70 text-[11px] font-semibold text-muted">
            {initials(user?.name || user?.email || "")}
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[12.5px] font-medium">{user?.name || "Signed in"}</div>
            <div className="truncate text-[11px] text-faint">{user?.email}</div>
          </div>
          <Link
            href="/settings"
            onClick={onNavigate}
            className="rounded-lg p-1.5 text-faint transition hover:bg-raised hover:text-ink"
            title="Settings"
          >
            <Settings size={14} />
          </Link>
          <button
            onClick={logout}
            title="Sign out"
            className="rounded-lg p-1.5 text-faint transition hover:bg-raised hover:text-ink"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </aside>
  );
}

export function MobileTopBar({
  onOpenNav,
  onOpenPalette,
}: {
  onOpenNav: () => void;
  onOpenPalette: () => void;
}) {
  const { assistant } = useAssistant();
  return (
    <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-canvas/70 px-4 py-3 backdrop-blur-2xl lg:hidden">
      <button onClick={onOpenNav} className="btn-quiet px-2 py-1.5" aria-label="Open navigation">
        <Mascot
          colourway={assistant?.avatar ?? "teal"}
          stage={assistant?.stage ?? "stranger"}
          size={28}
        />
      </button>
      <div className="min-w-0 flex-1 truncate text-[14px] font-semibold">
        {assistant?.name ?? "Aura"}
      </div>
      <button onClick={onOpenPalette} className="btn-quiet px-2 py-1.5" aria-label="Search">
        <Search size={16} />
      </button>
    </div>
  );
}

export function MobileNav({ open, onClose }: { open: boolean; onClose: () => void }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex lg:hidden" onClick={onClose}>
      <div className="absolute inset-0 bg-canvas/75 backdrop-blur-md" />
      <div className="relative animate-fade-up" onClick={(e) => e.stopPropagation()}>
        <Sidebar onNavigate={onClose} className="shadow-lift" />
        <button
          onClick={onClose}
          className="absolute right-[-44px] top-4 rounded-full border border-line bg-panel/80 p-2 shadow-soft backdrop-blur-xl"
          aria-label="Close navigation"
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}
