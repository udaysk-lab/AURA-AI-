"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  ArrowUp,
  GraduationCap,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  Square,
  Zap,
} from "lucide-react";
import {
  Conversation,
  Message,
  PendingAction,
  StillWorkingError,
  WorkItemDetail,
  api,
  streamChat,
  StreamEvent,
} from "@/lib/api";
import { driveTask, resetContinues } from "@/lib/taskrun";
import TaskPanel, { TaskPanelState } from "@/components/TaskPanel";
import { useAssistant } from "@/lib/assistant";
import ApprovalCard from "@/components/ApprovalCard";
import Mascot from "@/components/Mascot";
import ModePicker from "@/components/ModePicker";
import { useToast } from "@/components/Toast";
import {
  Markdown,
  SkillLine,
  ASK_EVENT,
  CHAT_EVENT,
  ChatEventDetail,
  Spinner,
  Toggle,
  fmtRelative,
  refreshBadges,
  refreshConversations,
  takeHandedOffQuestion,
} from "@/components/ui";

const SUGGESTIONS = [
  "What does my day look like?",
  "Summarise my inbox",
  "When am I free for 45 minutes this week?",
  "Add a task to review the board deck by Friday",
  "Remember that I prefer afternoon meetings",
];

interface LiveSkill {
  code: string;
  name: string;
  summary: string;
  ok: boolean;
}

/** One step line while a task runs: started, then finished. */
interface LiveStep {
  seq: number;
  tool: string;
  label: string;
  code: string;
  done: boolean;
  ok: boolean;
  summary: string;
}

interface RunCtx {
  convoId: string | null;
  error: string;
  finished: boolean;
  more: boolean;
  workItemId: string | null;
}

const EMPTY_PANEL: TaskPanelState = {
  workItemId: null,
  title: "",
  taskType: "",
  status: "starting",
  plan: [],
  systems: [],
  recall: null,
  noted: [],
  outcome: "",
  verification: "",
};

function verifStatus(v: WorkItemDetail["verification"]): string {
  if (!v) return "";
  if (typeof v === "string") return v;
  return v.status ?? "";
}

function panelFromDetail(d: WorkItemDetail): TaskPanelState {
  return {
    workItemId: d.id,
    title: d.title,
    taskType: d.task_type,
    status: d.status,
    plan: d.plan ?? [],
    systems: d.systems_used ?? [],
    recall: null,
    noted: [],
    outcome: d.outcome,
    verification: verifStatus(d.verification),
  };
}

const isLive = (s: TaskPanelState["status"]) =>
  s === "running" || s === "waiting" || s === "ready";

/**
 * Where to look at what a skill touched. A reply that read your calendar
 * offers a way to the calendar, rather than leaving you to find it.
 */
const SKILL_SCREENS: Array<{ prefix: string; href: string; label: string }> = [
  { prefix: "EM", href: "/emails", label: "Email" },
  { prefix: "CA", href: "/calendar", label: "Calendar" },
  { prefix: "MP", href: "/calendar", label: "Calendar" },
  { prefix: "FG", href: "/calendar", label: "Calendar" },
  { prefix: "TK", href: "/tasks", label: "Tasks" },
  { prefix: "DL", href: "/tasks", label: "Tasks" },
  { prefix: "MM", href: "/memory", label: "Memory" },
  { prefix: "DC", href: "/documents", label: "Documents" },
  { prefix: "BR", href: "/dashboard", label: "Today" },
  { prefix: "NT", href: "/notifications", label: "Notifications" },
  { prefix: "DV", href: "/device", label: "This computer" },
];

function screensFor(codes: string[]): Array<{ href: string; label: string }> {
  const out: Array<{ href: string; label: string }> = [];
  for (const code of codes) {
    const hit = SKILL_SCREENS.find((s) => code.startsWith(s.prefix));
    if (hit && !out.some((o) => o.href === hit.href)) out.push({ href: hit.href, label: hit.label });
  }
  return out.slice(0, 3);
}

