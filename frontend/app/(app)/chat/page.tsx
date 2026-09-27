"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Check,
  Clock4,
  GraduationCap,
  History,
  Pin,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import {
  Conversation,
  Decision,
  Message,
  PendingAction,
  api,
  streamChat,
  StreamEvent,
} from "@/lib/api";
import { useAssistant } from "@/lib/assistant";
import Mascot from "@/components/Mascot";
import { useToast } from "@/components/Toast";
import {
  Badge,
  Markdown,
  SkillBadge,
  SkillLine,
  Spinner,
  fmtRelative,
  refreshBadges,
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

export default function ChatPage() {
  const { assistant, refresh: refreshAssistant } = useAssistant();
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [liveSkills, setLiveSkills] = useState<LiveSkill[]>([]);
  const [memoryHits, setMemoryHits] = useState(0);
  const [learned, setLearned] = useState("");
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [deciding, setDeciding] = useState<string | null>(null);
  // Shown under the thread until the next message. It used to be appended to
  // the live draft, which disappears the moment streaming stops — so every
  // failure was invisible and the question just sat there unanswered.
  const [failure, setFailure] = useState<{ message: string; retry: string } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await api.conversations());
    } catch {
      /* the rail just stays as it was */
    }
  }, []);

  const loadPending = useCallback(async () => {
    try {
      setPending(await api.pendingActions());
    } catch {
      /* not fatal */
    }
  }, []);

  useEffect(() => {
    void loadConversations();
    void loadPending();
  }, [loadConversations, loadPending]);

  // The command palette hands off here with ?q=… ; consume it once and clear
  // it from the URL so a refresh doesn't re-send the same question.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const handoff = params.get("q");
    const convo = params.get("c");
    if (convo) {
      window.history.replaceState({}, "", "/chat");
      void openConversation(convo);
      return;
    }
    if (handoff) {
      window.history.replaceState({}, "", "/chat");
      void send(handoff);
    }
    // Intentionally runs once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, draft, liveSkills, failure]);

  // Grow the composer with its content, up to its max height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  const openConversation = async (id: string) => {
    setActiveId(id);
    setFailure(null);
    try {
      const convo = await api.conversation(id);
      setMessages(convo.messages ?? []);
    } catch (e) {
      setMessages([]);
      toast.error(e instanceof Error ? e.message : "Couldn't open that conversation");
    }
    setDraft("");
    setLiveSkills([]);
    setLearned("");
  };

  const newChat = () => {
    setActiveId(null);
    setMessages([]);
    setDraft("");
    setLiveSkills([]);
    setLearned("");
    setFailure(null);
    textareaRef.current?.focus();
  };

  const removeConversation = async (c: Conversation) => {
    if (!window.confirm(`Delete "${c.title}"? This can't be undone.`)) return;
    try {
      await api.deleteConversation(c.id);
      if (activeId === c.id) newChat();
      await loadConversations();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete that conversation");
    }
  };

  const send = async (text?: string) => {
    const content = (text ?? input).trim();
    if (!content || streaming) return;

    setInput("");
    setStreaming(true);
    setDraft("");
    setLiveSkills([]);
    setMemoryHits(0);
    setLearned("");
    setFailure(null);
    setMessages((prev) => [
      ...prev,
      {
        id: `local-${Date.now()}`,
        role: "user",
        content,
        meta: {},
        created_at: new Date().toISOString(),
      },
    ]);

    let convoId = activeId;
    let streamError = "";
    let finished = false;
    try {
      await streamChat(content, activeId, (e: StreamEvent) => {
        switch (e.type) {
          case "start":
            convoId = e.conversation_id;
            setActiveId(e.conversation_id);
            break;
          case "skill":
            setLiveSkills((s) => [
              ...s,
              { code: e.code, name: e.name, summary: e.summary, ok: e.ok },
            ]);
            break;
          case "learned":
            setLearned(e.value);
            break;
          case "memory":
            setMemoryHits(e.count);
            break;
          case "pending_action":
            void loadPending();
            refreshBadges();
            break;
          case "delta":
            setDraft((d) => d + e.value);
            break;
          case "done":
            finished = true;
            break;
          case "error":
            streamError = e.message;
            break;
        }
      });
      if (!finished && !streamError) {
        streamError = "The reply was cut off before it finished. Please try again.";
      }
    } catch (err) {
      streamError =
        err instanceof Error ? err.message : "Couldn't reach your assistant. Please try again.";
    }

    // Reload what the server kept, so the thread shows the saved turn (and
    // the new title in the rail) rather than a local copy. Everything below is
    // set in one go, so the live turn and the saved one never show together.
    let saved: Message[] | null = null;
    if (convoId) {
      try {
        saved = (await api.conversation(convoId)).messages ?? [];
      } catch {
        saved = null; // keep the local copy
      }
    }
    if (saved) {
      const kept = saved.some((m) => m.role === "user" && m.content === content);
      setMessages(
        kept || !streamError
          ? saved
          : [
              ...saved,
              {
                id: `local-${Date.now()}`,
                role: "user",
                content,
                meta: {},
                created_at: new Date().toISOString(),
              },
            ]
      );
    }
    setFailure(streamError ? { message: streamError, retry: content } : null);
    setDraft("");
    setLiveSkills([]);
    setLearned("");
    setStreaming(false);
    await Promise.all([loadConversations(), loadPending(), refreshAssistant()]);
  };

  const decide = async (id: string, decision: Decision) => {
    setDeciding(id);
    try {
      await api.decidePending(id, decision);
      await loadPending();
      refreshBadges();
      if (activeId) await openConversation(activeId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't record that decision");
    } finally {
      setDeciding(null);
    }
  };

  const activeTitle = conversations.find((c) => c.id === activeId)?.title;

  return (
    <div className="flex h-full min-h-0">
      {/* Conversation rail */}
      <div className="hidden w-[218px] shrink-0 flex-col border-r border-line bg-panel/30 p-3 backdrop-blur-xl lg:flex">
        <button onClick={newChat} className="btn-ghost mb-3 w-full justify-start">
          <Plus size={14} /> New chat
        </button>
        <div className="flex-1 space-y-0.5 overflow-y-auto">
          {conversations.length === 0 && (
            <p className="px-2.5 py-2 text-[12px] text-faint">Past conversations appear here.</p>
          )}
          {conversations.map((c) => (
            <div
              key={c.id}
              className={`group flex items-center gap-1.5 rounded-xl px-2.5 py-2 text-[12.5px] transition ${
                activeId === c.id
                  ? "border border-accent/25 bg-accent/12 font-medium text-ink"
                  : "border border-transparent text-muted hover:bg-raised/60 hover:text-ink"
              }`}
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
        </div>
      </div>

      {/* Thread */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Below lg the rail is hidden, so history and "new chat" live here.
            Without this there was no way to start over or reopen a
            conversation on a phone. */}
        <div className="flex items-center gap-2 border-b border-line px-4 py-2 lg:hidden">
          <History size={14} className="shrink-0 text-faint" />
          <select
            className="min-w-0 flex-1 truncate bg-transparent py-1 text-[13px] text-muted outline-none"
            value={activeId ?? ""}
            onChange={(e) => (e.target.value ? void openConversation(e.target.value) : newChat())}
            aria-label="Conversation"
          >
            <option value="" className="bg-panel">
              {activeId ? "New conversation" : "Past conversations…"}
            </option>
            {conversations.map((c) => (
              <option key={c.id} value={c.id} className="bg-panel">
                {c.title}
              </option>
            ))}
          </select>
          <button onClick={newChat} className="btn-ghost shrink-0 px-3 py-1.5 text-[12.5px]">
            <Plus size={13} /> New
          </button>
        </div>

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
                    stage={assistant?.stage ?? "stranger"}
                    size={82}
                  />
                </div>
                <h2 className="display text-shine mb-2 text-[22px]">
                  What do you need?
                </h2>
                <p className="mb-7 text-[13px] text-muted">
                  {assistant?.stage === "stranger"
                    ? `${assistant.name} is new here — tell them how you work and it'll stick.`
                    : `${assistant?.name ?? "Aura"} can read your inbox, hold your calendar and act for you.`}
                </p>
                <div className="mx-auto flex max-w-md flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => send(s)}
                      className="rounded-full border border-line bg-raised/30 px-3 py-1.5 text-[12px] text-muted backdrop-blur-md transition hover:border-accent/40 hover:bg-raised/60 hover:text-ink"
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

            {/* Live turn */}
            {streaming && (
              <div className="animate-fade-up space-y-2" aria-live="polite">
                {learned && (
                  <div className="flex items-center gap-2 rounded-xl border border-sage/30 bg-sage/10 px-3 py-2 text-[12.5px] text-sage">
                    <GraduationCap size={13} />
                    Learned: {learned}
                  </div>
                )}
                {(liveSkills.length > 0 || memoryHits > 0) && (
                  <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
                    {memoryHits > 0 && (
                      <SkillLine
                        code="MM01"
                        summary={`Recalled ${memoryHits} relevant memor${memoryHits === 1 ? "y" : "ies"}`}
                      />
                    )}
                    {liveSkills.map((s, i) => (
                      <SkillLine key={i} code={s.code} summary={s.summary} ok={s.ok} />
                    ))}
                  </div>
                )}
                {draft ? (
                  <Markdown text={draft} />
                ) : (
                  <div className="flex items-center gap-2 text-[13px] text-faint">
                    <Spinner /> Thinking…
                  </div>
                )}
              </div>
            )}

            {failure && !streaming && (
              <div
                role="alert"
                className="flex flex-wrap items-start gap-3 rounded-2xl border border-rose/35 bg-rose/10 px-4 py-3 text-[13px] text-rose"
              >
                <AlertTriangle size={15} className="mt-0.5 shrink-0" />
                <p className="min-w-0 flex-1 leading-relaxed">{failure.message}</p>
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
                  className="btn-ghost shrink-0 py-1 text-[12px] text-ink"
                >
                  <RotateCcw size={12} /> Try again
                </button>
              </div>
            )}

            {pending.length > 0 && (
              <div className="space-y-3 rounded-2xl border border-amber/35 bg-amber/[0.07] p-4">
                <div className="label text-amber">Needs your say-so</div>
                {pending.map((p) => (
                  <div key={p.id} className="space-y-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      {p.skill_code && <SkillBadge code={p.skill_code} />}
                      <Badge tone="warning">{p.tool_name}</Badge>
                    </div>
                    <pre className="whitespace-pre-wrap break-words font-sans text-[12.5px] leading-relaxed text-muted">
                      {p.preview}
                    </pre>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => decide(p.id, "once")}
                        disabled={deciding === p.id}
                        className="btn-primary py-1.5"
                      >
                        {deciding === p.id ? <Spinner /> : <Check size={13} />} Allow once
                      </button>
                      <button
                        onClick={() => decide(p.id, "window")}
                        disabled={deciding === p.id}
                        className="btn-ghost py-1.5"
                      >
                        <Clock4 size={13} /> 10 min
                      </button>
                      <button
                        onClick={() => decide(p.id, "always")}
                        disabled={deciding === p.id}
                        className="btn-ghost py-1.5"
                      >
                        Always
                      </button>
                      <button
                        onClick={() => decide(p.id, "reject")}
                        disabled={deciding === p.id}
                        className="btn-quiet py-1.5"
                      >
                        <X size={13} /> Don&apos;t
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div ref={bottomRef} />
          </div>
        </div>

        {/* Composer */}
        <div className="border-t border-line bg-canvas/40 px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 backdrop-blur-xl sm:px-6 sm:pt-4">
          <div className="mx-auto max-w-2xl">
            <div className="panel flex items-end gap-2 p-2 transition-colors focus-within:border-accent/40">
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
                className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2.5 text-[13.5px] placeholder:text-faint outline-none"
              />
              <button
                onClick={() => send()}
                disabled={!input.trim() || streaming}
                aria-label="Send"
                className="btn-primary h-9 w-9 shrink-0 rounded-full p-0"
              >
                {streaming ? <Spinner /> : <ArrowUp size={16} />}
              </button>
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
  const learned: string = message.meta?.learned ?? "";

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
          <GraduationCap size={13} /> Learned: {learned}
        </div>
      )}
      {(skillRuns.length > 0 || memoriesUsed.length > 0) && (
        <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
          {memoriesUsed.length > 0 && (
            <SkillLine
              code="MM01"
              summary={`Recalled ${memoriesUsed.length} relevant memor${memoriesUsed.length === 1 ? "y" : "ies"}`}
            />
          )}
          {skillRuns.map((s, i) => (
            <SkillLine key={i} code={s.code} summary={s.summary} ok={s.ok} />
          ))}
        </div>
      )}
      <Markdown text={message.content} />
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
