"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Archive,
  ArrowLeft,
  Copy,
  Inbox,
  RefreshCw,
  Search,
  Sparkles,
  Wand2,
} from "lucide-react";
import { Email, InboxSummary, api } from "@/lib/api";
import { useToast } from "@/components/Toast";
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
  const toast = useToast();
  const [folder, setFolder] = useState("inbox");
  const [query, setQuery] = useState("");
  // What the list is actually filtered by. It trails `query` by 300 ms, so a
  // search is one request once you stop typing — not two per keystroke, each
  // blanking the list, and answering out of order.
  const [search, setSearch] = useState("");
  const [emails, setEmails] = useState<Email[]>([]);
  const [summary, setSummary] = useState<InboxSummary | null>(null);
  const [selected, setSelected] = useState<Email | null>(null);
  // Phones show the list or the message, not both side by side.
  const [showDetail, setShowDetail] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const latest = useRef(0);

  const [draftOpen, setDraftOpen] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [instruction, setInstruction] = useState("Write a concise, professional reply.");
  const [draft, setDraft] = useState<{ subject: string; body: string } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await api.inboxSummary());
    } catch {
      /* the summary card is optional */
    }
  }, []);

  const load = useCallback(async () => {
    const ticket = ++latest.current;
    try {
      const list = await api.emails(folder, search);
      if (ticket !== latest.current) return; // a newer search already answered
      setEmails(list);
      setSelected((prev) =>
        prev && list.some((e) => e.id === prev.id) ? prev : list[0] ?? null
      );
    } catch (e) {
      if (ticket === latest.current) {
        toast.error(e instanceof Error ? e.message : "Couldn't load your email");
      }
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, [folder, search, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const open = async (email: Email) => {
    setSelected(email);
    setShowDetail(true);
    if (!email.is_read) {
      try {
        const full = await api.email(email.id);
        setSelected(full);
        setEmails((prev) => prev.map((e) => (e.id === full.id ? full : e)));
      } catch {
        /* the list copy is enough to read */
      }
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      await api.syncEmail();
      await Promise.all([load(), loadSummary()]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't sync email");
    } finally {
      setSyncing(false);
    }
  };

  const archive = async (email: Email) => {
    setArchiving(true);
    try {
      await api.archiveEmail(email.id);
      setSelected(null);
      setShowDetail(false);
      toast.success("Archived");
      await Promise.all([load(), loadSummary()]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't archive that email");
    } finally {
      setArchiving(false);
    }
  };

  const generateDraft = async () => {
    if (!selected) return;
    setDraftBusy(true);
    try {
      setDraft(await api.draftReply(selected.id, instruction));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't draft a reply");
    } finally {
      setDraftBusy(false);
    }
  };

  const copyDraft = async () => {
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft.body);
      toast.success("Draft copied");
    } catch {
      toast.error("Couldn't copy — select the text and copy it instead");
    }
  };

  return (
    <div className="flex h-full min-h-0">
      {/* List */}
      <div
        className={`${
          showDetail ? "hidden md:flex" : "flex"
        } w-full shrink-0 flex-col border-r border-line bg-panel/30 backdrop-blur-xl md:w-[340px] xl:w-[380px]`}
      >
        <div className="space-y-3 border-b border-line p-4">
          <div className="flex items-center justify-between">
            <h1 className="text-[16px] font-semibold tracking-tight text-ink">Email</h1>
            <button
              onClick={sync}
              disabled={syncing}
              className="btn-quiet px-2 py-1"
              title="Sync mail"
              aria-label="Sync mail"
            >
              {syncing ? <Spinner /> : <RefreshCw size={14} />}
            </button>
          </div>
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint" />
            <input
              className="input py-2 pl-8 text-[13px]"
              type="search"
              placeholder="Search email…"
              aria-label="Search email"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-1">
            {FOLDERS.map((f) => (
              <button
                key={f.key}
                onClick={() => {
                  setFolder(f.key);
                  setLoading(true);
                }}
                aria-pressed={folder === f.key}
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
              <EmptyState
                title={search ? "Nothing matches that search" : "Nothing here"}
                hint={
                  search
                    ? undefined
                    : folder === "inbox"
                      ? "Connect Google in Settings to bring your mail in."
                      : undefined
                }
                icon={<Inbox size={18} />}
              />
            </div>
          ) : (
            emails.map((e) => (
              <button
                key={e.id}
                onClick={() => open(e)}
                aria-current={selected?.id === e.id ? "true" : undefined}
                className={`relative w-full border-b border-line px-4 py-3 text-left transition ${
                  selected?.id === e.id
                    ? "bg-accent/10 before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-accent-soft"
                    : "hover:bg-raised/50"
                }`}
              >
                <div className="mb-1 flex items-center gap-2">
                  {!e.is_read && (
                    <span
                      className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent shadow-glow-sm"
                      aria-label="Unread"
                    />
                  )}
                  <span
                    className={`min-w-0 flex-1 truncate text-[12.5px] ${
                      e.is_read ? "text-muted" : "font-medium text-ink"
                    }`}
                  >
                    {e.sender_name || e.sender}
                  </span>
                  <span className="shrink-0 text-[10.5px] text-faint">
                    {fmtRelative(e.received_at)}
                  </span>
                </div>
                <div
                  className={`mb-1 truncate text-[13px] ${
                    e.is_read ? "text-muted" : "font-medium text-ink"
                  }`}
                >
                  {e.subject}
                </div>
                <div className="flex items-center gap-1.5">
                  {(e.importance === "urgent" || e.importance === "high") && (
                    <Badge tone={e.importance}>{e.importance}</Badge>
                  )}
                  {e.needs_reply && <Badge tone="accent">reply</Badge>}
                  {e.category && <span className="text-[10.5px] text-faint">{e.category}</span>}
                </div>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Detail */}
      <div className={`${showDetail ? "block" : "hidden md:block"} min-w-0 flex-1 overflow-y-auto`}>
        {!selected ? (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState title="Select an email" icon={<Inbox size={20} />} />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4 p-4 sm:p-8">
            <button
              onClick={() => setShowDetail(false)}
              className="btn-quiet -ml-2 px-2 py-1 text-[12.5px] md:hidden"
            >
              <ArrowLeft size={14} /> All email
            </button>

            {summary && summary.total > 0 && (
              <div className="panel flex items-start gap-3 p-4">
                <Sparkles size={14} className="mt-0.5 shrink-0 text-accent-soft" />
                <p className="text-[13px] leading-relaxed text-muted">{summary.summary}</p>
              </div>
            )}

            <div>
              <h2 className="mb-2 break-words text-[20px] font-semibold leading-snug tracking-tight text-ink">
                {selected.subject}
              </h2>
              <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-muted">
                <span className="text-ink/85">{selected.sender_name || selected.sender}</span>
                <span className="text-faint">·</span>
                <span className="break-all">{selected.sender}</span>
                <span className="text-faint">·</span>
                <span>{fmtRelative(selected.received_at)}</span>
                <Badge tone={selected.importance}>{selected.importance}</Badge>
                {selected.category && <Badge>{selected.category}</Badge>}
              </div>
            </div>

            {(selected.ai_summary || selected.action_items.length > 0) && (
              <Card className="border-accent/20 bg-accent-dim">
                <SectionTitle>AURA read this</SectionTitle>
                {selected.ai_summary && (
                  <p className="mb-3 text-[13px] leading-relaxed text-ink/85">
                    {selected.ai_summary}
                  </p>
                )}
                {selected.action_items.length > 0 && (
                  <ul className="space-y-1.5">
                    {selected.action_items.map((a, i) => (
                      <li key={i} className="flex items-start gap-2 text-[13px] text-ink/85">
                        <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-accent-soft" />
                        {a}
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
            )}

            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => {
                  setDraft(null);
                  setDraftOpen(true);
                }}
                className="btn-primary"
              >
                <Wand2 size={14} /> Draft reply
              </button>
              {!selected.is_archived && (
                <button
                  onClick={() => archive(selected)}
                  disabled={archiving}
                  className="btn-ghost"
                >
                  {archiving ? <Spinner /> : <Archive size={14} />} Archive
                </button>
              )}
            </div>

            <Card>
              <pre className="whitespace-pre-wrap break-words font-sans text-[13.5px] leading-relaxed text-ink/85">
                {selected.body || selected.snippet}
              </pre>
            </Card>
          </div>
        )}
      </div>

      <Modal open={draftOpen} onClose={() => setDraftOpen(false)} title="Draft a reply">
        <div className="space-y-3">
          <div>
            <label htmlFor="draft-instruction" className="label mb-1.5 block">
              What should it say?
            </label>
            <input
              id="draft-instruction"
              className="input"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !draftBusy && void generateDraft()}
            />
          </div>
          <button onClick={generateDraft} disabled={draftBusy} className="btn-primary w-full">
            {draftBusy ? <Spinner /> : <Wand2 size={14} />} {draft ? "Regenerate" : "Generate"}
          </button>
          {draft && (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-2 text-[12px] text-muted">
                <span className="min-w-0 truncate">
                  To {selected?.sender} · {draft.subject}
                </span>
                <button onClick={copyDraft} className="btn-quiet shrink-0 px-2 py-1 text-[12px]">
                  <Copy size={12} /> Copy
                </button>
              </div>
              <textarea
                className="input min-h-[220px] font-sans leading-relaxed"
                value={draft.body}
                aria-label="Draft reply"
                onChange={(e) => setDraft({ ...draft, body: e.target.value })}
              />
              <p className="text-[11.5px] text-faint">
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