/** One tap for the things people ask most, once a conversation is going. */
const QUICK_ACTIONS = [
  "Catch me up on today",
  "What needs a reply?",
  "What's due this week?",
  "Find me an hour to focus tomorrow",
];
const DEVICE_QUICK_ACTION = "How's my computer doing?";

const recallLine = (n: number) => `Recalled ${n} relevant memor${n === 1 ? "y" : "ies"}`;

/** The automatic recall line, unless a memory search already said the same. */
const showRecall = (n: number, skills: LiveSkill[]) =>
  n > 0 && !skills.some((s) => s.code === "MM01" && s.summary === recallLine(n));

function recallText(r: { work_items: number; preferences: number; memories: number }): string {
  const parts: string[] = [];
  if (r.work_items) parts.push(`${r.work_items} similar past task${r.work_items === 1 ? "" : "s"}`);
  if (r.preferences) parts.push(`${r.preferences} preference${r.preferences === 1 ? "" : "s"}`);
  if (r.memories) parts.push(`${r.memories} saved fact${r.memories === 1 ? "" : "s"}`);
  return parts.length ? `Recalled ${parts.join(", ")}` : "";
}

export default function ChatPage() {
  const { assistant, refresh: refreshAssistant } = useAssistant();
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  // "Don't use memory" chosen before the first message of a new chat; it travels with that message.
  const [draftMemoryOff, setDraftMemoryOff] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const [notices, setNotices] = useState<string[]>([]);
  const [verifyLine, setVerifyLine] = useState("");
  const [recallLineText, setRecallLineText] = useState("");
  const [step, setStep] = useState(1);
  const [deviceControl, setDeviceControl] = useState(false);
  const [learned, setLearned] = useState("");
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [panel, setPanel] = useState<TaskPanelState>(EMPTY_PANEL);
  const [stopping, setStopping] = useState(false);
  // A task another request is still running: shown, polled, not an error.
  const [watchId, setWatchId] = useState<string | null>(null);
  const [banner, setBanner] = useState("");
  // Set when the automatic continue cap was reached with work still to do.
  const [capped, setCapped] = useState<string | null>(null);
  // Shown under the thread until the next message. It used to be appended to
  // the live draft, which disappears the moment streaming stops — so every
  // failure was invisible and the question just sat there unanswered.
  const [failure, setFailure] = useState<{ message: string; retry: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Mirrors `streaming` for code that runs outside a render (event handlers
  // registered once, a quick double Enter before React re-renders).
  const streamingRef = useRef(false);
  const sendRef = useRef<(text?: string) => Promise<void>>(async () => {});
  const continueRef = useRef<(id: string) => Promise<void>>(async () => {});
  const reloadRef = useRef<(id: string) => Promise<void>>(async () => {});

  const activeConvo = conversations.find((c) => c.id === activeId);
  const memoryOff = activeConvo ? !!activeConvo.memory_off : draftMemoryOff;
  const panelVisible = !!panel.workItemId;
  const taskLive = panelVisible && isLive(panel.status);
  const canStop = streaming || taskLive;

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await api.conversations());
      refreshConversations();
    } catch {
      /* the sidebar list just stays as it was */
    }
  }, []);

  const loadPending = useCallback(async (fresh = false) => {
    try {
      setPending(await api.pendingActions(fresh));
    } catch {
      /* not fatal */
    }
  }, []);

  useEffect(() => {
    void loadConversations();
    void loadPending();
    api
      .authConfig()
      .then((c) => setDeviceControl(c.device_control))
      .catch(() => {
        /* the device quick action just stays hidden */
      });
  }, [loadConversations, loadPending]);

  // ?c= opens a conversation (notification links use it). ?q= only fills in
  // the composer: a question that arrives in a link is never sent until the
  // person presses Enter, because anyone can write a link. Questions typed
  // into the command palette arrive through this tab's storage instead.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const convo = params.get("c");
    const prefill = params.get("q");
    if (convo || prefill !== null) window.history.replaceState({}, "", "/chat");
    if (convo) {
      void openConversation(convo);
    } else if (prefill) {
      setInput(prefill);
      textareaRef.current?.focus();
    }

    const take = () => {
      const question = takeHandedOffQuestion();
      if (!question) return;
      if (streamingRef.current) setInput(question);
      else void sendRef.current(question);
    };
    if (!convo) take();
    window.addEventListener(ASK_EVENT, take);
    return () => window.removeEventListener(ASK_EVENT, take);
    // Intentionally runs once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, draft, liveSteps, failure, notices]);

  // Grow the composer with its content, up to its max height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const resetLive = () => {
    setDraft("");
    setLiveSteps([]);
    setNotices([]);
    setVerifyLine("");
    setRecallLineText("");
    setLearned("");
    setStep(1);
  };

  const reloadThread = useCallback(async (id: string) => {
    try {
      setMessages((await api.conversation(id)).messages ?? []);
    } catch {
      /* keep what is on screen */
    }
  }, []);
  reloadRef.current = reloadThread;

  /** Every event of section 8 lands here, for a new message and for a continued task alike. */
  const applyEvent = (e: StreamEvent, ctx: RunCtx) => {
    switch (e.type) {
      case "start":
        ctx.convoId = e.conversation_id;
        setActiveId(e.conversation_id);
        break;
      case "status":
        if (e.step) setStep(e.step);
        break;
      case "recall": {
        setRecallLineText(recallText(e));
        setPanel((p) => ({ ...p, recall: { work_items: e.work_items, preferences: e.preferences, memories: e.memories } }));
        break;
      }
      case "task":
        ctx.workItemId = e.work_item_id;
        setPanel((p) =>
          p.workItemId === e.work_item_id
            ? { ...p, title: e.title, taskType: e.task_type, status: e.status }
            : { ...p, workItemId: e.work_item_id, title: e.title, taskType: e.task_type, status: e.status, plan: [], systems: [], outcome: "", verification: "" }
        );
        break;
      case "plan":
        ctx.workItemId = e.work_item_id;
        setPanel((p) => ({ ...p, workItemId: e.work_item_id, plan: e.steps }));
        break;
      case "step_started":
        setLiveSteps((l) => [
          ...l.filter((x) => x.seq !== e.seq),
          { seq: e.seq, tool: e.tool, label: e.label, code: "", done: false, ok: true, summary: "" },
        ]);
        if (e.system) {
          setPanel((p) => (p.systems.includes(e.system) ? p : { ...p, systems: [...p.systems, e.system] }));
        }
        break;
      case "step_finished":
        setLiveSteps((l) =>
          l.some((x) => x.seq === e.seq)
            ? l.map((x) => (x.seq === e.seq ? { ...x, done: true, ok: e.ok, summary: e.summary, code: e.code } : x))
            : [...l, { seq: e.seq, tool: e.tool, label: e.tool, code: e.code, done: true, ok: e.ok, summary: e.summary }]
        );
        break;
      case "approval_needed":
        setPanel((p) => ({ ...p, status: "waiting" }));
        void loadPending(true);
        refreshBadges();
        break;
      case "waiting":
        setPanel((p) => ({ ...p, status: "waiting" }));
        void loadPending(true);
        refreshBadges();
        break;
      case "reviewer_block":
        setNotices((n) => [
          ...n,
          `A safety check stopped “${e.tool.replace(/_/g, " ")}”: ${e.reason}`,
        ]);
        break;
      case "verification":
        setVerifyLine(`${e.passed ? "Checked" : "Check failed"}: ${e.evidence || e.method}`);
        break;
      case "preference_noted":
        setLearned(e.text);
        setPanel((p) => ({ ...p, noted: [...p.noted, e.text] }));
        break;
      case "continue":
        ctx.more = true;
        break;
      case "report":
        setPanel((p) => ({
          ...p,
          status: e.status,
          title: e.title || p.title,
          outcome: e.outcome,
          verification: e.verification,
        }));
        break;
      case "delta":
        setDraft((d) => d + e.value);
        break;
      case "done":
        ctx.finished = true;
        if (e.work_status) setPanel((p) => ({ ...p, status: e.work_status as TaskPanelState["status"] }));
        break;
      case "error":
        ctx.error = e.message;
        break;
    }
  };

  /**
   * One run on screen: a new message, or a task being carried on. Keeps the
   * Stop button, the live lines and the panel in step, and follows `continue`
   * signals up to the cap in lib/taskrun.ts.
   */
  const runFlow = async (opts: { content?: string; continueId?: string; convoId?: string }) => {
    if (streamingRef.current) return;
    const content = opts.content;
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    streamingRef.current = true;
    setStreaming(true);
    setStopping(false);
    resetLive();
    setFailure(null);
    setBanner("");
    setCapped(null);
    if (content !== undefined) {
      setInput("");
      setPanel(EMPTY_PANEL);
      setMessages((prev) => [
        ...prev,
        { id: `local-${Date.now()}`, role: "user", content, meta: {}, created_at: new Date().toISOString() },
      ]);
    }

    const startsWithMemoryOff = activeId === null && draftMemoryOff && content !== undefined;
    const ctx: RunCtx = { convoId: opts.convoId ?? activeId, error: "", finished: false, more: false, workItemId: opts.continueId ?? null };
    let busyWith: string | null = null;
    let notSent = false;
    const onEvent = (e: StreamEvent) => applyEvent(e, ctx);

    const follow = async (id: string) => {
      const r = await driveTask(id, onEvent, ctrl.signal);
      if (r.busy) busyWith = id;
      if (r.capped) setCapped(id);
      if (r.error) ctx.error = r.error;
    };

    try {
      if (content !== undefined) {
        await streamChat(content, activeId, onEvent, ctrl.signal, startsWithMemoryOff);
        if (!ctx.finished && !ctx.error && !ctrl.signal.aborted) {
          ctx.error = "The reply was cut off before it finished. Please try again.";
        }
        if (ctx.more && ctx.workItemId && !ctx.error && !ctrl.signal.aborted) {
          ctx.more = false;
          await follow(ctx.workItemId);
        }
      } else if (opts.continueId) {
        await follow(opts.continueId);
      }
    } catch (err) {
      if (err instanceof StillWorkingError) {
        // Not a failure: the task is still running. Show it, and give the message back.
        notSent = true;
        busyWith = err.workItemId;
        setBanner("AURA is still working on this chat. Your message wasn't sent; it's back in the box for when the task finishes.");
      } else if (ctrl.signal.aborted || (err instanceof Error && err.name === "AbortError")) {
        ctx.error = "";
      } else {
        ctx.error = err instanceof Error ? err.message : "Couldn't reach your assistant. Please try again.";
      }
    }

    const convoId = ctx.convoId;
    let saved: Message[] | null = null;
    if (convoId) {
      try {
        saved = (await api.conversation(convoId)).messages ?? [];
      } catch {
        saved = null; // keep the local copy
      }
    }
    if (notSent && content !== undefined) {
      setInput(content);
      setMessages((prev) => prev.filter((m) => !(m.id.startsWith("local-") && m.role === "user" && m.content === content)));
    } else if (saved) {
      const kept = content === undefined || saved.some((m) => m.role === "user" && m.content === content);
      setMessages(
        kept || !ctx.error
          ? saved
          : [...saved, { id: `local-${Date.now()}`, role: "user", content: content!, meta: {}, created_at: new Date().toISOString() }]
      );
    }
    setFailure(ctx.error && content !== undefined ? { message: ctx.error, retry: content } : ctx.error ? { message: ctx.error, retry: "" } : null);
    resetLive();
    abortRef.current = null;
    streamingRef.current = false;
    setStreaming(false);
    setStopping(false);
    if (busyWith) setWatchId(busyWith);
    await Promise.all([loadConversations(), loadPending(true), refreshAssistant()]);
    // The new chat now exists and carries the flag itself.
    if (startsWithMemoryOff && ctx.convoId) setDraftMemoryOff(false);
  };

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streamingRef.current) return;
    await runFlow({ content });
  };
  sendRef.current = send;
  continueRef.current = (id: string) => runFlow({ continueId: id });

  const stop = async () => {
    const id = panel.workItemId;
    setStopping(true);
    abortRef.current?.abort();
    if (id) {
      try {
        const r = await api.stopWork(id);
        setPanel((p) => ({ ...p, status: r.status }));
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Couldn't stop that task");
      }
    }
    setWatchId(null);
    setStopping(false);
    if (activeId) await reloadThread(activeId);
    await Promise.all([loadPending(true), loadConversations()]);
    refreshBadges();
  };

  // A task another request holds: poll it until it stops running.
  useEffect(() => {
    if (!watchId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const d = await api.workItem(watchId);
        if (cancelled) return;
        setPanel(panelFromDetail(d));
        if (d.status !== "running") {
          setWatchId(null);
          if (d.conversation_id) await reloadRef.current(d.conversation_id);
          void loadPending(true);
          if (d.status === "ready" && !streamingRef.current) void continueRef.current(d.id);
        }
      } catch {
        /* try again on the next tick */
      }
    };
    void tick();
    const t = setInterval(() => void tick(), 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [watchId, loadPending]);

  // Switching thread mid-reply would pour the live reply into the other
  // thread and then overwrite it with this one, so the rail waits.
  const openConversation = async (id: string) => {
    if (streamingRef.current) return;
    setActiveId(id);
    setFailure(null);
    setBanner("");
    setCapped(null);
    setWatchId(null);
    setPanel(EMPTY_PANEL);
    try {
      const convo = await api.conversation(id);
      setMessages(convo.messages ?? []);
    } catch (e) {
      setMessages([]);
      toast.error(e instanceof Error ? e.message : "Couldn't open that conversation");
    }
    resetLive();

    // Show the task this conversation is on, and carry on any that are ready.
    try {
      const [items, ready] = await Promise.all([
        api.workItems({ conversation_id: id, limit: 10 }),
        api.workItems({ conversation_id: id, status: "ready" }),
      ]);
      const current = items.find((i) => isLive(i.status)) ?? items[0];
      if (current) {
        const detail = await api.workItem(current.id);
        setPanel(panelFromDetail(detail));
        if (detail.status === "running") setWatchId(detail.id);
      }
      for (const r of ready) {
        if (streamingRef.current) break;
        resetContinues(r.id);
        await runFlow({ continueId: r.id, convoId: id });
      }
    } catch {
      /* the thread is shown without a task panel */
    }
  };

  const newChat = () => {
    if (streamingRef.current) return;
    setActiveId(null);
    setDraftMemoryOff(false);
    setMessages([]);
    resetLive();
    setPanel(EMPTY_PANEL);
    setWatchId(null);
    setBanner("");
    setCapped(null);
    setFailure(null);
    textareaRef.current?.focus();
  };

  // The sidebar's "New task" and "Recent" list drive this screen by event.
  const shellRef = useRef({ open: openConversation, fresh: newChat, activeId });
  shellRef.current = { open: openConversation, fresh: newChat, activeId };
  useEffect(() => {
    const onShell = (ev: Event) => {
      const d = (ev as CustomEvent<ChatEventDetail>).detail;
      if (!d) return;
      if (d.type === "open") void shellRef.current.open(d.id);
      else if (d.type === "new") shellRef.current.fresh();
      else if (d.type === "deleted" && shellRef.current.activeId === d.id) shellRef.current.fresh();
    };
    window.addEventListener(CHAT_EVENT, onShell);
    return () => window.removeEventListener(CHAT_EVENT, onShell);
  }, []);

  // The card records the answer and hands the task to continueFor; the thread
  // then reloads to show what happened.
  const onDecided = async () => {
    await loadPending(true);
    refreshBadges();
    // Mid-run, the thread reloads itself when the run ends.
    if (activeId && !streamingRef.current) await openConversation(activeId);
  };

  const continueFor = (a: PendingAction) => (workItemId: string) => {
    // A card for another conversation's task is carried on quietly.
    if (a.conversation_id && a.conversation_id !== activeId) {
      return driveTask(workItemId).then(() => undefined);
    }
    return continueRef.current(workItemId);
  };

  const toggleMemory = async () => {
    if (!activeId) {
      // No chat yet: remember the choice and send it with the first message.
      setDraftMemoryOff((v) => !v);
      return;
    }
    const next = !memoryOff;
    setConversations((cs) => cs.map((c) => (c.id === activeId ? { ...c, memory_off: next } : c)));
    try {
      await api.setConversationMemoryOff(activeId, next);
      toast.success(next ? "AURA won't use memory in this chat" : "AURA can use memory in this chat");
    } catch (e) {
      setConversations((cs) => cs.map((c) => (c.id === activeId ? { ...c, memory_off: !next } : c)));
      toast.error(e instanceof Error ? e.message : "Couldn't change that");
    }
  };

  const activeTitle = activeConvo?.title;
  const taskPending = pending.filter((p) => p.work_item_id && p.work_item_id === panel.workItemId);

  return (
    <div className="flex h-full min-h-0 flex-col lg:flex-row">
      {panelVisible && (
        <div className="order-first shrink-0 lg:order-last lg:flex lg:min-h-0">
          <TaskPanel
            state={panel}
            waiting={taskPending}
            canStop={canStop}
            stopping={stopping}
            onStop={() => void stop()}
          />
        </div>
      )}
      {/* Thread */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 sm:py-8">
          <div className="mx-auto max-w-2xl space-y-5">
            {activeTitle && messages.length > 0 && (
              <h1 className="sr-only">{activeTitle}</h1>
            )}
            {messages.length === 0 && !draft && !streaming && !failure && (
              <div className="animate-fade-up pt-8 text-center sm:pt-14">
                <div className="mb-5 flex justify-center">
                  <Mascot
                    colourway={assistant?.avatar ?? "violet"}
                    size={82}
                  />
                </div>
                <h2 className="display text-shine mb-2 text-[22px]">
                  What do you need?
                </h2>
                <p className="mb-7 text-[13px] text-muted">
                  {`${assistant?.name ?? "Aura"} gets to work as soon as you ask: describe the task and it plans, acts, checks and reports back.`}
                </p>
                <div className="mx-auto flex max-w-md flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="min-h-[44px] rounded-full border border-line bg-raised/30 px-3 py-1.5 text-[12px] text-muted backdrop-blur-md transition hover:border-accent/40 hover:bg-raised/60 hover:text-ink"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) => (
              <MessageBubble key={m.id} message={m} />
            ))}

            {banner && (
              <div role="status" className="flex items-start gap-2 rounded-xl border border-amber/35 bg-amber/10 px-3 py-2 text-[12.5px] text-ink">
                <Spinner className="mt-0.5 text-amber" />
                <p className="min-w-0 flex-1 leading-relaxed">{banner}</p>
              </div>
            )}

            {watchId && !streaming && !banner && (
              <div role="status" className="flex items-center gap-2 text-[13px] text-muted">
                <Spinner /> AURA is still working on this. Updates appear here.
              </div>
            )}

            {/* Live turn */}
            {streaming && (
              <div className="animate-fade-up space-y-2" aria-live="polite">
                {learned && (
                  <div className="flex items-center gap-2 rounded-xl border border-sage/30 bg-sage/10 px-3 py-2 text-[12.5px] text-sage">
                    <GraduationCap size={13} />
                    Noted: {learned}
                  </div>
                )}
                {(liveSteps.length > 0 || recallLineText) && (
                  <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
                    {recallLineText && <SkillLine code="MM01" summary={recallLineText} />}
                    {liveSteps.map((s) =>
                      s.done ? (
                        <SkillLine key={s.seq} code={s.code || s.tool} summary={s.summary || s.label} ok={s.ok} />
                      ) : (
                        <div key={s.seq} className="flex items-center gap-2 py-1 text-[12.5px] text-muted">
                          <Spinner className="h-3 w-3 text-accent-soft" />
                          <span className="min-w-0 truncate">{s.label}…</span>
                        </div>
                      )
                    )}
                  </div>
                )}
                {notices.map((n, i) => (
                  <div key={i} role="status" className="flex items-start gap-2 rounded-xl border border-amber/35 bg-amber/10 px-3 py-2 text-[12.5px] text-ink">
                    <ShieldAlert size={14} className="mt-0.5 shrink-0 text-amber" aria-hidden />
                    <span className="min-w-0 flex-1">{n}</span>
                  </div>
                ))}
                {verifyLine && <div className="text-[12px] text-muted">{verifyLine}</div>}
                {draft ? (
                  <Markdown text={draft} />
                ) : liveSteps.some((s) => !s.done) ? null : (
                  <div className="flex items-center gap-2 text-[13px] text-muted">
                    <Spinner /> {step > 1 ? `Working it out — step ${step}…` : "Thinking…"}
                  </div>
                )}
              </div>
            )}

            {capped && !streaming && (
              <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-raised/50 px-3 py-2 text-[12.5px] text-muted">
                <p className="min-w-0 flex-1">This task has run for a while. It paused so you can check in.</p>
                <button
                  className="btn-ghost min-h-[44px]"
                  onClick={() => {
                    resetContinues(capped);
                    void continueRef.current(capped);
                  }}
                >
                  Keep going
                </button>
              </div>
            )}

            {failure && !streaming && (
              <div
                role="alert"
                className="flex flex-wrap items-start gap-3 rounded-2xl border border-rose/35 bg-rose/10 px-4 py-3 text-[13px] text-rose"
              >
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <p className="min-w-0 flex-1 leading-relaxed">{failure.message}</p>
                {failure.retry && (
                  <button
                    onClick={() => {
                      const retry = failure.retry;
                      // Drop the unanswered copy; send() adds it back.
                      setMessages((prev) => {
                        const i = prev.map((m) => m.content).lastIndexOf(retry);
                        return i >= 0 && prev[i].id.startsWith("local-")
                          ? prev.filter((_, j) => j !== i)
                          : prev;
                      });
                      void send(retry);
                    }}
                    className="btn-ghost min-h-[44px] shrink-0 py-1 text-[12px] text-ink"
                  >
                    <RotateCcw size={12} /> Try again
                  </button>
                )}
              </div>
            )}

            {pending.length > 0 && (
              <div className="space-y-3" aria-label="Waiting for your answer">
                <div className="label text-amber">Needs your say-so</div>
                {pending.map((p) => (
                  <ApprovalCard
                    key={p.id}
                    action={p}
                    onDecided={onDecided}
                    onContinue={continueFor(p)}
                  />
                ))}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        {/* Composer */}
        <div className="border-t border-line bg-canvas/40 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur-xl sm:px-6 sm:pt-4">
          <div className="mx-auto max-w-2xl">
            {messages.length > 0 && !streaming && (
              <div
                className="-mx-1 mb-2.5 flex gap-1.5 overflow-x-auto px-1 pb-0.5"
                aria-label="Quick actions"
              >
                <Zap size={12} className="mt-3 shrink-0 text-faint" aria-hidden />
                {[...QUICK_ACTIONS, ...(deviceControl ? [DEVICE_QUICK_ACTION] : [])].map((q) => (
                  <button
                    key={q}
                    onClick={() => send(q)}
                    className="min-h-[44px] shrink-0 rounded-full border border-line bg-raised/30 px-3 py-1 text-[11.5px] text-muted transition hover:border-accent/40 hover:text-ink"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
            <div className="panel p-2 transition-colors focus-within:border-accent/40">
              <div className="flex items-end gap-2">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    // isComposing: Enter that confirms an IME candidate (Hindi,
                    // Japanese…) must not send a half-typed message.
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                  rows={1}
                  placeholder={`Ask ${assistant?.name ?? "Aura"} anything…`}
                  aria-label={`Message ${assistant?.name ?? "Aura"}`}
                  className="max-h-40 min-h-[44px] flex-1 resize-none bg-transparent px-3 py-2.5 text-[13.5px] placeholder:text-faint outline-none"
                />
                {canStop && (
                  <button
                    type="button"
                    onClick={() => void stop()}
                    disabled={stopping}
                    aria-label="Stop the task"
                    title="Stop the task"
                    className="btn-ghost h-11 w-11 shrink-0 rounded-full p-0"
                  >
                    <Square size={14} aria-hidden />
                  </button>
                )}
                <button
                  onClick={() => send()}
                  disabled={!input.trim() || streaming}
                  aria-label="Send"
                  className="btn-primary h-11 w-11 shrink-0 rounded-full p-0"
                >
                  {streaming ? <Spinner /> : <ArrowUp size={16} />}
                </button>
              </div>
              <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3 border-t border-line px-1 pt-1.5">
                <ModePicker />
                <div className="flex min-h-[44px] items-center gap-2">
                  <span id="memory-off-label" className="text-[12px] text-muted">
                    Don&apos;t use memory in this chat
                  </span>
                  <Toggle
                    checked={memoryOff}
                    onChange={() => void toggleMemory()}
                    label="Do not use memory in this chat"
                  />
                </div>
              </div>
            </div>
            <p className="mt-2 hidden text-center text-[11px] text-faint sm:block">
              Correct {assistant?.name ?? "Aura"} and it remembers. Enter to send, Shift+Enter
              for a new line.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";
  const skillRuns: LiveSkill[] = message.meta?.skill_runs ?? [];
  const memoriesUsed: string[] = message.meta?.memories_used ?? [];
  const notedRaw: unknown = message.meta?.preferences_noted;
  const learned: string = Array.isArray(notedRaw)
    ? notedRaw
        .map((n) => (typeof n === "string" ? n : (n as { text?: string })?.text ?? ""))
        .filter((t) => t && !/^[0-9a-f-]{36}$/.test(t))
        .join("; ")
    : "";

  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-gradient-to-br from-accent-soft to-accent px-4 py-2.5 text-[13.5px] leading-relaxed text-white shadow-glow-sm sm:max-w-[80%]">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-up space-y-2">
      {learned && (
        <div className="flex items-center gap-2 rounded-xl border border-sage/30 bg-sage/10 px-3 py-2 text-[12.5px] text-sage">
          <GraduationCap size={13} /> Noted: {learned}
        </div>
      )}
      {(skillRuns.length > 0 || memoriesUsed.length > 0) && (
        <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
          {showRecall(memoriesUsed.length, skillRuns) && (
            <SkillLine code="MM01" summary={recallLine(memoriesUsed.length)} />
          )}
          {skillRuns.map((s, i) => (
            <SkillLine key={i} code={s.code} summary={s.summary} ok={s.ok} />
          ))}
        </div>
      )}
      <Markdown text={message.content} />
      {skillRuns.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {screensFor(skillRuns.map((s) => s.code)).map((screen) => (
            <Link
              key={screen.href}
              href={screen.href}
              className="inline-flex items-center gap-1 rounded-full border border-line bg-raised/40 px-2.5 py-1 text-[11.5px] text-muted transition hover:border-accent/40 hover:text-ink"
            >
              Open {screen.label} <ArrowRight size={11} />
            </Link>
          ))}
        </div>
      )}
      {message.meta?.latency_ms ? (
        <div className="flex items-center gap-1.5 text-[10.5px] text-faint">
          <Sparkles size={9} />
          {fmtRelative(message.created_at)} · {message.meta.steps} step
          {message.meta.steps === 1 ? "" : "s"} · {message.meta.latency_ms}ms
        </div>
      ) : null}
    </div>
  );
}
