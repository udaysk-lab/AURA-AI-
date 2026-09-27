"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Play, Plus, Trash2, Workflow, X, Zap } from "lucide-react";
import { Automation, Decision, PendingAction, api } from "@/lib/api";
import { useAction } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  Toggle,
  fmtRelative,
  refreshBadges,
} from "@/components/ui";

const EXAMPLES = [
  "When I receive an email from my accountant, create a task and remind me tomorrow.",
  "Every weekday at 8:00 AM, prepare my daily briefing.",
  "If an email is marked urgent, draft a reply and ask me to approve it.",
];

export default function AutomationsPage() {
  const run = useAction();
  const [rules, setRules] = useState<Automation[]>([]);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [creating, setCreating] = useState(false);
  // Which rule or approval is mid-request, so its buttons can't be pressed twice.
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, p] = await Promise.all([api.automations(), api.pendingActions()]);
      setRules(r);
      setPending(p);
    } catch {
      /* keep what's on screen; actions report their own failures */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    if (!input.trim() || creating) return;
    setCreating(true);
    const rule = await run(() => api.createAutomation(input.trim()), "Automation created");
    setCreating(false);
    if (!rule) return;
    setInput("");
    await load();
  };

  const act = async (id: string, fn: () => Promise<unknown>, success?: string) => {
    setBusy(id);
    await run(fn, success);
    setBusy(null);
    await load();
  };

  const decide = (p: PendingAction, decision: Decision) =>
    act(
      p.id,
      async () => {
        await api.decidePending(p.id, decision);
        refreshBadges();
      },
      decision === "reject" ? "Dismissed" : "Approved"
    );

  const remove = (r: Automation) => {
    if (!window.confirm(`Delete the "${r.name}" automation?`)) return;
    void act(r.id, () => api.deleteAutomation(r.id), "Deleted");
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-8">
      <PageHeader
        title="Automations"
        blurb="Describe a rule in plain English. AURA compiles it into a workflow it can run unattended — and always stops for approval before anything irreversible."
      />

      <Card>
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <input
            className="input min-w-0 flex-1"
            placeholder="When… then…"
            aria-label="Describe an automation"
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <button
            type="submit"
            disabled={creating || !input.trim()}
            className="btn-primary shrink-0"
          >
            {creating ? <Spinner /> : <Plus size={14} />} Create
          </button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => (
            <button
              key={e}
              onClick={() => setInput(e)}
              title={e}
              className="max-w-full truncate rounded-full border border-line bg-raised/30 px-2.5 py-1 text-[11.5px] text-muted transition hover:border-accent/40 hover:text-ink"
            >
              {e}
            </button>
          ))}
        </div>
      </Card>

      {pending.length > 0 && (
        <Card className="border-amber/35 bg-amber/[0.06]">
          <SectionTitle>
            <span className="text-amber">Queued by automations</span>
          </SectionTitle>
          <div className="space-y-2">
            {pending.map((p) => (
              <div key={p.id} className="panel-raised flex flex-wrap items-center gap-3 p-3">
                <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words font-sans text-[12.5px] text-muted">
                  {p.preview}
                </pre>
                <div className="flex gap-2">
                  <button
                    onClick={() => decide(p, "once")}
                    disabled={busy === p.id}
                    className="btn-primary py-1.5"
                  >
                    {busy === p.id ? <Spinner /> : <Check size={13} />} Allow
                  </button>
                  <button
                    onClick={() => decide(p, "always")}
                    disabled={busy === p.id}
                    className="btn-ghost py-1.5"
                  >
                    Always
                  </button>
                  <button
                    onClick={() => decide(p, "reject")}
                    disabled={busy === p.id}
                    className="btn-quiet py-1.5"
                    aria-label="Don't do this"
                    title="Don't do this"
                  >
                    <X size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : rules.length === 0 ? (
        <EmptyState
          title="No automations yet"
          hint="Try one of the examples above."
          icon={<Workflow size={18} />}
        />
      ) : (
        <div className="space-y-3">
          {rules.map((r) => (
            <div
              key={r.id}
              className={`panel p-5 transition-all duration-200 hover:border-accent/25 ${
                r.enabled ? "" : "opacity-60"
              }`}
            >
              <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="mb-1 flex items-center gap-2">
                    <Zap size={13} className={r.enabled ? "text-accent-soft" : "text-faint"} />
                    <span className="text-[14px] font-medium text-ink">{r.name}</span>
                  </div>
                  <p className="text-[12.5px] italic text-faint">
                    &ldquo;{r.natural_language}&rdquo;
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() =>
                      act(
                        r.id,
                        async () => {
                          await api.runAutomation(r.id);
                          refreshBadges();
                        },
                        `Ran “${r.name}”`
                      )
                    }
                    disabled={busy === r.id}
                    title="Run now"
                    aria-label={`Run ${r.name} now`}
                    className="btn-quiet px-2 py-1"
                  >
                    {busy === r.id ? <Spinner /> : <Play size={13} />}
                  </button>
                  <Toggle
                    checked={r.enabled}
                    disabled={busy === r.id}
                    label={`${r.enabled ? "Pause" : "Turn on"} ${r.name}`}
                    onChange={(enabled) =>
                      act(r.id, () => api.updateAutomation(r.id, { enabled }))
                    }
                  />
                  <button
                    onClick={() => remove(r)}
                    disabled={busy === r.id}
                    className="btn-quiet px-2 py-1"
                    aria-label={`Delete ${r.name}`}
                    title="Delete automation"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="accent">{r.trigger_type}</Badge>
                {Object.entries(r.trigger_config || {}).map(([k, v]) => (
                  <Badge key={k}>
                    {k}: {String(v)}
                  </Badge>
                ))}
                <span className="text-faint">→</span>
                {r.actions?.map((a, i) => (
                  <Badge key={i} tone="success">
                    {a.tool}
                  </Badge>
                ))}
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line pt-3 text-[11px] text-faint">
                <span>ran {r.run_count}×</span>
                {r.last_run_at && <span>· last {fmtRelative(r.last_run_at)}</span>}
                {r.requires_confirmation && <span>· asks before acting</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
