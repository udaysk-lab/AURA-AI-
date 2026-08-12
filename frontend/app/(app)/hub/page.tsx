"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Lock, Plus, Puzzle, Trash2 } from "lucide-react";
import { PluginItem, PluginSummary, api } from "@/lib/api";
import { useAction } from "@/components/Toast";
import {
  Badge,
  Card,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  Stat,
} from "@/components/ui";

const ACCENT_RING: Record<string, string> = {
  teal: "from-teal/30",
  amber: "from-amber/30",
  rose: "from-rose/30",
  violet: "from-violet/30",
  sage: "from-sage/30",
};

const ACCENT_TEXT: Record<string, string> = {
  teal: "text-teal",
  amber: "text-amber",
  rose: "text-rose",
  violet: "text-violet",
  sage: "text-sage",
};

export default function HubPage() {
  const run = useAction();
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [summary, setSummary] = useState<PluginSummary | null>(null);
  const [categories, setCategories] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [p, s, c] = await Promise.all([
        api.plugins(),
        api.pluginSummary(),
        api.pluginCategories(),
      ]);
      setPlugins(p);
      setSummary(s);
      setCategories(c);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (plugin: PluginItem) => {
    setBusy(plugin.id);
    await run(
      async () => {
        if (plugin.installed) await api.uninstallPlugin(plugin.id);
        else await api.installPlugin(plugin.id);
        await load();
      },
      plugin.installed ? `${plugin.name} removed` : `${plugin.name} installed`
    );
    setBusy(null);
  };

  const visible = filter ? plugins.filter((p) => p.category === filter) : plugins;

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-6 sm:p-8">
      <PageHeader
        title="Plugin hub"
        blurb="Plugins are bundles of skills. Installing one gives your assistant new abilities; removing it takes them away without losing what the skills already learned about you."
      />

      {summary && (
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: "Installed", value: summary.installed },
            { label: "Available", value: summary.available },
            { label: "In the catalogue", value: summary.total },
          ].map((s) => (
            <Card key={s.label}>
              <Stat value={s.value} label={s.label} />
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
                : "border-line text-muted hover:border-accent/30 hover:text-ink"
            }`}
          >
            {c}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {visible.map((p) => (
            <div
              key={p.id}
              className={`panel relative overflow-hidden p-5 transition-all duration-200 ${
                p.available
                  ? "hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-lift"
                  : "opacity-55"
              } ${p.installed ? "border-accent/25" : ""}`}
            >
              <div
                className={`pointer-events-none absolute inset-x-0 top-0 h-24 bg-gradient-to-b ${
                  ACCENT_RING[p.accent] ?? ACCENT_RING.teal
                } to-transparent opacity-60`}
              />
              <div className="relative">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2.5">
                    <div
                      className={`flex h-9 w-9 items-center justify-center rounded-xl border border-line bg-raised/70 ${
                        ACCENT_TEXT[p.accent] ?? ACCENT_TEXT.teal
                      }`}
                    >
                      <Puzzle size={16} />
                    </div>
                    <div>
                      <div className="text-[15px] font-semibold leading-tight">{p.name}</div>
                      <div className="text-[11.5px] text-faint">{p.category}</div>
                    </div>
                  </div>
                  {p.core ? (
                    <Badge tone="accent">
                      <Lock size={9} /> core
                    </Badge>
                  ) : p.installed ? (
                    <Badge tone="success">
                      <Check size={9} /> installed
                    </Badge>
                  ) : null}
                </div>

                <p className="mb-2 text-[13px] font-medium">{p.summary}</p>
                <p className="mb-3 text-[12.5px] leading-relaxed text-muted">{p.detail}</p>

                {p.skill_names.length > 0 && (
                  <div className="mb-4 flex flex-wrap gap-1.5">
                    {p.skills.map((code, i) => (
                      <span
                        key={code}
                        className="chip border-line bg-raised text-muted"
                        title={p.skill_names[i]}
                      >
                        <span className="font-mono text-[10px]">{code}</span>
                        {p.skill_names[i]}
                      </span>
                    ))}
                  </div>
                )}

                {!p.available ? (
                  <div className="rounded-xl border border-dashed border-line px-3 py-2 text-[12px] text-faint">
                    {p.unavailable_reason}
                  </div>
                ) : p.core ? (
                  <div className="text-[12px] text-faint">
                    Core plugins can&apos;t be removed — without them there is no assistant.
                  </div>
                ) : (
                  <button
                    onClick={() => toggle(p)}
                    disabled={busy === p.id}
                    className={p.installed ? "btn-ghost py-1.5" : "btn-primary py-1.5"}
                  >
                    {busy === p.id ? (
                      <Spinner />
                    ) : p.installed ? (
                      <Trash2 size={13} />
                    ) : (
                      <Plus size={13} />
                    )}
                    {p.installed ? "Remove" : "Install"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <Card>
        <SectionTitle>What&apos;s coming</SectionTitle>
        <p className="text-[12.5px] leading-relaxed text-muted">
          Greyed-out plugins need a connector that hasn&apos;t been built yet. They&apos;re
          shown rather than hidden because a plugin that pretends to work is worse than one
          that says it doesn&apos;t.
        </p>
      </Card>
    </div>
  );
}
