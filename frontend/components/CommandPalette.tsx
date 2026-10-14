"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Bell,
  Calendar,
  CheckSquare,
  FileText,
  Laptop,
  LayoutDashboard,
  type LucideIcon,
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
} from "lucide-react";
import { api } from "@/lib/api";
import { useToast } from "./Toast";
import { Spinner, handOffQuestion, refreshBadges, useShortcutLabel } from "./ui";

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  run: () => void | Promise<void>;
  keywords?: string;
}

/**
 * ⌘K. Navigation plus the actions people actually repeat — asking the assistant
 * something, triggering a catch-up, compacting memory. Typing anything that
 * isn't a command falls through to "ask your assistant", so the palette is
 * never a dead end.
 */
export default function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const go = useCallback(
    (href: string) => () => {
      router.push(href);
      onClose();
    },
    [router, onClose]
  );

  const commands: Command[] = useMemo(
    () => [
      { id: "today", label: "Today", group: "Go", icon: LayoutDashboard, run: go("/dashboard") },
      { id: "chat", label: "Chat", group: "Go", icon: MessageSquare, run: go("/chat") },
      { id: "skills", label: "Skills", group: "Go", icon: Wand2, run: go("/skills") },
      { id: "hub", label: "Plugin hub", group: "Go", icon: Puzzle, run: go("/hub"), keywords: "plugins install marketplace" },
      { id: "channels", label: "Channels", group: "Go", icon: Radio, run: go("/channels"), keywords: "slack telegram email cli" },
      { id: "documents", label: "Documents", group: "Go", icon: FileText, run: go("/documents"), keywords: "files upload pdf" },
      { id: "schedules", label: "Schedules", group: "Go", icon: Timer, run: go("/schedules"), keywords: "cron recurring" },
      { id: "email", label: "Email", group: "Go", icon: Mail, run: go("/emails") },
      { id: "calendar", label: "Calendar", group: "Go", icon: Calendar, run: go("/calendar") },
      { id: "tasks", label: "Tasks", group: "Go", icon: CheckSquare, run: go("/tasks") },
      { id: "memory", label: "Memory", group: "Go", icon: Sparkles, run: go("/memory") },
      { id: "automations", label: "Automations", group: "Go", icon: Workflow, run: go("/automations") },
      { id: "notifications", label: "Notifications", group: "Go", icon: Bell, run: go("/notifications"), keywords: "alerts inbox unread" },
      { id: "settings", label: "Settings", group: "Go", icon: Settings, run: go("/settings") },
      // Harmless on a hosted instance: the screen explains that device
      // control is off rather than 404ing.
      {
        id: "device",
        label: "This computer",
        group: "Go",
        icon: Laptop,
        run: go("/device"),
        keywords: "device terminal console shell files processes machine local",
      },
      {
        id: "catchup",
        label: "Catch me up",
        hint: "Run the heartbeat now",
        group: "Do",
        icon: Radio,
        keywords: "heartbeat sync background",
        run: async () => {
          setBusy(true);
          try {
            const report = await api.runHeartbeat();
            toast.success(report.headline);
            refreshBadges();
            router.push("/dashboard");
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't run the heartbeat");
          } finally {
            setBusy(false);
            onClose();
          }
        },
      },
      {
        id: "compact",
        label: "Tidy memory",
        hint: "Merge duplicates, fade the unused",
        group: "Do",
        icon: Sparkles,
        keywords: "compact consolidate clean",
        run: async () => {
          setBusy(true);
          try {
            const r = await api.compactMemory();
            toast.success(`${r.before} → ${r.after} memories · ${r.merged} merged`);
          } catch (e) {
            toast.error(e instanceof Error ? e.message : "Couldn't tidy memory");
          } finally {
            setBusy(false);
            onClose();
          }
        },
      },
    ],
    [go, router, toast, onClose]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) =>
      `${c.label} ${c.group} ${c.keywords ?? ""} ${c.hint ?? ""}`.toLowerCase().includes(q)
    );
  }, [commands, query]);

  const askInstead = query.trim().length > 2 && filtered.length === 0;
  const nothing = !askInstead && filtered.length === 0;
  const toggleLabel = useShortcutLabel("K");

  const ask = useCallback(() => {
    // Chat picks this up on arrival, or at once if it's already open — the
    // old ?q= push did nothing at all when you were already on Chat.
    handOffQuestion(query.trim());
    router.push("/chat");
    onClose();
  }, [router, query, onClose]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
      setTimeout(() => inputRef.current?.focus(), 20);
    }
  }, [open]);

  useEffect(() => setCursor(0), [query]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") return onClose();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(filtered.length - 1, c + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(0, c - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (busy) return;
      if (askInstead) return ask();
      void filtered[cursor]?.run();
    }
  };

  if (!open) return null;

  let lastGroup = "";

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-canvas/75 p-4 pt-[12vh] backdrop-blur-md"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search and commands"
        className="panel w-full max-w-lg animate-pop-in overflow-hidden p-0 shadow-lift"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2.5 border-b border-line px-4 py-3">
          <Search size={15} className="shrink-0 text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Search, or ask your assistant…"
            aria-label="Search, or ask your assistant"
            className="flex-1 bg-transparent text-[14px] placeholder:text-faint outline-none"
          />
          {busy ? (
            <Spinner className="shrink-0 text-faint" />
          ) : (
            <kbd className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-[10px] text-faint">
              esc
            </kbd>
          )}
        </div>

        <div className="max-h-[52vh] overflow-y-auto p-2">
          {nothing ? (
            <p className="px-3 py-6 text-center text-[13px] text-faint">
              No matches. Keep typing to ask your assistant instead.
            </p>
          ) : askInstead ? (
            <button
              onClick={ask}
              className="flex w-full items-center gap-3 rounded-xl bg-accent-dim px-3 py-3 text-left"
            >
              <MessageSquare size={15} className="text-accent" />
              <span className="min-w-0 flex-1 truncate text-[13.5px]">
                Ask: <span className="text-muted">{query}</span>
              </span>
              <ArrowRight size={14} className="text-accent" />
            </button>
          ) : (
            filtered.map((c, i) => {
              const showGroup = c.group !== lastGroup;
              lastGroup = c.group;
              return (
                <div key={c.id}>
                  {showGroup && <div className="label px-3 pb-1 pt-2">{c.group}</div>}
                  <button
                    onMouseEnter={() => setCursor(i)}
                    onClick={() => void c.run()}
                    disabled={busy}
                    className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition ${
                      i === cursor ? "bg-raised" : "hover:bg-raised/60"
                    }`}
                  >
                    <c.icon size={15} className={i === cursor ? "text-accent" : "text-faint"} />
                    <span className="min-w-0 flex-1 truncate text-[13.5px]">{c.label}</span>
                    {c.hint && (
                      <span className="shrink-0 text-[11.5px] text-faint">{c.hint}</span>
                    )}
                  </button>
                </div>
              );
            })
          )}
        </div>

        <div className="flex items-center justify-between border-t border-line px-4 py-2 text-[11px] text-faint">
          <span>↑↓ to move · ⏎ to run</span>
          <span>{toggleLabel} to toggle</span>
        </div>
      </div>
    </div>
  );
}
