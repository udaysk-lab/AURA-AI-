"use client";

import { useState } from "react";
import { Check, ChevronDown, Circle, Minus, Square, X } from "lucide-react";
import type { PendingAction, PlanStep, WorkStatus } from "@/lib/api";
import { humanTool } from "@/lib/permissions";
import { Badge, Spinner } from "./ui";

/** What the panel shows about the task in view. Built by the chat page from events or the detail endpoint. */
export interface TaskPanelState {
  workItemId: string | null;
  title: string;
  taskType: string;
  status: WorkStatus | "starting";
  plan: PlanStep[];
  systems: string[];
  /** Counts from the `recall` event. The API gives counts, not the texts. */
  recall: { work_items: number; preferences: number; memories: number } | null;
  /** Preferences captured from this turn (`preference_noted`). */
  noted: string[];
  outcome: string;
  /** From the `report` / `verification` events. */
  verification: string;
}

const STATUS_LABEL: Record<TaskPanelState["status"], string> = {
  starting: "Starting",
  running: "Working",
  waiting: "Waiting on you",
  ready: "Ready to carry on",
  done: "Done",
  partly_done: "Partly done",
  failed: "Didn't finish",
  stopped: "Stopped",
};

const STEP_LABEL: Record<PlanStep["status"], string> = {
  todo: "Not started",
  doing: "In progress",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
};

function StepIcon({ status }: { status: PlanStep["status"] }) {
  const cls = "mt-0.5 shrink-0";
  switch (status) {
    case "doing":
      return <Spinner className={`${cls} text-accent-soft`} />;
    case "done":
      return <Check size={14} className={`${cls} text-sage`} aria-hidden />;
    case "failed":
      return <X size={14} className={`${cls} text-rose`} aria-hidden />;
    case "skipped":
      return <Minus size={14} className={`${cls} text-faint`} aria-hidden />;
    default:
      return <Circle size={14} className={`${cls} text-faint`} aria-hidden />;
  }
}

function PanelSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="label">{title}</h3>
      {children}
    </section>
  );
}

export interface TaskPanelProps {
  state: TaskPanelState;
  /** Approvals that belong to this task. */
  waiting: PendingAction[];
  /** The task is running (or the page is streaming it): show Stop. */
  canStop: boolean;
  stopping?: boolean;
  onStop?: () => void;
}

/**
 * The task beside the conversation: the plan and where it has got to, what is
 * waiting on the person, the systems used, and what AURA recalled for it. On a
 * narrow screen it folds into a one-line bar that opens on tap.
 */
