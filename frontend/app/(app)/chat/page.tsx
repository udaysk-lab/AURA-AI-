"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  Check,
  Clock4,
  GraduationCap,
  Pin,
  Plus,
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
import {
  Badge,
  Markdown,
  SkillBadge,
  SkillLine,
  Spinner,
  fmtRelative,
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
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async () => {
    setConversations(await api.conversations());
  }, []);

  const loadPending = useCallback(async () => {
    setPending(await api.pendingActions());
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
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, draft, liveSkills]);

  const openConversation = async (id: string) => {
    setActiveId(id);
    const convo = await api.conversation(id);
    setMessages(convo.messages ?? []);
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
    textareaRef.current?.focus();
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
            break;
          case "delta":
            setDraft((d) => d + e.value);
            break;
          case "error":
            setDraft((d) => d + `\n\n_Error: ${e.message}_`);
            break;
        }
      });
      if (convoId) await openConversation(convoId);
      await loadConversations();
      await loadPending();
      await refreshAssistant();
    } catch (err) {
      setDraft(
        (d) =>
          d +
          `\n\n_Couldn't reach your assistant: ${
            err instanceof Error ? err.message : "unknown error"
          }_`
      );
    } finally {
      setStreaming(false);
    }
  };

  const decide = async (id: string, decision: Decision) => {
    await api.decidePending(id, decision);
    await loadPending();
    if (activeId) await openConversation(activeId);
  };

  return (
    <div className="flex h-screen">
      {/* Conversation rail */}
      <div className="hidden w-[218px] shrink-0 flex-col border-r border-line bg-panel/30 p-3 backdrop-blur-xl lg:flex">
        <button onClick={newChat} className="btn-ghost mb-3 w-full justify-start">
          <Plus size={14} /> New chat
        </button>
        <div className="flex-1 space-y-0.5 overflow-y-auto">
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
              >
                {c.pinned && <Pin size={10} className="mr-1 inline text-accent-soft" />}
                {c.title}
              </button>
              <button
                onClick={async () => {
                  await api.deleteConversation(c.id);
                  if (activeId === c.id) newChat();
                  await loadConversations();
                }}
                className="opacity-0 transition group-hover:opacity-100"
              >
                <Trash2 size={12} className="text-faint hover:text-rose" />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Thread */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex-1 overflow-y-auto px-6 py-8">
          <div className="mx-auto max-w-2xl space-y-5">
            {messages.length === 0 && !draft && (
              <div className="animate-fade-up pt-14 text-center">
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
              <div className="animate-fade-up space-y-2">
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
                        summary={`Recalled ${memoryHits} relevant memories`}
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
                        className="btn-primary py-1.5"
                      >
                        <Check size={13} /> Allow once
                      </button>
                      <button
                        onClick={() => decide(p.id, "window")}
                        className="btn-ghost py-1.5"
                      >
                        <Clock4 size={13} /> 10 min
                      </button>
                      <button
                        onClick={() => decide(p.id, "always")}
                        className="btn-ghost py-1.5"
                      >
                        Always
                      </button>
                      <button
                        onClick={() => decide(p.id, "reject")}
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
        <div className="border-t border-line bg-canvas/40 px-6 py-4 backdrop-blur-xl">
          <div className="mx-auto max-w-2xl">
            <div className="panel flex items-end gap-2 p-2 transition-colors focus-within:border-accent/40">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send();
                  }
                }}
                rows={1}
                placeholder={`Ask ${assistant?.name ?? "Aura"} anything…`}
                className="max-h-40 flex-1 resize-none bg-transparent px-3 py-2.5 text-[13.5px] placeholder:text-faint outline-none"
              />
              <button
                onClick={() => send()}
                disabled={!input.trim() || streaming}
                className="btn-primary h-9 w-9 rounded-full p-0"
              >
                {streaming ? <Spinner /> : <ArrowUp size={16} />}
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-faint">
              Correct {assistant?.name ?? "Aura"} and it remembers. Nothing irreversible
              happens without you.
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
        <div className="max-w-[80%] rounded-2xl rounded-br-md bg-gradient-to-br from-accent-soft to-accent px-4 py-2.5 text-[13.5px] leading-relaxed text-white shadow-glow-sm">
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
              summary={`Recalled ${memoriesUsed.length} relevant memories`}
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
