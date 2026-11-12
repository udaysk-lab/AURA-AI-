"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Pencil, Pin, Plus, Search, Sparkles, Trash2, X } from "lucide-react";
import {
  MemoryItem,
  MemoryResetScope,
  PreferenceItem,
  TaskTypeOption,
  WorkItemDetail,
  WorkItemOut,
  api,
} from "@/lib/api";
import { useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  Skeleton,
  Spinner,
  Toggle,
  fmtDay,
  fmtRelative,
} from "@/components/ui";

const KINDS = ["fact", "contact", "project", "decision"];

type Tab = "preferences" | "work" | "facts";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "preferences", label: "Preferences" },
  { id: "work", label: "Past work" },
  { id: "facts", label: "Facts" },
];

const RESET_SCOPES: Array<{ value: MemoryResetScope; label: string; hint: string }> = [
  { value: "preferences", label: "Preferences", hint: "Every preference, stated or learned." },
  { value: "task_memory", label: "Past work", hint: "The record of finished tasks. Tasks still running or waiting stay." },
  { value: "facts", label: "Facts", hint: "Every saved fact, contact, project and decision." },
  { value: "all", label: "Everything", hint: "All three of the above." },
];

const STATUS_LABEL: Record<string, string> = {
  running: "Working",
  waiting: "Waiting on you",
  ready: "Ready to carry on",
  done: "Done",
  partly_done: "Partly done",
  failed: "Didn't finish",
  stopped: "Stopped",
};

const label = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

const chip = (active: boolean) =>
  `min-h-[44px] rounded-full border px-3.5 py-1.5 text-[12.5px] transition ${
    active
      ? "border-accent/50 bg-accent-dim text-accent-soft"
      : "border-line text-muted hover:border-accent/30 hover:text-ink"
  }`;

const iconBtn =
  "flex h-11 w-11 items-center justify-center rounded-md transition hover:bg-raised disabled:opacity-50";

