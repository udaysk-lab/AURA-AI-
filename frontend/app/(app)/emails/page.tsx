"use client";

import { useCallback, useEffect, useState } from "react";
import { Archive, Inbox, RefreshCw, Search, Sparkles, Wand2 } from "lucide-react";
import { Email, InboxSummary, api } from "@/lib/api";
import {
  Badge,
  Card,
  EmptyState,
  Modal,
  SectionTitle,
  Skeleton,
  Spinner,
  fmtRelative,
} from "@/components/ui";

const FOLDERS = [
  { key: "inbox", label: "Inbox" },
  { key: "unread", label: "Unread" },
  { key: "important", label: "Important" },
  { key: "archive", label: "Archive" },
];

export default function EmailsPage() {
  const [folder, setFolder] = useState("inbox");
  const [query, setQuery] = useState("");
  const [emails, setEmails] = useState<Email[]>([]);
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  const [selected, setSelected] = useState<Email | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);

  const [draftOpen, setDraftOpen] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [instruction, setInstruction] = useState("Write a concise, professional reply.");
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, sum] = await Promise.all([
        api.emails(folder, query),
        api.inboxSummary(),
      ]);
      setEmails(list);
      setSummary(sum);
      setSelected((prev) => (prev && list.some((e) => e.id === prev.id) ? prev : list[0] ?? null));
    } finally {
      setLoading(false);
    }
  }, [folder, query]);

  useEffect(() => {
    void load();
  }, [load]);

  const open = async (email: Email) => {
    setSelected(email);
    if (!email.is_read) {
      const full = await api.email(email.id);
      setSelected(full);
      setEmails((prev) => prev.map((e) => (e.id === full.id ? full : e)));
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await api.syncEmail();
      await load();
    } finally {
      setSyncing(false);
    }
  };

  const archive = async (email: Email) => {
    await api.archiveEmail(email.id);
    setSelected(null);
    await load();
  };

  const generateDraft = async () => {
    if (!selected) return;
    setDraftBusy(true);
    try {
      setDraft(await api.draftReply(selected.id, instruction));
    } finally {
      setDraftBusy(false);
    }
  };

  return (
    <div className="flex h-screen">
      {/* List */}
      <div className="flex w-[380px] shrink-0 flex-col border-r border-line bg-panel/30 backdrop-blur-xl">
        <div className="space-y-3 border-b border-line p-4">
          <div className="flex items-center justify-between">
            <h1 className="text-[16px] font-semibold tracking-tight text-ink">Email</h1>
            <button onClick={sync} disabled={syncing} className="btn-quiet px-2 py-1">
              {syncing ? <Spinner /> : <RefreshCw size={14} />}
            </button>
          </div>
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint" />
            <input
              className="input py-2 pl-8 text-[13px]"
              placeholder="Search email…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="flex gap-1">
            {FOLDERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFolder(f.key)}
                className={`rounded-lg px-2.5 py-1 text-[12px] transition ${
                  folder === f.key
                    ? "border border-accent/25 bg-accent/12 text-ink"
                    : "border border-transparent text-muted hover:text-ink"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading ? (
            <div className="space-y-2 p-4">
              {[0, 1, 2, 3, 4].map((i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : emails.length === 0 ? (
            <div className="p-4">
              <EmptyState title="Nothing here" icon={<Inbox size={18} />} />
            </div>
          ) : (
            emails.map((e) => (
              <button
                key={e.id}
                onClick={() => open(e)}
                className={`relative w-full border-b border-line px-4 py-3 text-left transition ${
                  selected?.id === e.id
                    ? "bg-accent/10 before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent-soft"
                    : "hover:bg-raised/50"
                }`}
              >
                <div className="mb-1 flex items-center gap-2">
                  {!e.is_read && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent shadow-glow-sm" />
                  )}
                  <span
                    className={`min-w-0 flex-1 truncate text-[12.5px] ${
                      e.is_read ? "text-zinc-500" : "font-medium text-zinc-200"
                    }`}
                  >
                    {e.sender_name || e.sender}
                  </span>
                  <span className="shrink-0 text-[10.5px] text-zinc-700">
                    {fmtRelative(e.received_at)}
                  </span>
                </div>
                <div
                  className={`mb-1 truncate text-[13px] ${
                    e.is_read ? "text-zinc-400" : "font-medium text-ink"
                  }`}
                >
                  {e.subject}
                </div>
                <div className="flex items-center gap-1.5">
                  {(e.importance === "urgent" || e.importance === "high") && (
                    <Badge tone={e.importance}>{e.importance}</Badge>
                  )}
                  {e.needs_reply && <Badge tone="accent">reply</Badge>}
                  {e.category && <span className="text-[10.5px] text-zinc-700">{e.category}</span>}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Detail */}
      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex h-full items-center justify-center">
            <EmptyState title="Select an email" icon={<Inbox size={20} />} />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4 p-8">
            {summary && (
              <div className="panel flex items-start gap-3 p-4">
                <Sparkles size={14} className="mt-0.5 shrink-0 text-accent-soft" />
                <p className="text-[13px] leading-relaxed text-zinc-400">{summary.summary}</p>
              </div>
            )}

            <div>
              <h1 className="mb-2 text-[20px] font-semibold leading-snug tracking-tight text-ink">
                {selected.subject}
              </h1>
              <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-zinc-500">
                <span className="text-zinc-300">{selected.sender_name || selected.sender}</span>
                <span className="text-zinc-700">·</span>
                <span>{selected.sender}</span>
                <span className="text-zinc-700">·</span>
                <span>{fmtRelative(selected.received_at)}</span>
                <Badge tone={selected.importance}>{selected.importance}</Badge>
                {selected.category && <Badge>{selected.category}</Badge>}
              </div>
            </div>

            {(selected.ai_summary || selected.action_items.length > 0) && (
              <Card className="border-accent/20 bg-accent-dim">
                <SectionTitle>AURA read this</SectionTitle>
                {selected.ai_summary && (
                  <p className="mb-3 text-[13px] leading-relaxed text-zinc-300">
                    {selected.ai_summary}
                  </p>
                )}
                {selected.action_items.length > 0 && (
                  <ul className="space-y-1.5">
                    {selected.action_items.map((a, i) => (
                      <li key={i} className="flex items-start gap-2 text-[13px] text-zinc-300">
                        <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent-soft" />
                        {a}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            )}

            <div className="flex gap-2">
              <button
                onClick={() => {
                  setDraft(null);
                  setDraftOpen(true);
                }}
                className="btn-primary"
              >
                <Wand2 size={14} /> Draft reply
              </button>
              <button onClick={() => archive(selected)} className="btn-ghost">
                <Archive size={14} /> Archive
              </button>
            </div>

            <Card>
              <pre className="whitespace-pre-wrap break-words font-sans text-[13.5px] leading-relaxed text-zinc-300">
                {selected.body || selected.snippet}
              </pre>
            </Card>
          </div>
        )}
      </div>

      <Modal open={draftOpen} onClose={() => setDraftOpen(false)} title="Draft a reply">
        <div className="space-y-3">
          <div>
            <label className="label mb-1.5 block">What should it say?</label>
            <input
              className="input"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
          </div>
          <button onClick={generateDraft} disabled={draftBusy} className="btn-primary w-full">
            {draftBusy ? <Spinner /> : <Wand2 size={14} />} Generate
          </button>
          {draft && (
            <div className="space-y-2">
              <div className="text-[12px] text-zinc-500">
                To {selected?.sender} · {draft.subject}
              </div>
              <textarea
                className="input min-h-[220px] font-sans leading-relaxed"
                value={draft.body}
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              />
              <p className="text-[11.5px] text-zinc-600">
                Copy this into your mail client, or ask AURA in Chat to send it — it will
                queue for your approval first.
              </p>
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
