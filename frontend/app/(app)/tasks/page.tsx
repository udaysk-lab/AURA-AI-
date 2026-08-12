"use client";

import { useCallback, useEffect, useState } from "react";
import { Columns3, List, Plus, Trash2 } from "lucide-react";
import { Task, api } from "@/lib/api";
import { Badge, Card, EmptyState, PageHeader, Skeleton, fmtDay } from "@/components/ui";

const COLUMNS: Array<{ key: Task["status"]; label: string }> = [
  { key: "todo", label: "To do" },
  { key: "doing", label: "In progress" },
  { key: "done", label: "Done" },
];

const PRIORITY_ORDER: Task["priority"][] = ["urgent", "high", "medium", "low"];

export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"board" | "list">("board");
  const [quick, setQuick] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTasks(await api.tasks());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!quick.trim()) return;
    await api.quickTask(quick.trim());
    setQuick("");
    await load();
  };

  const move = async (task: Task, status: Task["status"]) => {
    setTasks((prev) => prev.map((t) => (t.id === task.id ? { ...t, status } : t)));
    await api.updateTask(task.id, { status });
    await load();
  };

  const remove = async (task: Task) => {
    setTasks((prev) => prev.filter((t) => t.id !== task.id));
    await api.deleteTask(task.id);
  };

  const overdue = (t: Task) =>
    t.due_at && t.status !== "done" && new Date(t.due_at) < new Date();

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-8">
      <PageHeader
        title="Tasks"
        blurb="Everything you committed to, including the promises buried in replies."
        glow="amber"
        action={
          <div className="flex gap-1 rounded-full border border-line bg-raised/40 p-1 backdrop-blur-md">
            <button
              onClick={() => setView("board")}
              className={`rounded-full px-3 py-1.5 text-[12px] transition ${
                view === "board"
                  ? "bg-accent/15 text-ink shadow-glow-sm"
                  : "text-muted hover:text-ink"
              }`}
            >
              <Columns3 size={13} className="mr-1 inline" /> Board
            </button>
            <button
              onClick={() => setView("list")}
              className={`rounded-full px-3 py-1.5 text-[12px] transition ${
                view === "list"
                  ? "bg-accent/15 text-ink shadow-glow-sm"
                  : "text-muted hover:text-ink"
              }`}
            >
              <List size={13} className="mr-1 inline" /> List
            </button>
          </div>
        }
      />

      <div className="panel flex items-center gap-2 p-2 transition-colors focus-within:border-accent/40">
        <Plus size={15} className="ml-2 text-faint" />
        <input
          className="flex-1 bg-transparent px-1 py-2 text-[13.5px] text-ink placeholder:text-faint outline-none"
          placeholder="Quick add — e.g. 'send board deck tomorrow 5pm urgent'"
          value={quick}
          onChange={(e) => setQuick(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <button onClick={add} disabled={!quick.trim()} className="btn-primary py-1.5">
          Add
        </button>
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
          <Skeleton className="h-64" />
        </div>
      ) : view === "board" ? (
        <div className="grid gap-4 md:grid-cols-3">
          {COLUMNS.map((col) => {
            const items = tasks.filter((t) => t.status === col.key);
            return (
              <div key={col.key} className="panel p-3">
                <div className="mb-3 flex items-center justify-between px-1">
                  <span className="label">{col.label}</span>
                  <span className="rounded-full border border-line bg-raised/60 px-2 py-0.5 text-[10.5px] text-muted">
                    {items.length}
                  </span>
                </div>
                <div className="space-y-2">
                  {items.length === 0 && (
                    <div className="rounded-xl border border-dashed border-line px-3 py-6 text-center text-[12px] text-faint">
                      Empty
                    </div>
                  )}
                  {items.map((t) => (
                    <div
                      key={t.id}
                      className="panel-raised group p-3 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30"
                    >
                      <div className="mb-2 flex items-start gap-2">
                        <p
                          className={`min-w-0 flex-1 text-[13px] leading-snug ${
                            t.status === "done" ? "text-faint line-through" : "text-ink/90"
                          }`}
                        >
                          {t.title}
                        </p>
                        <button
                          onClick={() => remove(t)}
                          className="shrink-0 opacity-0 transition group-hover:opacity-100"
                        >
                          <Trash2 size={12} className="text-faint transition-colors hover:text-rose" />
                        </button>
                      </div>
                      <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
                        <Badge tone={t.priority}>{t.priority}</Badge>
                        {t.due_at && (
                          <span
                            className={`text-[11px] ${
                              overdue(t) ? "text-rose" : "text-faint"
                            }`}
                          >
                            {overdue(t) ? "overdue · " : ""}
                            {fmtDay(t.due_at)}
                          </span>
                        )}
                        {t.recurrence && <Badge>{t.recurrence}</Badge>}
                        {t.source === "agent" && <Badge tone="accent">AURA</Badge>}
                      </div>
                      <div className="flex gap-1">
                        {COLUMNS.filter((c) => c.key !== t.status).map((c) => (
                          <button
                            key={c.key}
                            onClick={() => move(t, c.key)}
                            className="rounded-md border border-line px-2 py-0.5 text-[10.5px] text-muted transition hover:border-accent/40 hover:text-ink"
                          >
                            → {c.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <Card className="p-2">
          {tasks.length === 0 ? (
            <EmptyState title="No tasks yet" hint="Add one above, or ask AURA in Chat." />
          ) : (
            [...tasks]
              .sort(
                (a, b) =>
                  PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority)
              )
              .map((t) => (
                <div
                  key={t.id}
                  className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition hover:bg-raised/60"
                >
                  <input
                    type="checkbox"
                    checked={t.status === "done"}
                    onChange={() => move(t, t.status === "done" ? "todo" : "done")}
                    className="h-4 w-4 shrink-0 accent-[rgb(var(--accent))]"
                  />
                  <span
                    className={`min-w-0 flex-1 truncate text-[13.5px] ${
                      t.status === "done" ? "text-faint line-through" : "text-ink/90"
                    }`}
                  >
                    {t.title}
                  </span>
                  <Badge tone={t.priority}>{t.priority}</Badge>
                  {t.due_at && (
                    <span
                      className={`shrink-0 text-[11.5px] ${
                        overdue(t) ? "text-rose" : "text-faint"
                      }`}
                    >
                      {fmtDay(t.due_at)}
                    </span>
                  )}
                  <button
                    onClick={() => remove(t)}
                    className="shrink-0 opacity-0 transition group-hover:opacity-100"
                  >
                    <Trash2 size={13} className="text-faint transition-colors hover:text-rose" />
                  </button>
                </div>
              ))
          )}
        </Card>
      )}
    </div>
  );
}