export default function MemoryPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("preferences");
  const [paused, setPaused] = useState(false);
  const [pausedLoaded, setPausedLoaded] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [version, setVersion] = useState(0); // bumped after a reset so tabs reload

  useEffect(() => {
    api
      .memorySettings()
      .then((s) => setPaused(s.paused))
      .catch(() => {
        /* the switch stays off */
      })
      .finally(() => setPausedLoaded(true));
  }, []);

  const togglePause = async (next: boolean) => {
    setPaused(next);
    try {
      const s = await api.setMemoryPaused(next);
      setPaused(s.paused);
      toast.success(s.paused ? "Memory paused" : "Memory is on");
    } catch (e) {
      setPaused(!next);
      toast.error(e instanceof Error ? e.message : "Couldn't change that");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-8">
      <PageHeader
        title="Memory"
        glow="violet"
        blurb="How you like things done, the work AURA has done for you, and facts it keeps. You can change or remove any of it."
      />

      <Card>
        <div className="flex flex-wrap items-center gap-4">
          <div className="min-w-0 flex-1">
            <div id="pause-label" className="text-[14px] font-medium">
              Pause memory
            </div>
            <p className="text-[12.5px] leading-relaxed text-muted">
              While paused AURA learns nothing new from your chats. It still uses what it already knows.
              To skip memory in one chat, use the switch under that chat&apos;s message box.
            </p>
          </div>
          <Toggle
            checked={paused}
            onChange={(n) => void togglePause(n)}
            disabled={!pausedLoaded}
            label="Pause memory"
          />
          <button type="button" className="btn-ghost min-h-[44px]" onClick={() => setResetOpen(true)}>
            <Trash2 size={14} aria-hidden /> Reset…
          </button>
        </div>
      </Card>

      <div role="tablist" aria-label="Memory" className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            onClick={() => setTab(t.id)}
            className={chip(tab === t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "preferences" && <PreferencesTab key={`p${version}`} />}
        {tab === "work" && <PastWorkTab key={`w${version}`} />}
        {tab === "facts" && <FactsTab key={`f${version}`} />}
      </div>

      <ResetModal
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        onDone={() => {
          setResetOpen(false);
          setVersion((v) => v + 1);
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------

function ResetModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [scope, setScope] = useState<MemoryResetScope>("preferences");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open) {
      setTyped("");
      setScope("preferences");
    }
  }, [open]);

  const run = async () => {
    if (typed !== "RESET" || busy) return;
    setBusy(true);
    try {
      const r = await api.resetMemory(scope);
      const d = r.deleted;
      toast.success(
        `Removed ${d.preferences} preference${d.preferences === 1 ? "" : "s"}, ${d.work_items} past task${d.work_items === 1 ? "" : "s"}, ${d.memories} fact${d.memories === 1 ? "" : "s"}`
      );
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't reset memory");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Reset memory">
      <div className="space-y-4">
        <fieldset className="space-y-1">
          <legend className="mb-1 text-[12.5px] text-muted">What to delete</legend>
          {RESET_SCOPES.map((s) => (
            <label key={s.value} className="flex min-h-[44px] cursor-pointer items-start gap-3 rounded-xl px-2 py-2 hover:bg-raised/60">
              <input
                type="radio"
                name="reset-scope"
                value={s.value}
                checked={scope === s.value}
                onChange={() => setScope(s.value)}
                className="mt-1 h-4 w-4"
              />
              <span>
                <span className="block text-[13.5px] font-medium">{s.label}</span>
                <span className="block text-[12px] text-muted">{s.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <p className="text-[12.5px] leading-relaxed text-muted">This can&apos;t be undone.</p>
        <div>
          <label htmlFor="reset-confirm" className="mb-1 block text-[12.5px] text-muted">
            Type RESET to confirm
          </label>
          <input
            id="reset-confirm"
            className="input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoComplete="off"
          />
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn-quiet min-h-[44px] flex-1" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary min-h-[44px] flex-1"
            disabled={typed !== "RESET" || busy}
            onClick={() => void run()}
          >
            {busy ? <Spinner /> : <Trash2 size={14} aria-hidden />} Delete
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Preferences

function PreferencesTab() {
  const toast = useToast();
  const [items, setItems] = useState<PreferenceItem[]>([]);
  const [types, setTypes] = useState<TaskTypeOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [text, setText] = useState("");
  const [taskType, setTaskType] = useState("general");
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editType, setEditType] = useState("general");
  const [busy, setBusy] = useState<string | null>(null);

  const typeOptions = useMemo(() => {
    const list = types.some((t) => t.value === "general")
      ? types
      : [{ value: "general", label: "Anything" }, ...types];
    return list;
  }, [types]);
  const typeLabel = (v: string) => typeOptions.find((t) => t.value === v)?.label ?? label(v);

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([
        api.preferences({ include_inactive: true }),
        api.taskTypes().catch(() => [] as TaskTypeOption[]),
      ]);
      // A preference that was replaced by a newer one is history, not a setting.
      setItems(p.filter((x) => !x.superseded_by));
      setTypes(t);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load preferences");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = useMemo(() => {
    const g = new Map<string, PreferenceItem[]>();
    for (const p of items) g.set(p.task_type, [...(g.get(p.task_type) ?? []), p]);
    // "general" first, then the rest in the server's type order.
    const order = ["general", ...typeOptions.map((t) => t.value)];
    return Array.from(g.entries()).sort(
      (a, b) => (order.indexOf(a[0]) + 1 || 999) - (order.indexOf(b[0]) + 1 || 999)
    );
  }, [items, typeOptions]);

  const add = async () => {
    const t = text.trim();
    if (!t || saving) return;
    setSaving(true);
    try {
      await api.addPreference({ text: t, task_type: taskType });
      setText("");
      toast.success("Saved");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save that");
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (p: PreferenceItem, active: boolean) => {
    setBusy(p.id);
    setItems((l) => l.map((x) => (x.id === p.id ? { ...x, active } : x)));
    try {
      await api.updatePreference(p.id, { active });
    } catch (e) {
      setItems((l) => l.map((x) => (x.id === p.id ? { ...x, active: p.active } : x)));
      toast.error(e instanceof Error ? e.message : "Couldn't change that");
    } finally {
      setBusy(null);
    }
  };

  const saveEdit = async (p: PreferenceItem) => {
    const t = editText.trim();
    if (!t) return;
    setBusy(p.id);
    try {
      await api.updatePreference(p.id, { text: t, task_type: editType });
      setEditing(null);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save that");
    } finally {
      setBusy(null);
    }
  };

  const remove = async (p: PreferenceItem) => {
    if (!window.confirm("Delete this preference? AURA won't follow it any more.")) return;
    setBusy(p.id);
    try {
      await api.deletePreference(p.id);
      setItems((l) => l.filter((x) => x.id !== p.id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete that");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-5">
      <Card>
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <div className="min-w-0 flex-1">
            <label htmlFor="pref-text" className="mb-1 block text-[12.5px] text-muted">
              A preference
            </label>
            <input
              id="pref-text"
              className="input min-h-[44px]"
              placeholder="e.g. Keep replies under 120 words"
              maxLength={300}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="pref-type" className="mb-1 block text-[12.5px] text-muted">
              For
            </label>
            <select
              id="pref-type"
              className="input min-h-[44px] w-full sm:w-[180px]"
              value={taskType}
              onChange={(e) => setTaskType(e.target.value)}
            >
              {typeOptions.map((t) => (
                <option key={t.value} value={t.value} className="bg-panel">
                  {t.value === "general" ? "Everything" : t.label}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={!text.trim() || saving} className="btn-primary min-h-[44px] shrink-0">
            {saving ? <Spinner /> : <Plus size={14} aria-hidden />} Add
          </button>
        </form>
      </Card>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : grouped.length === 0 ? (
        <EmptyState
          title="No preferences yet"
          hint="Say “always…” or “never…” in a chat, correct AURA, or add one above."
          icon={<Sparkles size={18} />}
        />
      ) : (
        grouped.map(([type, list]) => (
          <section key={type} aria-labelledby={`pg-${type}`} className="space-y-2">
            <h2 id={`pg-${type}`} className="label">
              {type === "general" ? "Everything" : typeLabel(type)}
            </h2>
            {list.map((p) => (
              <div key={p.id} className={`panel p-4 ${p.active ? "" : "opacity-55"}`}>
                {editing === p.id ? (
                  <form
                    className="space-y-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveEdit(p);
                    }}
                  >
                    <label htmlFor={`pe-${p.id}`} className="block text-[12.5px] text-muted">
                      Preference
                    </label>
                    <textarea
                      id={`pe-${p.id}`}
                      className="input min-h-[72px]"
                      maxLength={300}
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                    />
                    <label htmlFor={`pt-${p.id}`} className="block text-[12.5px] text-muted">
                      For
                    </label>
                    <select
                      id={`pt-${p.id}`}
                      className="input min-h-[44px]"
                      value={editType}
                      onChange={(e) => setEditType(e.target.value)}
                    >
                      {typeOptions.map((t) => (
                        <option key={t.value} value={t.value} className="bg-panel">
                          {t.value === "general" ? "Everything" : t.label}
                        </option>
                      ))}
                    </select>
                    <div className="flex gap-2">
                      <button type="button" className="btn-quiet min-h-[44px]" onClick={() => setEditing(null)}>
                        Cancel
                      </button>
                      <button type="submit" className="btn-primary min-h-[44px]" disabled={!editText.trim() || busy === p.id}>
                        Save
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="flex items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-[13.5px] leading-relaxed">{p.text}</p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <Badge tone={p.source === "correction" ? "warning" : "accent"}>
                          {p.source === "correction" ? "From a correction" : "You said so"}
                        </Badge>
                        {!p.active && <Badge>Off</Badge>}
                        <span className="text-[11.5px] text-muted">
                          used {p.use_count}× · {fmtRelative(p.created_at)}
                        </span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Toggle
                        checked={p.active}
                        onChange={(n) => void toggle(p, n)}
                        disabled={busy === p.id}
                        label={p.active ? "Turn off this preference" : "Turn on this preference"}
                      />
                      <button
                        type="button"
                        className={iconBtn}
                        aria-label="Edit this preference"
                        onClick={() => {
                          setEditing(p.id);
                          setEditText(p.text);
                          setEditType(p.task_type);
                        }}
                      >
                        <Pencil size={14} className="text-muted" aria-hidden />
                      </button>
                      <button
                        type="button"
                        className={iconBtn}
                        aria-label="Delete this preference"
                        disabled={busy === p.id}
                        onClick={() => void remove(p)}
                      >
                        <Trash2 size={14} className="text-muted" aria-hidden />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </section>
        ))
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Past work

function PastWorkTab() {
  const toast = useToast();
  const [items, setItems] = useState<WorkItemOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<WorkItemDetail | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(
    async (q = "") => {
      setLoading(true);
      try {
        setItems(await api.workItems({ limit: 50, q: q.trim() }));
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Couldn't load past work");
      } finally {
        setLoading(false);
      }
    },
    [toast]
  );

  useEffect(() => {
    void load();
  }, [load]);

  const show = async (id: string) => {
    setOpening(id);
    try {
      setOpen(await api.workItem(id));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't open that");
    } finally {
      setOpening(null);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this record? AURA won't be able to learn from it again.")) return;
    setBusy(id);
    try {
      await api.deleteWorkItem(id);
      setItems((l) => l.filter((x) => x.id !== id));
      setOpen((o) => (o?.id === id ? null : o));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't delete that");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <form
        className="relative"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          void load(query);
        }}
      >
        <label htmlFor="work-search" className="sr-only">
          Search past work
        </label>
        <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-muted" aria-hidden />
        <input
          id="work-search"
          type="search"
          className="input min-h-[44px] pl-8"
          placeholder="Search past work…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </form>

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title="No past work"
          hint="Each task AURA does for you is recorded here, so it can do similar ones better."
          icon={<Sparkles size={18} />}
        />
      ) : (
        <ul className="space-y-2">
          {items.map((w) => (
            <li key={w.id} className="panel flex items-start gap-2 p-2 pl-4">
              <button
                type="button"
                onClick={() => void show(w.id)}
                className="min-h-[44px] min-w-0 flex-1 py-2 text-left"
                aria-label={`Open ${w.title}`}
              >
                <span className="block break-words text-[13.5px] font-medium">{w.title}</span>
                {w.outcome && (
                  <span className="mt-0.5 block break-words text-[12.5px] leading-relaxed text-muted">{w.outcome}</span>
                )}
                <span className="mt-1.5 flex flex-wrap items-center gap-2">
                  <Badge tone={w.status === "failed" ? "urgent" : w.status === "done" ? "success" : "normal"}>
                    {STATUS_LABEL[w.status] ?? w.status}
                  </Badge>
                  <span className="text-[11.5px] text-muted">
                    {label(w.task_type)} · {fmtDay(w.created_at)}
                  </span>
                  {opening === w.id && <Spinner />}
                </span>
              </button>
              <button
                type="button"
                className={iconBtn}
                aria-label={`Delete ${w.title}`}
                disabled={busy === w.id}
                onClick={() => void remove(w.id)}
              >
                <Trash2 size={14} className="text-muted" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}

      <Modal open={!!open} onClose={() => setOpen(null)} title={open?.title ?? ""} width="max-w-2xl">
        {open && (
          <div className="space-y-4 text-[13px]">
            <div className="flex flex-wrap items-center gap-2">
              <Badge>{STATUS_LABEL[open.status] ?? open.status}</Badge>
              <span className="text-muted">
                {label(open.task_type)} · {fmtDay(open.created_at)}
              </span>
            </div>
            <section>
              <h3 className="label mb-1">What you asked</h3>
              <p className="whitespace-pre-wrap break-words leading-relaxed">{open.instruction}</p>
            </section>
            <section>
              <h3 className="label mb-1">Plan</h3>
              {open.plan?.length ? (
                <ol className="list-decimal space-y-1 pl-5">
                  {open.plan.map((p) => (
                    <li key={p.index}>
                      {p.text} <span className="text-muted">({p.status})</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-muted">No plan was recorded.</p>
              )}
            </section>
            <section>
              <h3 className="label mb-1">Steps</h3>
              {open.steps?.length ? (
                <ul className="space-y-1">
                  {open.steps.map((s) => (
                    <li key={s.seq} className="break-words">
                      <span className="text-muted">{label(s.kind)}:</span>{" "}
                      {s.summary || s.tool_name || s.status}{" "}
                      <span className="text-muted">({s.status})</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted">No steps were recorded.</p>
              )}
              {open.systems_used?.length > 0 && (
                <p className="mt-2 text-muted">Systems: {open.systems_used.join(", ")}</p>
              )}
            </section>
            <section>
              <h3 className="label mb-1">Result</h3>
              <p className="whitespace-pre-wrap break-words leading-relaxed">
                {open.report || open.outcome || "No result was recorded."}
              </p>
            </section>
            <button
              type="button"
              className="btn-ghost min-h-[44px]"
              disabled={busy === open.id}
              onClick={() => void remove(open.id)}
            >
              <Trash2 size={14} aria-hidden /> Delete this record
            </button>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Facts (the existing memories, with content editing)

function FactsTab() {
  const toast = useToast();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  const [searched, setSearched] = useState("");
  const [content, setContent] = useState("");
  const [kind, setKind] = useState("fact");
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

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

  const saveEdit = async (m: MemoryItem) => {
    const t = editText.trim();
    if (!t) return;
    setBusy(m.id);
    try {
      const updated = await api.updateMemory(m.id, { content: t });
      setMemories((l) => l.map((x) => (x.id === m.id ? { ...x, ...updated } : x)));
      setEditing(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't save that");
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

  return (
    <div className="space-y-5">
      <Card>
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <div className="min-w-0 flex-1">
            <label htmlFor="fact-text" className="mb-1 block text-[12.5px] text-muted">
              Something AURA should remember
            </label>
            <input
              id="fact-text"
              className="input min-h-[44px]"
              placeholder="e.g. My accountant is Priya Raman"
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="fact-kind" className="mb-1 block text-[12.5px] text-muted">
              Kind
            </label>
            <select
              id="fact-kind"
              className="input min-h-[44px] w-full sm:w-[140px]"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              {KINDS.map((k) => (
                <option key={k} value={k} className="bg-panel">
                  {k}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={!content.trim() || saving} className="btn-primary min-h-[44px] shrink-0">
            {saving ? <Spinner /> : <Plus size={14} aria-hidden />} Save
          </button>
        </form>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <form
          className="relative min-w-[200px] flex-1"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <Search size={13} className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-muted" aria-hidden />
          <input
            className="input min-h-[44px] pl-8 text-[13px]"
            type="search"
            placeholder="Search by meaning…"
            aria-label="Search facts"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </form>
        <button
          onClick={() => {
            setQuery("");
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
            className="btn-quiet min-h-[44px] px-2 py-0.5 text-[12px]"
          >
            <X size={12} aria-hidden /> Clear
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
              : "AURA saves facts and decisions as you chat, or add them above."
          }
          icon={<Sparkles size={18} />}
        />
      ) : (
        <div className="space-y-2">
          {memories.map((m) => (
            <div
              key={m.id}
              className={`panel flex items-start gap-3 p-4 transition-all duration-200 hover:border-accent/25 ${
                m.pinned ? "border-accent/30" : ""
              }`}
            >
              <div className="min-w-0 flex-1">
                {editing === m.id ? (
                  <form
                    className="space-y-2"
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveEdit(m);
                    }}
                  >
                    <label htmlFor={`fe-${m.id}`} className="block text-[12.5px] text-muted">
                      Memory
                    </label>
                    <textarea
                      id={`fe-${m.id}`}
                      className="input min-h-[72px]"
                      maxLength={2000}
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                    />
                    <div className="flex gap-2">
                      <button type="button" className="btn-quiet min-h-[44px]" onClick={() => setEditing(null)}>
                        Cancel
                      </button>
                      <button type="submit" className="btn-primary min-h-[44px]" disabled={!editText.trim() || busy === m.id}>
                        Save
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                    <p className="break-words text-[13.5px] leading-relaxed text-ink/90">{m.content}</p>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <Badge tone={m.pinned ? "accent" : "normal"}>{m.kind}</Badge>
                      <span className="text-[11.5px] text-muted">
                        used {m.use_count}× · {fmtRelative(m.created_at)} · {m.source}
                      </span>
                    </div>
                  </>
                )}
              </div>
              {editing !== m.id && (
                <div className="flex shrink-0">
                  <button
                    onClick={() => {
                      setEditing(m.id);
                      setEditText(m.content);
                    }}
                    className={iconBtn}
                    aria-label="Edit this memory"
                  >
                    <Pencil size={14} className="text-muted" aria-hidden />
                  </button>
                  <button
                    onClick={() => pin(m)}
                    disabled={busy === m.id}
                    aria-label={m.pinned ? "Unpin" : "Pin into every prompt"}
                    aria-pressed={m.pinned}
                    className={iconBtn}
                  >
                    <Pin size={14} className={m.pinned ? "text-accent-soft" : "text-muted"} aria-hidden />
                  </button>
                  <button
                    onClick={() => remove(m)}
                    disabled={busy === m.id}
                    aria-label="Forget this memory"
                    className={iconBtn}
                  >
                    <Trash2 size={14} className="text-muted" aria-hidden />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
