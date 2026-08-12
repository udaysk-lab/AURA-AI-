"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Play, Plus, Trash2, Workflow, X, Zap } from "lucide-react";
import { Automation, PendingAction, api } from "@/lib/api";
import {
  Badge,
  Card,
  EmptyState,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  fmtRelative,
} from "@/components/ui";

const EXAMPLES = [
  "When I receive an email from my accountant, create a task and remind me tomorrow.",
  "Every weekday at 8:00 AM, prepare my daily briefing.",
  "If an email is marked urgent, draft a reply and ask me to approve it.",
];

export default function AutomationsPage() {
  const [rules, setRules] = useState<Automation[]>([]);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, p] = await Promise.all([api.automations(), api.pendingActions()]);
      setRules(r);
      setPending(p);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    if (!input.trim()) return;
    setCreating(true);
    try {
      await api.createAutomation(input.trim());
      setInput("");
      await load();
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-8">
      <PageHeader
        title="Automations"
        blurb="Describe a rule in plain English. AURA compiles it into a workflow it can run unattended — and always stops for approval before anything irreversible."
      />

      <Card>
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="When… then…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <button onClick={create} disabled={creating || !input.trim()} className="btn-primary">
            {creating ? <Spinner /> : <Plus size={14} />} Create
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => (
            <button
              key={e}
              onClick={() => setInput(e)}
              className="rounded-full border border-line bg-raised/30 px-2.5 py-1 text-[11.5px] text-muted transition hover:border-accent/40 hover:text-ink"
            >
              {e.length > 52 ? `${e.slice(0, 52)}…` : e}
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
                    onClick={async () => {
                      await api.decidePending(p.id, "once");
                      await load();
                    }}
                    className="btn-primary py-1.5"
                  >
                    <Check size={13} /> Allow
                  </button>
                  <button
                    onClick={async () => {
                      await api.decidePending(p.id, "always");
                      await load();
                    }}
                    className="btn-ghost py-1.5"
                  >
                    Always
                  </button>
                  <button
                    onClick={async () => {
                      await api.decidePending(p.id, "reject");
                      await load();
                    }}
                    className="btn-quiet py-1.5"
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
                    onClick={async () => {
                      await api.runAutomation(r.id);
                      await load();
                    }}
                    title="Run now"
                    className="btn-quiet px-2 py-1"
                  >
                    <Play size={13} />
                  </button>
                  <button
                    onClick={async () => {
                      await api.updateAutomation(r.id, { enabled: !r.enabled });
                      await load();
                    }}
                    className={`relative h-5 w-9 rounded-full transition ${
                      r.enabled ? "bg-accent shadow-glow-sm" : "bg-raised border border-line"
                    }`}
                  >
                    <span
                      className={`absolute top-[1px] h-4 w-4 rounded-full bg-white shadow-sm transition-all ${
                        r.enabled ? "left-[18px]" : "left-0.5"
                      }`}
                    />
                  </button>
                  <button
                    onClick={async () => {
                      await api.deleteAutomation(r.id);
                      await load();
                    }}
                    className="btn-quiet px-2 py-1"
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

              <div className="mt-3 flex items-center gap-3 border-t border-line pt-3 text-[11px] text-faint">
                <span>ran {r.run_count}×</span>
                {r.last_run_at && <span>last {fmtRelative(r.last_run_at)}</span>}
                {r.requires_confirmation && <span>· asks before acting</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
