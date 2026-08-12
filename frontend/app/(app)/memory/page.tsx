"use client";

import { useCallback, useEffect, useState } from "react";
import { Pin, Plus, Search, Sparkles, Trash2 } from "lucide-react";
import { MemoryItem, api } from "@/lib/api";
import { Badge, Card, EmptyState, PageHeader, Skeleton, fmtRelative } from "@/components/ui";

const KINDS = ["preference", "style", "project", "contact", "decision", "habit", "fact"];

export default function MemoryPage() {
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("preference");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setMemories(await api.memories(filter));
    } finally {
      setLoading(false);
      setSearching(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  const search = async () => {
    if (!query.trim()) return load();
    setLoading(true);
    setSearching(true);
    try {
      setMemories(await api.searchMemories(query));
    } finally {
      setLoading(false);
    }
  };

  const add = async () => {
    if (!content.trim()) return;
    await api.createMemory(content.trim(), kind);
    setContent("");
    await load();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-8">
      <PageHeader
        title="Memory"
        glow="violet"
        blurb="What AURA knows about you. Retrieved by meaning, not keywords — only relevant entries ever reach the model."
      />

      <Card>
        <div className="space-y-3">
          <div className="flex gap-2">
            <input
              className="input flex-1"
              placeholder="Teach AURA something — e.g. 'I never take meetings on Fridays'"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && add()}
            />
            <select
              className="input w-[140px]"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {KINDS.map((k) => (
                <option key={k} value={k} className="bg-panel">
                  {k}
                </option>
              ))}
            </select>
            <button onClick={add} disabled={!content.trim()} className="btn-primary">
              <Plus size={14} /> Save
            </button>
          </div>
        </div>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint" />
          <input
            className="input py-2 pl-8 text-[13px]"
            placeholder="Semantic search…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search()}
          />
        </div>
        <button
          onClick={() => {
            setFilter("");
            setQuery("");
            void load();
          }}
          className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
            !filter && !searching
              ? "border-accent/50 bg-accent-dim text-accent-soft"
              : "border-line text-muted hover:border-accent/30 hover:text-ink"
          }`}
        >
          All
        </button>
        {KINDS.map((k) => (
          <button
            key={k}
            onClick={() => {
              setQuery("");
              setFilter(k);
            }}
            className={`rounded-full border px-3 py-1.5 text-[12px] transition ${
              filter === k
                ? "border-accent/50 bg-accent-dim text-accent-soft"
                : "border-line text-muted hover:border-accent/30 hover:text-ink"
            }`}
          >
            {k}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : memories.length === 0 ? (
        <EmptyState
          title="Nothing stored yet"
          hint="AURA saves preferences and decisions as you chat, or add them above."
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
                <p className="text-[13.5px] leading-relaxed text-ink/90">{m.content}</p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Badge tone={m.pinned ? "accent" : "normal"}>{m.kind}</Badge>
                  <span className="text-[11px] text-faint">
                    used {m.use_count}× · {fmtRelative(m.created_at)} · {m.source}
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                <button
                  onClick={async () => {
                    await api.pinMemory(m.id);
                    await load();
                  }}
                  title={m.pinned ? "Unpin" : "Pin into every prompt"}
                  className="rounded-md p-1.5 transition hover:bg-raised"
                >
                  <Pin
                    size={13}
                    className={m.pinned ? "text-accent-soft" : "text-faint"}
                  />
                </button>
                <button
                  onClick={async () => {
                    await api.deleteMemory(m.id);
                    await load();
                  }}
                  className="rounded-md p-1.5 opacity-0 transition hover:bg-raised group-hover:opacity-100"
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
