"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FileText, Plus, Search, Sparkles, Trash2, Upload } from "lucide-react";
import { DocumentDetail, DocumentItem, DocumentPassage, api } from "@/lib/api";
import { useAction } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  fmtRelative,
} from "@/components/ui";

function humanSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const run = useAction();
  const fileRef = useRef<HTMLInputElement>(null);

  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selected, setSelected] = useState<DocumentDetail | null>(null);
  const [passages, setPassages] = useState<DocumentPassage[] | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteBody, setPasteBody] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDocuments(await api.documents());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    for (const file of Array.from(files)) {
      await run(() => api.uploadDocument(file), `${file.name} added`);
    }
    setUploading(false);
    await load();
  };

  const open = async (doc: DocumentItem) => {
    setSelected(null);
    const detail = await api.document(doc.id);
    setSelected(detail);
  };

  const search = async () => {
    if (!query.trim()) {
      setPassages(null);
      return;
    }
    setBusy("search");
    await run(async () => setPassages(await api.searchDocuments(query.trim())));
    setBusy(null);
  };

  const summarize = async (doc: DocumentItem) => {
    setBusy(doc.id);
    await run(async () => {
      await api.summarizeDocument(doc.id);
      await load();
      if (selected?.id === doc.id) setSelected(await api.document(doc.id));
    }, "Summarised");
    setBusy(null);
  };

  const remove = async (doc: DocumentItem) => {
    await run(async () => {
      await api.deleteDocument(doc.id);
      if (selected?.id === doc.id) setSelected(null);
      await load();
    }, "Deleted");
  };

  const paste = async () => {
    if (!pasteTitle.trim() || !pasteBody.trim()) return;
    await run(
      () => api.addDocumentText(pasteTitle.trim(), pasteBody.trim()),
      "Added"
    );
    setPasteOpen(false);
    setPasteTitle("");
    setPasteBody("");
    await load();
  };

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6 sm:p-8">
      <PageHeader
        title="Documents"
        glow="azure"
        blurb="Upload what you need answers from. Files are split into passages and indexed by meaning, so you can ask what a clause says instead of hunting for the file."
        action={
          <div className="flex gap-2">
            <button onClick={() => setPasteOpen(true)} className="btn-ghost">
              <Plus size={14} /> Paste text
            </button>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
              className="btn-primary"
            >
              {uploading ? <Spinner /> : <Upload size={14} />} Upload
            </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept=".pdf,.docx,.txt,.md,.csv,.tsv,.json,.html,.log"
              onChange={(e) => upload(e.target.files)}
            />
          </div>
        }
      />

      {/* Search */}
      <Card>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint" />
            <input
              className="input py-2 pl-8"
              placeholder="Ask across your documents — e.g. what's the notice period"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && search()}
            />
          </div>
          <button onClick={search} disabled={busy === "search"} className="btn-ghost">
            {busy === "search" ? <Spinner /> : "Search"}
          </button>
        </div>

        {passages && (
          <div className="mt-4 space-y-2">
            {passages.length === 0 ? (
              <p className="text-[13px] text-muted">Nothing matched that.</p>
            ) : (
              passages.map((p, i) => (
                <div key={i} className="panel-raised p-3.5">
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className="text-[12.5px] font-medium">{p.title}</span>
                    <Badge>passage {p.ordinal + 1}</Badge>
                    <span className="text-[11px] text-faint">score {p.score}</span>
                  </div>
                  <p className="text-[12.5px] leading-relaxed text-muted">{p.excerpt}</p>
                </div>
              ))
            )}
          </div>
        )}
      </Card>

      {/* Library */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void upload(e.dataTransfer.files);
        }}
        className={`rounded-2xl transition-all duration-200 ${
          dragging
            ? "shadow-glow ring-2 ring-accent ring-offset-4 ring-offset-canvas"
            : ""
        }`}
      >
        {loading ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-28" />
            ))}
          </div>
        ) : documents.length === 0 ? (
          <EmptyState
            title="Nothing uploaded yet"
            hint="Drop files here, or use Upload. PDF, Word, Markdown, CSV and plain text all work."
            icon={<FileText size={20} />}
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {documents.map((d) => (
              <div key={d.id} className="panel group p-4">
                <div className="mb-2 flex items-start justify-between gap-2">
                  <button
                    onClick={() => open(d)}
                    className="min-w-0 flex-1 text-left text-[14px] font-medium leading-snug transition-colors hover:text-accent-soft"
                  >
                    {d.title}
                  </button>
                  <button
                    onClick={() => remove(d)}
                    className="shrink-0 opacity-0 transition group-hover:opacity-100"
                  >
                    <Trash2 size={13} className="text-faint hover:text-rose" />
                  </button>
                </div>
                <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px] text-faint">
                  <Badge>{d.source}</Badge>
                  <span>{humanSize(d.size_bytes)}</span>
                  <span>{fmtRelative(d.created_at)}</span>
                </div>
                {d.summary ? (
                  <p className="line-clamp-3 text-[12.5px] leading-relaxed text-muted">
                    {d.summary}
                  </p>
                ) : (
                  <button
                    onClick={() => summarize(d)}
                    disabled={busy === d.id}
                    className="btn-quiet px-0 py-0 text-[12px]"
                  >
                    {busy === d.id ? <Spinner /> : <Sparkles size={12} />} Summarise
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Detail */}
      <Modal
        open={!!selected}
        onClose={() => setSelected(null)}
        title={selected?.title ?? ""}
        width="max-w-2xl"
      >
        {selected && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-faint">
              <Badge>{selected.mime_type}</Badge>
              <span>{humanSize(selected.size_bytes)}</span>
              <span>{selected.chunk_count} passages indexed</span>
              <span>{fmtRelative(selected.created_at)}</span>
            </div>
            {selected.summary && (
              <div className="rounded-xl border border-accent/25 bg-accent-dim p-4">
                <div className="label mb-1.5">Summary</div>
                <p className="text-[13px] leading-relaxed">{selected.summary}</p>
              </div>
            )}
            <div>
              <div className="label mb-1.5">Contents</div>
              <pre className="max-h-[45vh] overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-line bg-raised/50 p-3.5 font-sans text-[12.5px] leading-relaxed text-muted">
                {selected.content}
              </pre>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={pasteOpen} onClose={() => setPasteOpen(false)} title="Paste text">
        <div className="space-y-3">
          <input
            className="input"
            placeholder="Title"
            value={pasteTitle}
            onChange={(e) => setPasteTitle(e.target.value)}
          />
          <textarea
            className="input min-h-[220px]"
            placeholder="Paste notes, a transcript, a contract clause…"
            value={pasteBody}
            onChange={(e) => setPasteBody(e.target.value)}
          />
          <button
            onClick={paste}
            disabled={!pasteTitle.trim() || !pasteBody.trim()}
            className="btn-primary w-full"
          >
            Add to documents
          </button>
        </div>
      </Modal>

      <Card>
        <SectionTitle>Where this shows up</SectionTitle>
        <p className="text-[12.5px] leading-relaxed text-muted">
          Install the <strong>Documents</strong> plugin and your assistant can search these
          in conversation — &ldquo;what did we agree on notice period?&rdquo; pulls the
          passage rather than making something up.
        </p>
      </Card>
    </div>
  );
}
