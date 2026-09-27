"use client";

import { useCallback, useEffect, useState } from "react";
import { Pin, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import { MemoryItem, api } from "@/lib/api";
import { useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  PageHeader,
  Skeleton,
  Spinner,
  fmtRelative,
} from "@/components/ui";

const KINDS = ["preference", "style", "project", "contact", "decision", "habit", "fact"];

export default function MemoryPage() {
  const toast = useToast();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  // The query the list is showing results for, if any.
  const [searched, setSearched] = useState("");
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setMemories(await api.memories(filter));
      setSearched("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load memories");
    } finally {
      setLoading(false);
    }
  }, [filter, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const search = async () => {
    const q = query.trim();
    if (!q) return load();
    setLoading(true);
    try {
      setMemories(await api.searchMemories(q));
      setSearched(q);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Search failed");
    } finally {
      setLoading(false);
    }
  };

  // After pinning or deleting, refresh whatever is on screen — the search
  // results if that's what you were looking at, not the whole list.
  const reload = () => (searched ? search() : load());

  const add = async () => {
    const text = content.trim();
    if (!text || saving) return;
    setSaving(true);
    try {
      await api.createMemory(text, kind);
      setContent("");
      toast.success("Saved to memory");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save that");
    } finally {
      setSaving(false);
    }
  };

  const pin = async (m: MemoryItem) => {
    setBusy(m.id);
    try {
      await api.pinMemory(m.id);
      await reload();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't pin that");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (m: MemoryItem) => {
    if (!window.confirm("Forget this? AURA won't be able to use it again.")) return;
    setBusy(m.id);
    try {
      await api.deleteMemory(m.id);
      setMemories((prev) => prev.filter((x) => x.id !== m.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't forget that");
    } finally {
      setBusy(null);
    }
  };

  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1.5 text-[12px] transition ${
      active
        ? "border-accent/50 bg-accent-dim text-accent-soft"
        : "border-line text-muted hover:border-accent/30 hover:text-ink"
    }`;

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-8">
      <PageHeader
        title="Memory"
        glow="violet"
        blurb="What AURA knows about you. Retrieved by meaning, not keywords — only relevant entries ever reach the model."
      />

      <Card>
        {/* Stacks on a phone: side by side, the text box got squeezed down to
            two visible letters. */}
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <input
            className="input min-w-0 flex-1"
            placeholder="Teach AURA something — e.g. 'I never take meetings on Fridays'"
            aria-label="Something AURA should remember"
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
          <div className="flex gap-2">
            <select
              className="input w-full sm:w-[140px]"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              aria-label="Kind of memory"
            >
              {KINDS.map((k) => (
                <option key={k} value={k} className="bg-panel">
                  {k}
                </option>
              ))}
            </select>
            <button type="submit" disabled={!content.trim() || saving} className="btn-primary shrink-0">
              {saving ? <Spinner /> : <Plus size={14} />} Save
            </button>
          </div>
        </form>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <form
          className="relative min-w-[200px] flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint" />
          <input
            className="input py-2 pl-8 text-[13px]"
            type="search"
            placeholder="Search by meaning…"
            aria-label="Search memories"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </form>
        <button
          onClick={() => {
            setQuery("");
            // Changing the filter reloads by itself; "All" while already on
            // All (e.g. to leave search results) has to ask.
            if (filter) setFilter("");
            else void load();
          }}
          className={chip(!filter && !searched)}
          aria-pressed={!filter && !searched}
        >
          All
        </button>
        {KINDS.map((k) => (
          <button
            key={k}
            onClick={() => {
              setQuery("");
              if (filter === k) void load();
              else setFilter(k);
            }}
            className={chip(filter === k && !searched)}
            aria-pressed={filter === k && !searched}
          >
            {k}
          </button>
        ))}
      </div>

      {searched && !loading && (
        <div className="flex items-center gap-2 text-[12.5px] text-muted">
          <span>
            {memories.length} result{memories.length === 1 ? "" : "s"} for “{searched}”
          </span>
          <button
            onClick={() => {
              setQuery("");
              void load();
            }}
            className="btn-quiet px-1.5 py-0.5 text-[12px]"
          >
            <X size={12} /> Clear
          </button>
        </div>
      )}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : memories.length === 0 ? (
        <EmptyState
          title={searched ? "Nothing close to that" : "Nothing stored yet"}
          hint={
            searched
              ? "Try different words — search works on meaning."
              : "AURA saves preferences and decisions as you chat, or add them above."
          }
          icon={<Sparkles size={18} />}
        />
      ) : (
        <div className="space-y-2">
          {memories.map((m) => (
            <div
              key={m.id}
              className={`panel group flex items-start gap-3 p-4 transition-all duration-200 hover:border-accent/25 ${
                m.pinned ? "border-accent/30" : ""
              }`}
            >
              {m.pinned && (
                <span className="absolute inset-y-4 left-0 w-0.5 rounded-full bg-accent-soft" />
              )}
              <div className="min-w-0 flex-1">
                <p className="break-words text-[13.5px] leading-relaxed text-ink/90">{m.content}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone={m.pinned ? "accent" : "normal"}>{m.kind}</Badge>
                  <span className="text-[11px] text-faint">
                    used {m.use_count}× · {fmtRelative(m.created_at)} · {m.source}
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <button
                  onClick={() => pin(m)}
                  disabled={busy === m.id}
                  title={m.pinned ? "Unpin" : "Pin into every prompt"}
                  aria-label={m.pinned ? "Unpin" : "Pin into every prompt"}
                  aria-pressed={m.pinned}
                  className="rounded-md p-1.5 transition hover:bg-raised"
                >
                  <Pin size={13} className={m.pinned ? "text-accent-soft" : "text-faint"} />
                </button>
                <button
                  onClick={() => remove(m)}
                  disabled={busy === m.id}
                  title="Forget"
                  aria-label="Forget this memory"
                  className="reveal-on-hover rounded-md p-1.5 hover:bg-raised"
                >
                  <Trash2 size={13} className="text-faint transition-colors hover:text-rose" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
