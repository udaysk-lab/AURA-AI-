"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Bell,
  Calendar,
  ChevronDown,
  CheckSquare,
  FileText,
  Laptop,
  LayoutDashboard,
  LogOut,
  Mail,
  Pin,
  Plus,
  Puzzle,
  Radio,
  Search,
  Settings,
  Sparkles,
  Trash2,
  Timer,
  Workflow,
  X,
} from "lucide-react";
import { Conversation, PendingAction, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";
import Mascot from "./Mascot";
import {
  BADGES_EVENT,
  CONVERSATIONS_EVENT,
  initials,
  sendChatEvent,
  useShortcutLabel,
} from "./ui";
import { useToast } from "./Toast";

// The everyday links, always visible under the Recent list.
const MAIN_LINKS = [
  { href: "/schedules", label: "Scheduled", icon: Timer },
  { href: "/hub", label: "Systems", icon: Puzzle },
  { href: "/memory", label: "Memory", icon: Sparkles },
  { href: "/settings", label: "Settings", icon: Settings },
];

// Existing screens, re-housed in a collapsible group.
const WORKSPACE_LINKS = [
  { href: "/dashboard", label: "Today", icon: LayoutDashboard },
  { href: "/emails", label: "Inbox", icon: Mail },
  { href: "/calendar", label: "Calendar", icon: Calendar },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/automations", label: "Automations", icon: Workflow },
  { href: "/channels", label: "Channels", icon: Radio },
  { href: "/notifications", label: "Notifications", icon: Bell },
];

// Only on a local install: the screen for the computer AURA is running on.
// Hidden entirely on a hosted instance, where there is no device to operate.
const DEVICE_LINK = { href: "/device", label: "This computer", icon: Laptop };

const WORKSPACE_KEY = "aura_workspace_open";
const RECENT_LIMIT = 12;

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
  const [deviceControl, setDeviceControl] = useState(false);
  const lastLoad = useRef(0);

  const load = useCallback(async (force = false) => {
    // Every navigation used to refetch both lists, on top of the page's own
    // requests. Once every 15 seconds is plenty between polls.
    if (!force && Date.now() - lastLoad.current < 15_000) return;
    lastLoad.current = Date.now();
    try {
      // On a page load the page beside this asks for the same list; the
      // shared request answers both. A forced reload (after a decision, or
      // the 30-second poll) asks afresh.
      const [actions, notes] = await Promise.all([
        api.pendingActions(force),
        api.notifications().catch(() => []),
      ]);
      setPending(actions);
      setUnread(notes.filter((n) => !n.read).length);
    } catch {
      /* not fatal — badges just stay as they were */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [pathname, load]);

  // Whether this instance can operate a computer is fixed at startup, so ask
  // once per mount rather than on every navigation.
  useEffect(() => {
    let cancelled = false;
    void api
      .authConfig()
      .then((config) => {
        if (!cancelled) setDeviceControl(config.device_control);
      })
      .catch(() => {
        /* the nav item just stays hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const t = setInterval(() => void load(true), 30000);
    const onRefresh = () => void load(true);
    window.addEventListener(BADGES_EVENT, onRefresh);
    return () => {
      clearInterval(t);
      window.removeEventListener(BADGES_EVENT, onRefresh);
    };
  }, [load]);

  const searchShortcut = useShortcutLabel("K");
  const router = useRouter();
  const toast = useToast();

  // Recent conversations come from the existing conversations endpoint.
  const [recent, setRecent] = useState<Conversation[]>([]);
  const loadRecent = useCallback(async () => {
    try {
      setRecent(await api.conversations());
    } catch {
      /* the list just stays as it was */
    }
  }, []);
  useEffect(() => {
    void loadRecent();
  }, [pathname, loadRecent]);
  useEffect(() => {
    window.addEventListener(CONVERSATIONS_EVENT, loadRecent);
    return () => window.removeEventListener(CONVERSATIONS_EVENT, loadRecent);
  }, [loadRecent]);

  const onChat = pathname === "/chat" || pathname.startsWith("/chat/");
  const newTask = () => {
    onNavigate?.();
    if (onChat) sendChatEvent({ type: "new" });
    else router.push("/chat");
  };
  const openConversation = (id: string) => {
    onNavigate?.();
    if (onChat) sendChatEvent({ type: "open", id });
    else router.push(`/chat?c=${encodeURIComponent(id)}`);
  };
  const removeConversation = async (c: Conversation) => {
    if (!window.confirm(`Delete "${c.title}"? This can't be undone.`)) return;
    try {
      await api.deleteConversation(c.id);
      sendChatEvent({ type: "deleted", id: c.id });
      setRecent((list) => list.filter((x) => x.id !== c.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete that conversation");
    }
  };

  // Workspace group: remembered, and forced open while you are inside it.
  const workspaceLinks = [...WORKSPACE_LINKS, ...(deviceControl ? [DEVICE_LINK] : [])];
  const inWorkspace = workspaceLinks.some(
    (l) => pathname === l.href || pathname.startsWith(`${l.href}/`)
  );
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  useEffect(() => {
    try {
      setWorkspaceOpen(localStorage.getItem(WORKSPACE_KEY) === "1");
    } catch {
      /* private mode: stays collapsed */
    }
  }, []);
  const toggleWorkspace = () => {
    const next = !workspaceOpen;
    setWorkspaceOpen(next);
    try {
      localStorage.setItem(WORKSPACE_KEY, next ? "1" : "0");
    } catch {
      /* not fatal */
    }
  };
  const showWorkspace = workspaceOpen || inWorkspace;

  const linkClass = (active: boolean) =>
    `group relative flex items-center gap-2.5 rounded-xl px-2.5 py-[7px] text-[13px] transition-all ${
      active
        ? "border border-accent/25 bg-accent/12 font-medium text-ink shadow-glow-sm"
        : "border border-transparent text-muted hover:bg-raised/60 hover:text-ink"
    }`;

  const renderLink = ({ href, label, icon: Icon }: (typeof MAIN_LINKS)[number]) => {
    // The skills page is reached from a system, so Systems stays lit there.
    const active =
      pathname === href ||
      pathname.startsWith(`${href}/`) ||
      (href === "/hub" && (pathname === "/skills" || pathname.startsWith("/skills/")));
    const badge = href === "/notifications" && unread ? unread : undefined;
    return (
      <Link
        key={href}
        href={href}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        className={linkClass(active)}
      >
        {active && <span className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-accent-soft" />}
        <Icon size={15} className={active ? "text-accent-soft" : ""} />
        <span className="flex-1">{label}</span>
        {badge ? (
          <span
            className="rounded-full bg-accent px-1.5 text-[10px] font-semibold text-white shadow-glow-sm"
            aria-label={`${badge} unread`}
          >
            {badge}
          </span>
        ) : null}
      </Link>
    );
  };

  return (
    <aside
      className={`relative flex h-dvh w-[246px] shrink-0 flex-col border-r border-line bg-panel/45 px-3 py-3 backdrop-blur-2xl ${className}`}
    >
      {/* Aurora bleeds into the rail so it reads as one surface with the app */}
      <div
        className="aurora-spot -left-16 top-0 h-64 w-64"
        style={{ background: "rgb(var(--accent) / 0.28)" }}
      />

      <Link
        href="/chat"
        onClick={onNavigate}
        className="relative mb-3 flex items-center gap-3 rounded-2xl px-2 py-1.5 transition hover:bg-raised/70"
        title={assistant?.name ?? "AURA"}
      >
        <Mascot colourway={assistant?.avatar ?? "teal"} size={32} />
        <span className="text-[16px] font-semibold tracking-tight">AURA</span>
      </Link>

      <button onClick={newTask} className="btn-primary relative mb-2 w-full justify-center">
        <Plus size={14} /> New task
      </button>

      {onOpenPalette && (
        <button
          onClick={onOpenPalette}
          className="relative mb-2 flex items-center gap-2 rounded-xl border border-line bg-raised/40 px-2.5 py-1.5 text-[12.5px] text-faint transition hover:border-accent/40 hover:bg-raised/70 hover:text-ink"
        >
          <Search size={13} />
          <span className="flex-1 text-left">Search…</span>
          <kbd className="rounded border border-line px-1 text-[10px]">{searchShortcut}</kbd>
        </button>
      )}

      <nav className="relative -mx-1 flex min-h-0 flex-1 flex-col gap-px overflow-y-auto px-1">
        <div className="label px-2.5 pb-0.5 pt-2.5">Recent</div>
        {recent.length === 0 && (
          <p className="px-2.5 py-1.5 text-[12px] text-faint">Your tasks and chats appear here.</p>
        )}
        {recent.slice(0, RECENT_LIMIT).map((c) => (
          <div
            key={c.id}
            className="group flex items-center gap-1.5 rounded-xl border border-transparent px-2.5 py-[7px] text-[12.5px] text-muted transition hover:bg-raised/60 hover:text-ink"
          >
            <button
              onClick={() => openConversation(c.id)}
              className="min-w-0 flex-1 truncate text-left"
              title={c.title}
            >
              {c.pinned && <Pin size={10} className="mr-1 inline text-accent-soft" />}
              {c.title}
            </button>
            <button
              onClick={() => removeConversation(c)}
              aria-label={`Delete conversation ${c.title}`}
              title="Delete conversation"
              className="rounded p-0.5 opacity-0 transition focus-visible:opacity-100 group-hover:opacity-100"
            >
              <Trash2 size={12} className="text-faint hover:text-rose" />
            </button>
          </div>
        ))}

        <div className="mt-2 flex flex-col gap-px border-t border-line pt-2">
          {MAIN_LINKS.map(renderLink)}
        </div>

        <div className="mt-1">
          <button
            onClick={toggleWorkspace}
            aria-expanded={showWorkspace}
            className="label flex w-full items-center gap-1.5 px-2.5 pb-0.5 pt-2.5 text-left hover:text-ink"
          >
            <ChevronDown
              size={12}
              className={`transition-transform ${showWorkspace ? "" : "-rotate-90"}`}
            />
            Workspace
          </button>
          {showWorkspace && (
            <div className="flex flex-col gap-px">{workspaceLinks.map(renderLink)}</div>
          )}
        </div>
      </nav>

      {pending.length > 0 && (
        <Link
          href="/dashboard#approvals"
          onClick={onNavigate}
          className="relative mb-2 mt-2 flex items-center gap-2 rounded-xl border border-amber/35 bg-amber/12 px-2.5 py-2 text-[12px] font-medium text-amber backdrop-blur-md transition hover:bg-amber/20"
        >
          <Bell size={13} />
          {pending.length} waiting on you
        </Link>
      )}

      <div className="relative border-t border-line pt-2.5">
        <div className="flex items-center gap-2.5 px-1">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-line bg-raised/70 text-[11px] font-semibold text-muted">
            {initials(user?.name || user?.email || "")}
          </div>
          <div className="min-w-0 flex-1 leading-tight">
            <div className="truncate text-[12.5px] font-medium">{user?.name || "Signed in"}</div>
            <div className="truncate text-[11px] text-faint" title={user?.email}>
              {user?.email}
            </div>
          </div>
          <button
            onClick={logout}
            title="Sign out"
            aria-label="Sign out"
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
    <div className="sticky top-0 z-30 flex items-center gap-3 border-b border-line bg-canvas/70 px-4 py-2.5 backdrop-blur-2xl lg:hidden">
      <button onClick={onOpenNav} className="btn-quiet px-2 py-1.5" aria-label="Open navigation">
        <Mascot
          colourway={assistant?.avatar ?? "teal"}
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
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex lg:hidden"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Navigation"
    >
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
