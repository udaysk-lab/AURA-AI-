"use client";

import { useCallback, useEffect, useState } from "react";
import { Clock, Play, Plus, Timer, Trash2 } from "lucide-react";
import { ScheduleItem, api } from "@/lib/api";
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
  Toggle,
  fmtRelative,
} from "@/components/ui";

const EXAMPLES = [
  {
    when: "Every weekday at 7:30am",
    prompt:
      "Give me my morning briefing: today's meetings, what's due, anything urgent in the inbox, and the three things you'd do first.",
  },
  {
    when: "Every Friday at 4pm",
    prompt:
      "Summarise the week: what I finished, what slipped, what's outstanding with other people, and what next week looks like.",
  },
  {
    when: "Every day at 6pm",
    prompt:
      "Anything I promised someone today that I haven't turned into a task? Check my sent mail and today's meetings.",
  },
  {
    when: "Every Monday at 9am",
    prompt:
      "Check my calendar for the next two weeks and flag any conflicts, any meeting without an agenda, and anything landing in my focus hours.",
  },
];

export default function SchedulesPage() {
  const run = useAction();
  const [schedules, setSchedules] = useState<ScheduleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<{ name: string; text: string } | null>(null);

  const [when, setWhen] = useState("");
  const [prompt, setPrompt] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSchedules(await api.schedules());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    if (!prompt.trim()) return;
    await run(
      () => api.createSchedule({ prompt: prompt.trim(), natural_language: when.trim() }),
      "Schedule created"
    );
    setOpen(false);
    setWhen("");
    setPrompt("");
    await load();
  };

  const runNow = async (schedule: ScheduleItem) => {
    setBusy(schedule.id);
    const outcome = await run(() => api.runSchedule(schedule.id));
    if (outcome) setResult({ name: schedule.name, text: String(outcome.text ?? "") });
    setBusy(null);
    await load();
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6 sm:p-8">
      <PageHeader
        title="Schedules"
        glow="teal"
        blurb="Standing instructions your assistant runs on a timer. Unlike an automation — which fires a fixed list of actions — a schedule runs a full request, so it can think, look things up and decide."
        action={
          <button onClick={() => setOpen(true)} className="btn-primary">
            <Plus size={14} /> New schedule
          </button>
        }
      />

      {loading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : schedules.length === 0 ? (
        <>
          <EmptyState
            title="No schedules yet"
            hint="Pick one of the starters below, or write your own."
            icon={<Timer size={20} />}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {EXAMPLES.map((e) => (
              <button
                key={e.when}
                onClick={() => {
                  setWhen(e.when);
                  setPrompt(e.prompt);
                  setOpen(true);
                }}
                className="panel p-4 text-left transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-lift"
              >
                <div className="mb-1.5 flex items-center gap-2">
                  <Clock size={12} className="text-accent-soft" />
                  <span className="text-[12.5px] font-medium">{e.when}</span>
                </div>
                <p className="text-[12.5px] leading-relaxed text-muted">{e.prompt}</p>
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="space-y-3">
          {schedules.map((s) => (
            <div
              key={s.id}
              className={`panel p-5 transition-all duration-200 hover:border-accent/25 ${
                s.enabled ? "" : "opacity-60"
              }`}
            >
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-[15px] font-semibold">{s.name}</span>
                    <Badge tone="accent">
                      <Clock size={9} /> {s.cron_label}
                    </Badge>
                    <Badge>{s.deliver_to}</Badge>
                  </div>
                  <p className="text-[12.5px] leading-relaxed text-muted">{s.prompt}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    onClick={() => runNow(s)}
                    disabled={busy === s.id}
                    className="btn-quiet px-2 py-1"
                    title="Run now"
                  >
                    {busy === s.id ? <Spinner /> : <Play size={13} />}
                  </button>
                  <Toggle
                    checked={s.enabled}
                    onChange={async (v) => {
                      await run(() => api.updateSchedule(s.id, { enabled: v }));
                      await load();
                    }}
                    label={`Enable ${s.name}`}
                  />
                  <button
                    onClick={async () => {
                      await run(() => api.deleteSchedule(s.id), "Deleted");
                      await load();
                    }}
                    className="btn-quiet px-2 py-1"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-3 text-[11px] text-faint">
                <span className="font-mono">{s.cron}</span>
                <span>ran {s.run_count}×</span>
                {s.last_run_at && <span>last {fmtRelative(s.last_run_at)}</span>}
              </div>

              {s.last_result && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-[12px] text-muted hover:text-ink">
                    Last result
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-line bg-raised/50 p-3 font-sans text-[12.5px] leading-relaxed text-muted">
                    {s.last_result}
                  </pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}

      <Card>
        <SectionTitle>Requires the worker</SectionTitle>
        <p className="text-[12.5px] leading-relaxed text-muted">
          Schedules fire from the background worker. Start it alongside the API with{" "}
          <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11.5px]">
            python worker.py
          </code>
          . Without it, &ldquo;Run now&rdquo; still works but nothing fires on its own.
        </p>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="New schedule" width="max-w-xl">
        <div className="space-y-3">
          <div>
            <label className="label mb-1.5 block">When</label>
            <input
              className="input"
              placeholder="every weekday at 7:30am"
              value={when}
              onChange={(e) => setWhen(e.target.value)}
            />
            <p className="mt-1.5 text-[11.5px] text-faint">
              Plain English. Parsed into a cron expression you can edit afterwards.
            </p>
          </div>
          <div>
            <label className="label mb-1.5 block">What to do</label>
            <textarea
              className="input min-h-[130px]"
              placeholder="Give me my morning briefing…"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <p className="mt-1.5 text-[11.5px] text-faint">
              Write it as you&apos;d say it in chat. It runs through the full agent, so it
              can look things up and chain skills.
            </p>
          </div>
          <button onClick={create} disabled={!prompt.trim()} className="btn-primary w-full">
            Create schedule
          </button>
        </div>
      </Modal>

      <Modal
        open={!!result}
        onClose={() => setResult(null)}
        title={result?.name ?? ""}
        width="max-w-xl"
      >
        <pre className="max-h-[55vh] overflow-y-auto whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed">
          {result?.text}
        </pre>
      </Modal>
    </div>
  );
}