export default function TaskPanel({ state, waiting, canStop, stopping, onStop }: TaskPanelProps) {
  const [open, setOpen] = useState(false);
  const done = state.plan.filter((p) => p.status === "done" || p.status === "skipped").length;
  const recall = state.recall;
  const recalled =
    recall && recall.work_items + recall.preferences + recall.memories > 0 ? recall : null;
  const bodyId = "task-panel-body";
  const active = state.status === "running" || state.status === "starting";

  return (
    <aside
      aria-label="Task"
      className="shrink-0 border-b border-line bg-canvas/40 backdrop-blur-xl lg:w-80 lg:overflow-y-auto lg:border-b-0 lg:border-l"
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={bodyId}
        className="flex min-h-[44px] w-full items-center gap-2 px-4 py-2 text-left lg:hidden"
      >
        {active ? <Spinner className="text-accent-soft" /> : <Square size={12} className="text-faint" aria-hidden />}
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
          {state.title || "Task"}
        </span>
        <span className="shrink-0 text-[12px] text-muted">
          {STATUS_LABEL[state.status]}
          {state.plan.length > 0 && ` · ${done}/${state.plan.length}`}
        </span>
        <ChevronDown
          size={14}
          className={`shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>

      <div id={bodyId} className={`${open ? "block" : "hidden"} space-y-5 p-4 lg:block`}>
        <div className="space-y-1.5">
          <h2 className="text-[14px] font-semibold leading-snug">{state.title || "Task"}</h2>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={state.status === "waiting" ? "warning" : state.status === "failed" ? "urgent" : "accent"}>
              {STATUS_LABEL[state.status]}
            </Badge>
            {state.taskType && state.taskType !== "other" && (
              <span className="text-[12px] text-muted">{state.taskType.replace(/_/g, " ")}</span>
            )}
          </div>
          {canStop && onStop && (
            <button
              type="button"
              onClick={onStop}
              disabled={stopping}
              className="btn-ghost mt-1 min-h-[44px] w-full"
            >
              <Square size={12} aria-hidden /> {stopping ? "Stopping…" : "Stop this task"}
            </button>
          )}
          {state.outcome && !active && (
            <p className="text-[12.5px] leading-relaxed text-muted">{state.outcome}</p>
          )}
          {state.verification && state.verification !== "none" && !active && (
            <p className="text-[12px] text-muted">Check: {state.verification.replace(/_/g, " ")}</p>
          )}
        </div>

        <PanelSection title="Plan">
          {state.plan.length === 0 ? (
            <p className="text-[12.5px] text-muted">
              {active ? "Working out the steps…" : "No steps were listed for this task."}
            </p>
          ) : (
            <ol className="space-y-2">
              {state.plan.map((p) => (
                <li key={p.index} className="flex items-start gap-2 text-[12.5px] leading-snug">
                  <StepIcon status={p.status} />
                  <span className="min-w-0 flex-1">
                    <span className={p.status === "done" ? "text-muted" : "text-ink"}>{p.text}</span>
                    <span className="sr-only"> ({STEP_LABEL[p.status]})</span>
                    {p.note && <span className="block text-[11.5px] text-muted">{p.note}</span>}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </PanelSection>

        {waiting.length > 0 && (
          <PanelSection title="Waiting on you">
            <ul className="space-y-1.5">
              {waiting.map((w) => (
                <li key={w.id}>
                  <button
                    type="button"
                    onClick={() => {
                      const el = document.getElementById(`approval-${w.id}`);
                      el?.scrollIntoView({ behavior: "smooth", block: "center" });
                      el?.focus({ preventScroll: true });
                    }}
                    className="min-h-[44px] w-full rounded-xl border border-amber/35 bg-amber/10 px-3 py-2 text-left text-[12.5px] text-ink transition hover:border-amber/60"
                  >
                    Allow AURA to {humanTool(w.tool_name).toLowerCase()}?
                    <span className="block text-[11.5px] text-muted">Answer in the conversation</span>
                  </button>
                </li>
              ))}
            </ul>
          </PanelSection>
        )}

        <PanelSection title="Systems used">
          {state.systems.length === 0 ? (
            <p className="text-[12.5px] text-muted">None yet.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {state.systems.map((s) => (
                <Badge key={s}>{s}</Badge>
              ))}
            </div>
          )}
        </PanelSection>

        <PanelSection title="Remembered for this task">
          {!recalled && state.noted.length === 0 ? (
            <p className="text-[12.5px] text-muted">Nothing relevant found.</p>
          ) : (
            <ul className="space-y-1 text-[12.5px] text-muted">
              {recalled && recalled.work_items > 0 && (
                <li>
                  {recalled.work_items} similar past task{recalled.work_items === 1 ? "" : "s"}
                </li>
              )}
              {recalled && recalled.preferences > 0 && (
                <li>
                  {recalled.preferences} preference{recalled.preferences === 1 ? "" : "s"}
                </li>
              )}
              {recalled && recalled.memories > 0 && (
                <li>
                  {recalled.memories} saved fact{recalled.memories === 1 ? "" : "s"}
                </li>
              )}
              {state.noted.map((n, i) => (
                <li key={i} className="text-ink">
                  Noted: {n}
                </li>
              ))}
            </ul>
          )}
        </PanelSection>
      </div>
    </aside>
  );
}
