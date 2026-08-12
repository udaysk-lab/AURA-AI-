"use client";

import { useCallback, useEffect, useState } from "react";
import { GraduationCap, Radio, Sparkles, Wand2, X } from "lucide-react";
import { SkillItem, SkillRun, SkillStats, api } from "@/lib/api";
import {
  Badge,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
  SkillLine,
  Skeleton,
  Spinner,
  Stat,
  Toggle,
  fmtRelative,
} from "@/components/ui";

const CATEGORY_TONE: Record<string, string> = {
  Email: "accent",
  Calendar: "medium",
  Meetings: "violet",
  Tasks: "warning",
  Memory: "success",
  People: "urgent",
  Planning: "medium",
  System: "normal",
};

export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [runs, setRuns] = useState<SkillRun[]>([]);
  const [stats, setStats] = useState<SkillStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [teaching, setTeaching] = useState<SkillItem | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, a, st] = await Promise.all([
        api.skills(),
        api.skillActivity(40),
        api.skillStats(),
      ]);
      setSkills(s);
      setRuns(a);
      setStats(st);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (skill: SkillItem) => {
    setSkills((prev) =>
      prev.map((s) => (s.code === skill.code ? { ...s, enabled: !s.enabled } : s))
    );
    await api.toggleSkill(skill.code, !skill.enabled);
    await load();
  };

  const teach = async () => {
    if (!teaching || !note.trim()) return;
    setSaving(true);
    try {
      await api.teachSkill(teaching.code, note.trim());
      setNote("");
      setTeaching(null);
      await load();
    } finally {
      setSaving(false);
    }
  };

  const categories = Array.from(new Set(skills.map((s) => s.category)));
  const visible = filter ? skills.filter((s) => s.category === filter) : skills;
  const grouped = visible.reduce<Record<string, SkillItem[]>>((acc, s) => {
    (acc[s.category] ||= []).push(s);
    return acc;
  }, {});

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-8">
      <PageHeader
        title="Skills"
        blurb={
          <>
            What your assistant can actually do. Turning one off removes it from their
            reach entirely — they can&apos;t use what they can&apos;t see. Teach a skill
            and the correction sticks.
          </>
        }
      />

      {stats && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          {[
            { label: "Skills available", value: stats.total, small: false },
            { label: "Turned on", value: stats.enabled, small: false },
            { label: "Times used", value: stats.total_runs, small: false },
            { label: "Most used", value: stats.most_used_name || "—", small: true },
          ].map((s) => (
            <Card key={s.label}>
              <Stat value={s.value} label={s.label} small={s.small} />
            </Card>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setFilter("")}
          className={`rounded-full border px-3 py-1.5 text-[12.5px] transition ${
            !filter
              ? "border-accent/60 bg-accent-dim text-accent-soft shadow-glow-sm"
              : "border-line text-muted hover:border-accent/30 hover:text-ink"
          }`}
        >
          All
        </button>
        {categories.map((c) => (
          <button
            key={c}
            onClick={() => setFilter(c)}
            className={`rounded-full border px-3 py-1.5 text-[12.5px] transition ${
              filter === c
                ? "border-accent/60 bg-accent-dim text-accent-soft shadow-glow-sm"
                : "border-line text-muted hover:text-ink"
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="grid gap-3 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : (
        Object.entries(grouped).map(([category, items]) => (
          <div key={category}>
            <SectionTitle>{category}</SectionTitle>
            <div className="grid gap-3 md:grid-cols-2">
              {items.map((s) => (
                <div
                  key={s.code}
                  className={`panel p-4 transition-all duration-200 ${
                    s.enabled
                      ? "hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-lift"
                      : "opacity-45 grayscale"
                  }`}
                >
                  <div className="mb-2 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="mb-1 flex items-center gap-2">
                        <span className="font-mono text-[10.5px] text-accent-soft">
                          {s.code}
                        </span>
                        <span className="text-[14px] font-medium">{s.name}</span>
                      </div>
                      <p className="text-[12.5px] leading-relaxed text-muted">
                        {s.description}
                      </p>
                    </div>
                    <Toggle
                      checked={s.enabled}
                      onChange={() => toggle(s)}
                      label={`Enable ${s.name}`}
                    />
                  </div>

                  <div className="mb-3 flex flex-wrap items-center gap-1.5">
                    <Badge tone={CATEGORY_TONE[s.category] ?? "normal"}>{s.category}</Badge>
                    {s.proactive && (
                      <Badge tone="success">
                        <Radio size={9} /> runs on its own
                      </Badge>
                    )}
                    {s.autonomy_floor === "full" && <Badge tone="urgent">always asks</Badge>}
                    <span className="text-[11px] text-faint">
                      used {s.run_count}×
                      {s.last_run_at ? ` · ${fmtRelative(s.last_run_at)}` : ""}
                    </span>
                  </div>

                  {s.learned_notes.length > 0 && (
                    <div className="mb-3 rounded-xl border border-line bg-raised/50 p-3">
                      <div className="label mb-1.5 flex items-center gap-1.5">
                        <GraduationCap size={11} /> What it learned
                      </div>
                      <ul className="space-y-1">
                        {s.learned_notes.map((n, i) => (
                          <li key={i} className="text-[12px] leading-relaxed text-muted">
                            — {n}
                          </li>
                        ))}
                      </ul>
                      <button
                        onClick={async () => {
                          await api.clearSkillNotes(s.code);
                          await load();
                        }}
                        className="mt-2 text-[11px] text-faint transition hover:text-ink"
                      >
                        <X size={10} className="inline" /> clear
                      </button>
                    </div>
                  )}

                  <button
                    onClick={() => {
                      setTeaching(s);
                      setNote("");
                    }}
                    className="btn-quiet px-0 py-0 text-[12px]"
                  >
                    <Wand2 size={12} /> Teach it something
                  </button>
                </div>
              ))}
            </div>
          </div>
        ))
      )}

      <Card>
        <SectionTitle
          action={
            <button onClick={load} className="text-[11.5px] text-muted hover:text-ink">
              Refresh
            </button>
          }
        >
          Activity log
        </SectionTitle>
        {runs.length === 0 ? (
          <EmptyState
            title="Nothing run yet"
            hint="Ask for something in Chat, or let the heartbeat do a pass."
            icon={<Sparkles size={18} />}
          />
        ) : (
          <div className="max-h-[420px] overflow-y-auto rounded-xl border border-line bg-raised/50 px-3 py-2">
            {runs.map((r) => (
              <SkillLine
                key={r.id}
                code={r.code}
                summary={r.summary}
                meta={`${r.trigger} · ${fmtRelative(r.created_at)}`}
                ok={r.status === "success"}
              />
            ))}
          </div>
        )}
      </Card>

      <Modal
        open={!!teaching}
        onClose={() => setTeaching(null)}
        title={teaching ? `Teach ${teaching.name}` : ""}
      >
        <div className="space-y-3">
          <p className="text-[12.5px] leading-relaxed text-muted">
            Write it as an instruction. It gets replayed into the prompt every time this
            skill is in scope, so keep it short and specific.
          </p>
          <textarea
            className="input min-h-[90px]"
            placeholder="Never schedule anything before 10am. Keep replies under three sentences."
            value={note}
            onChange={(e) => setNote(e.target.value)}
            autoFocus
          />
          <button
            onClick={teach}
            disabled={saving || note.trim().length < 3}
            className="btn-primary w-full"
          >
            {saving ? <Spinner /> : <GraduationCap size={14} />} Save lesson
          </button>
        </div>
      </Modal>
    </div>
  );
}
