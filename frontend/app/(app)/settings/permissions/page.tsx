"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { Check, Lock, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import {
  CatalogueEntry,
  PermissionEvent,
  PermissionRule,
  PermissionState,
  EventsVerify,
  RuleEffect,
  RuleTargetKind,
  api,
} from "@/lib/api";
import {
  ALWAYS_NEEDS_YOU,
  EFFECTS,
  MODES,
  describeTarget,
  humanSystem,
  humanTool,
  targetKindLabel,
} from "@/lib/permissions";
import ModePicker from "@/components/ModePicker";
import { useAction } from "@/components/Toast";
import { Badge, Card, PageHeader, SectionTitle, Skeleton, Spinner, toDate } from "@/components/ui";

const EVENT_BATCH = 200;
const EVENT_PAGE = 20;

/** Kinds of log entry that are changes to permissions (the rest are decisions and approvals). */
const CHANGE_KIND = /^(mode_changed|rule_added|rule_revoked|folder_.*|app_.*|block_.*|system_changed|schedule_mode_changed|boundaries_changed|self_change_blocked|legacy_mapped)$/;

/** Catalogue target names to the target kinds a rule can use. */
const TARGET_KIND_OF: Record<string, RuleTargetKind> = {
  path: "path",
  command: "command",
  recipient: "recipient",
  url: "domain",
  domain: "domain",
  app: "app",
};

const TARGET_PLACEHOLDER: Record<string, string> = {
  path: "D:\\Work\\Finance\\**",
  command: "git status",
  recipient: "name@example.com",
  domain: "example.com",
  app: "chrome",
};

const DURATIONS: Array<{ label: string; minutes: number | undefined }> = [
  { label: "Until I remove it", minutes: undefined },
  { label: "10 minutes", minutes: 10 },
  { label: "1 hour", minutes: 60 },
];

function targetsOf(entry: CatalogueEntry): RuleTargetKind[] {
  const kinds = new Set<RuleTargetKind>();
  for (const t of entry.targets ?? []) {
    const name = typeof t === "string" ? t : (t.kind ?? t.name ?? "");
    const kind = TARGET_KIND_OF[name];
    if (kind) kinds.add(kind);
  }
  return Array.from(kinds);
}

export default function PermissionsPage() {
  const run = useAction();
  const [perm, setPerm] = useState<PermissionState | null>(null);
  const [boundaries, setBoundaries] = useState("");
  const [savingBoundaries, setSavingBoundaries] = useState(false);
  const [catalogue, setCatalogue] = useState<CatalogueEntry[]>([]);
  const [systemNames, setSystemNames] = useState<Record<string, string>>({});
  const [rules, setRules] = useState<PermissionRule[]>([]);
  const [events, setEvents] = useState<PermissionEvent[]>([]);
  const [lastBatch, setLastBatch] = useState(0);
  const [shown, setShown] = useState(EVENT_PAGE);
  const [verify, setVerify] = useState<EventsVerify | null>(null);
  const [verifyFailed, setVerifyFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  const systemName = useCallback(
    (id: string) =>
      systemNames[id] ?? (id === "device-control" ? "This computer" : humanSystem(id)),
    [systemNames]
  );

  const loadRules = useCallback(async () => {
    setRules(await api.permissionRules());
  }, []);

  const loadEvents = useCallback(async () => {
    const batch = await api.permissionEvents(EVENT_BATCH);
    setEvents(batch);
    setLastBatch(batch.length);
    setShown(EVENT_PAGE);
    try {
      setVerify(await api.verifyPermissionEvents());
      setVerifyFailed(false);
    } catch {
      setVerify(null);
      setVerifyFailed(true);
    }
  }, []);

  const loadAll = useCallback(async () => {
    try {
      const [cat, plugins] = await Promise.all([
        api.permissionCatalogue().catch(() => [] as CatalogueEntry[]),
        api.plugins().catch(() => []),
      ]);
      setCatalogue(cat);
      setSystemNames(Object.fromEntries(plugins.map((p) => [p.id, p.name])));
      await Promise.all([loadRules().catch(() => {}), loadEvents().catch(() => {})]);
    } finally {
      setLoading(false);
    }
  }, [loadRules, loadEvents]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const onMode = useCallback((state: PermissionState) => {
    setPerm(state);
    setBoundaries(state.boundaries ?? "");
  }, []);

  const saveBoundaries = async () => {
    setSavingBoundaries(true);
    const next = await run(() => api.setPermissionBoundaries(boundaries.trim()), "Saved");
    setSavingBoundaries(false);
    if (next) {
      setPerm(next);
      setBoundaries(next.boundaries ?? boundaries);
      await loadEvents().catch(() => {});
    }
  };

  const changeEffect = async (rule: PermissionRule, effect: RuleEffect) => {
    if (!rule.id || effect === rule.effect) return;
    // There is no edit call: add the new rule first, then remove the old one,
    // so a failure leaves the old rule in place rather than none.
    const minutes = rule.expires_at
      ? Math.max(1, Math.ceil((toDate(rule.expires_at).getTime() - Date.now()) / 60000))
      : undefined;
    const created = await run(() =>
      api.addPermissionRule({
        effect,
        system: rule.system,
        tool: rule.tool || undefined,
        target_kind: (rule.target_kind || "") as RuleTargetKind,
        pattern: rule.pattern || "",
        applies_to: rule.applies_to || "",
        minutes,
        note: rule.note || undefined,
      })
    );
    if (!created) return;
    await run(() => api.removePermissionRule(rule.id as string), "Rule changed");
    await Promise.all([loadRules().catch(() => {}), loadEvents().catch(() => {})]);
  };

  const removeRule = async (rule: PermissionRule) => {
    if (!rule.id) return;
    if (!window.confirm("Remove this rule? AURA goes back to the mode's default for it.")) return;
    await run(() => api.removePermissionRule(rule.id as string), "Rule removed");
    await Promise.all([loadRules().catch(() => {}), loadEvents().catch(() => {})]);
  };

  const loadOlder = async () => {
    const oldest = events.reduce((m, e) => Math.min(m, e.seq), Infinity);
    if (!Number.isFinite(oldest)) return;
    setLoadingOlder(true);
    const batch = await run(() => api.permissionEvents(EVENT_BATCH, oldest));
    setLoadingOlder(false);
    if (!batch) return;
    setEvents((prev) => [...prev, ...batch]);
    setLastBatch(batch.length);
    setShown((n) => n + EVENT_PAGE);
  };

  const userRules = rules.filter((r) => r.source !== "install");
  const installRules = rules.filter((r) => r.source === "install");
  const changes = useMemo(() => events.filter((e) => CHANGE_KIND.test(e.kind)), [events]);

  if (loading) {
    return (
      <div className="space-y-4 p-4 sm:p-8">
        <Skeleton className="h-24" />
        <Skeleton className="h-48" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="space-y-5 p-4 sm:p-8">
      <PageHeader
        title="Permissions"
        blurb="You decide how much AURA does without asking. AURA can never change these settings itself."
      />

      <Card>
        <SectionTitle>Permission mode</SectionTitle>
        <ModePicker variant="detailed" onChange={onMode} />
        {perm && perm.mode_label && (
          <p className="mt-3 text-[13px] text-muted">
            Right now: <span className="font-medium text-ink">{perm.mode_label}</span>
          </p>
        )}
      </Card>

      <Card>
        <SectionTitle>Your limits for Auto</SectionTitle>
        <label htmlFor="boundaries" className="mb-1.5 block text-[13px] leading-relaxed text-muted">
          Anything you write here is shown to the safety check that reviews each action in
          Auto. For example: never email anyone outside the company.
        </label>
        <textarea
          id="boundaries"
          className="input min-h-[96px]"
          maxLength={2000}
          value={boundaries}
          onChange={(e) => setBoundaries(e.target.value)}
          placeholder="Never email anyone outside the company."
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <span className="text-[12.5px] text-muted">{boundaries.length}/2000</span>
          <button
            type="button"
            onClick={() => void saveBoundaries()}
            disabled={savingBoundaries || boundaries.trim() === (perm?.boundaries ?? "").trim()}
            className="btn-ghost min-h-[44px]"
          >
            {savingBoundaries ? <Spinner /> : <Check size={14} aria-hidden />} Save limits
          </button>
        </div>
      </Card>

      <Card>
        <SectionTitle>Always needs you, in every mode</SectionTitle>
        <ul className="space-y-2">
          {ALWAYS_NEEDS_YOU.map((line) => (
            <li key={line} className="flex items-start gap-2.5 text-[13.5px] leading-relaxed">
              <Lock size={13} className="mt-1 shrink-0 text-accent-soft" aria-hidden />
              {line}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-[13px] leading-relaxed text-muted">
          AURA&apos;s own files and settings, passwords and start-up items, and blocked apps are
          off limits whatever you choose.
        </p>
      </Card>

      <Card>
        <SectionTitle
          action={
            !adding ? (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="btn-ghost min-h-[44px]"
              >
                <Plus size={14} aria-hidden /> Add a rule
              </button>
            ) : undefined
          }
        >
          Rules
        </SectionTitle>
        <p className="mb-4 text-[13px] leading-relaxed text-muted">
          Block wins over Ask, and Ask wins over Always allow. Block and Ask hold in every
          mode.
        </p>

        {adding && (
          <AddRuleForm
            catalogue={catalogue}
            systemName={systemName}
            onCancel={() => setAdding(false)}
            onAdded={async () => {
              setAdding(false);
              await Promise.all([loadRules().catch(() => {}), loadEvents().catch(() => {})]);
            }}
          />
        )}

        {userRules.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-[13.5px] text-muted">
            No rules yet. When you answer an approval with &ldquo;Always allow&rdquo;, the rule
            appears here.
          </p>
        ) : (
          <RulesList
            rules={userRules}
            systemName={systemName}
            onEffect={changeEffect}
            onRemove={removeRule}
          />
        )}

        {installRules.length > 0 && (
          <div className="mt-5 border-t border-line pt-4">
            <h3 className="mb-1 text-[13.5px] font-medium">Set by this install</h3>
            <p className="mb-3 text-[13px] leading-relaxed text-muted">
              These come from how AURA was installed. You can&apos;t change them here.
            </p>
            <ul className="space-y-2">
              {installRules.map((r, i) => (
                <li
                  key={r.id ?? `install-${i}`}
                  className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-raised/40 px-3.5 py-2.5 text-[13px]"
                >
                  <Lock size={12} className="shrink-0 text-muted" aria-hidden />
                  <span className="font-medium">{systemName(r.system)}</span>
                  <span>{r.tool ? humanTool(r.tool) : "Every action"}</span>
                  <span className="text-muted">{describeTarget(r.pattern)}</span>
                  <Badge tone={r.effect === "block" ? "urgent" : r.effect === "ask" ? "warning" : "success"}>
                    {EFFECTS.find((e) => e.key === r.effect)?.label ?? r.effect}
                  </Badge>
                  {r.note && <span className="w-full text-muted">{r.note}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle
          action={
            <button
              type="button"
              onClick={() => void loadEvents().catch(() => {})}
              className="btn-quiet min-h-[44px]"
            >
              <RefreshCw size={13} aria-hidden /> Refresh
            </button>
          }
        >
          Recent permission changes
        </SectionTitle>
        <p className="mb-3 text-[13px] leading-relaxed text-muted">
          Every change is recorded here and you are notified, so a change you did not make
          stands out.
        </p>

        <div role="status" className="mb-4">
          {verify?.ok && (
            <p className="flex items-start gap-2 text-[13px] text-sage">
              <ShieldCheck size={14} className="mt-0.5 shrink-0" aria-hidden />
              Record checked: {verify.checked} {verify.checked === 1 ? "entry" : "entries"}, none
              altered.
            </p>
          )}
          {verify && !verify.ok && (
            <p className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-[13px] leading-relaxed text-rose">
              This record does not match at entry {verify.first_bad_seq ?? "unknown"}. Something
              may have changed it outside AURA. Treat recent changes with care.
            </p>
          )}
          {verifyFailed && (
            <p className="text-[13px] text-muted">Couldn&apos;t check the record just now.</p>
          )}
        </div>

        {changes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line px-4 py-6 text-center text-[13.5px] text-muted">
            No changes yet.
          </p>
        ) : (
          <ol className="divide-y divide-line">
            {changes.slice(0, shown).map((e) => (
              <li key={e.seq} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5">
                <span className="min-w-0 break-words text-[13.5px]">{eventLine(e, systemName)}</span>
                <time
                  dateTime={toDate(e.created_at).toISOString()}
                  className="shrink-0 text-[12.5px] text-muted"
                >
                  {toDate(e.created_at).toLocaleString([], {
                    day: "numeric",
                    month: "short",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </time>
              </li>
            ))}
          </ol>
        )}

        <div className="mt-3 flex flex-wrap gap-2">
          {changes.length > shown && (
            <button
              type="button"
              className="btn-ghost min-h-[44px]"
              onClick={() => setShown((n) => n + EVENT_PAGE)}
            >
              Show more
            </button>
          )}
          {changes.length <= shown && lastBatch >= EVENT_BATCH && (
            <button
              type="button"
              className="btn-ghost min-h-[44px]"
              onClick={() => void loadOlder()}
              disabled={loadingOlder}
            >
              {loadingOlder ? <Spinner /> : null} Load older entries
            </button>
          )}
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------

function modeLabel(value?: string) {
  return MODES.find((m) => m.key === value)?.label;
}

function eventLine(e: PermissionEvent, systemName: (id: string) => string): string {
  const who =
    e.actor === "user" ? "You" : e.actor === "aura" ? "AURA" : e.actor === "migration" ? "AURA" : "AURA (automatic)";
  const tool = e.tool ? `"${humanTool(e.tool)}"` : "";
  const system = typeof e.detail?.system === "string" ? ` in ${systemName(e.detail.system)}` : "";
  const target = e.target ? ` (${e.target})` : "";
  const effect = e.detail?.effect as string | undefined;

  switch (e.kind) {
    case "mode_changed": {
      const label = modeLabel(String(e.detail?.mode ?? e.target ?? ""));
      return label ? `${who} set the mode to ${label}` : `${who} changed the permission mode`;
    }
    case "rule_added": {
      const verb = effect === "block" ? "blocked" : effect === "ask" ? "set to ask for" : effect === "allow" ? "allowed" : "added a rule for";
      return `${who} ${verb} ${tool || "an action"}${system}${target}`;
    }
    case "rule_revoked":
      return `${who} removed the rule for ${tool || "an action"}${system}${target}`;
    case "system_changed":
      return `${who} changed which systems are assigned${e.target ? `: ${e.target}` : ""}`;
    case "schedule_mode_changed":
      return `${who} changed the approval setting of a schedule or automation`;
    case "boundaries_changed":
      return `${who} updated the limits for Auto`;
    case "self_change_blocked":
      return "A change to permissions from something running on this computer was refused";
    case "legacy_mapped":
      return `Carried over a setting from before the update${e.target ? `: ${e.target}` : ""}`;
    default: {
      // folder_*, app_*, block_*: "folder added" etc.
      const [area, ...rest] = e.kind.split("_");
      const areaWords = area === "block" ? "blocked app list" : area;
      const verb = rest.join(" ") || "changed";
      return `${who} ${verb} (${areaWords})${e.target ? `: ${e.target}` : ""}`;
    }
  }
}

function EffectControl({
  value,
  onChange,
  label,
}: {
  value: RuleEffect;
  onChange: (next: RuleEffect) => void;
  label: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex flex-wrap items-center gap-0.5 rounded-full border border-line bg-raised/40 p-0.5"
    >
      {EFFECTS.map((e) => {
        const selected = value === e.key;
        const tone =
          e.key === "block"
            ? "bg-rose/15 text-rose"
            : e.key === "ask"
              ? "bg-amber/15 text-amber"
              : "bg-sage/15 text-sage";
        return (
          <button
            key={e.key}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(e.key)}
            className={`min-h-[44px] rounded-full px-3 text-[12.5px] font-medium transition ${
              selected ? tone : "text-muted hover:text-ink"
            }`}
          >
            {e.label}
          </button>
        );
      })}
    </div>
  );
}

function RulesList({
  rules,
  systemName,
  onEffect,
  onRemove,
}: {
  rules: PermissionRule[];
  systemName: (id: string) => string;
  onEffect: (rule: PermissionRule, effect: RuleEffect) => void;
  onRemove: (rule: PermissionRule) => void;
}) {
  const sourceNote = (r: PermissionRule) =>
    r.source === "card"
      ? "From an approval"
      : r.source === "legacy"
        ? "Carried over from your old settings"
        : "";
  const scopeNote = (r: PermissionRule) =>
    r.applies_to?.startsWith("schedule:")
      ? "Only in one schedule"
      : r.applies_to?.startsWith("automation:")
        ? "Only in one automation"
        : "";
  const until = (r: PermissionRule) =>
    r.expires_at
      ? `Until ${toDate(r.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
      : "";

  const cell = (r: PermissionRule) => (
    <>
      <div>{describeTarget(r.pattern)}</div>
      {r.target_kind ? <div className="text-[12.5px] text-muted">{targetKindLabel(r.target_kind)}</div> : null}
      {[scopeNote(r), until(r), sourceNote(r)].filter(Boolean).map((n) => (
        <div key={n} className="text-[12.5px] text-muted">
          {n}
        </div>
      ))}
    </>
  );

  return (
    <>
      {/* Wide screens: a real table */}
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full text-left text-[13.5px]">
          <caption className="sr-only">Permission rules</caption>
          <thead>
            <tr className="border-b border-line text-[12.5px] text-muted">
              <th scope="col" className="py-2 pr-3 font-medium">System</th>
              <th scope="col" className="py-2 pr-3 font-medium">Action</th>
              <th scope="col" className="py-2 pr-3 font-medium">Only for</th>
              <th scope="col" className="py-2 pr-3 font-medium">Rule</th>
              <th scope="col" className="py-2 font-medium"><span className="sr-only">Remove</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rules.map((r, i) => (
              <tr key={r.id ?? i} className="align-top">
                <td className="py-2.5 pr-3">{systemName(r.system)}</td>
                <td className="py-2.5 pr-3">{r.tool ? humanTool(r.tool) : "Every action"}</td>
                <td className="max-w-[16rem] break-words py-2.5 pr-3">{cell(r)}</td>
                <td className="py-1.5 pr-3">
                  <EffectControl
                    value={r.effect}
                    onChange={(e) => onEffect(r, e)}
                    label={`Rule for ${r.tool ? humanTool(r.tool) : "every action"} in ${systemName(r.system)}`}
                  />
                </td>
                <td className="py-1.5">
                  <button
                    type="button"
                    onClick={() => onRemove(r)}
                    className="btn-quiet min-h-[44px] min-w-[44px] px-3"
                    aria-label={`Remove rule for ${r.tool ? humanTool(r.tool) : "every action"} in ${systemName(r.system)}`}
                  >
                    <Trash2 size={14} aria-hidden />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phones: one card per rule */}
      <ul className="space-y-3 md:hidden">
        {rules.map((r, i) => (
          <li key={r.id ?? i} className="space-y-2.5 rounded-xl border border-line bg-raised/40 p-3.5 text-[13.5px]">
            <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5">
              <dt className="text-muted">System</dt>
              <dd>{systemName(r.system)}</dd>
              <dt className="text-muted">Action</dt>
              <dd>{r.tool ? humanTool(r.tool) : "Every action"}</dd>
              <dt className="text-muted">Only for</dt>
              <dd className="min-w-0 break-words">{cell(r)}</dd>
            </dl>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <EffectControl
                value={r.effect}
                onChange={(e) => onEffect(r, e)}
                label={`Rule for ${r.tool ? humanTool(r.tool) : "every action"} in ${systemName(r.system)}`}
              />
              <button
                type="button"
                onClick={() => onRemove(r)}
                className="btn-quiet min-h-[44px]"
                aria-label={`Remove rule for ${r.tool ? humanTool(r.tool) : "every action"} in ${systemName(r.system)}`}
              >
                <Trash2 size={14} aria-hidden /> Remove
              </button>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function AddRuleForm({
  catalogue,
  systemName,
  onCancel,
  onAdded,
}: {
  catalogue: CatalogueEntry[];
  systemName: (id: string) => string;
  onCancel: () => void;
  onAdded: () => Promise<void>;
}) {
  const run = useAction();
  const uid = useId();
  const [effect, setEffect] = useState<RuleEffect>("ask");
  const [system, setSystem] = useState("");
  const [tool, setTool] = useState("");
  const [kind, setKind] = useState<RuleTargetKind>("");
  const [pattern, setPattern] = useState("");
  const [duration, setDuration] = useState(0);
  const [saving, setSaving] = useState(false);

  const systems = useMemo(
    () => Array.from(new Set(catalogue.map((c) => c.system))).sort((a, b) => systemName(a).localeCompare(systemName(b))),
    [catalogue, systemName]
  );
  const tools = useMemo(
    () => catalogue.filter((c) => c.system === system).sort((a, b) => a.tool.localeCompare(b.tool)),
    [catalogue, system]
  );
  const entry = tools.find((t) => t.tool === tool);
  const kinds = entry ? targetsOf(entry) : [];

  const canSave = system !== "" && !saving && (kind === "" || pattern.trim() !== "");

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    const created = await run(
      () =>
        api.addPermissionRule({
          effect,
          system,
          tool: tool || undefined,
          target_kind: kind,
          pattern: kind ? pattern.trim() : "",
          minutes: DURATIONS[duration].minutes,
        }),
      "Rule added"
    );
    setSaving(false);
    if (created) await onAdded();
  };

  return (
    <form
      className="mb-5 space-y-3.5 rounded-2xl border border-accent/25 bg-raised/30 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <div className="grid gap-3.5 sm:grid-cols-2">
        <div>
          <label htmlFor={`${uid}-system`} className="label mb-1.5 block">
            System
          </label>
          <select
            id={`${uid}-system`}
            className="input min-h-[44px]"
            value={system}
            onChange={(e) => {
              setSystem(e.target.value);
              setTool("");
              setKind("");
              setPattern("");
            }}
          >
            <option value="">Choose a system</option>
            <option value="*">Every system</option>
            {systems.map((s) => (
              <option key={s} value={s}>
                {systemName(s)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-tool`} className="label mb-1.5 block">
            Action
          </label>
          <select
            id={`${uid}-tool`}
            className="input min-h-[44px]"
            value={tool}
            disabled={!system || system === "*"}
            onChange={(e) => {
              setTool(e.target.value);
              setKind("");
              setPattern("");
            }}
          >
            <option value="">Every action</option>
            {tools.map((t) => (
              <option key={t.tool} value={t.tool}>
                {humanTool(t.tool)}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid gap-3.5 sm:grid-cols-2">
        <div>
          <label htmlFor={`${uid}-kind`} className="label mb-1.5 block">
            Only for
          </label>
          <select
            id={`${uid}-kind`}
            className="input min-h-[44px]"
            value={kind}
            disabled={kinds.length === 0}
            onChange={(e) => {
              setKind(e.target.value as RuleTargetKind);
              setPattern("");
            }}
          >
            <option value="">Anything</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {targetKindLabel(k)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${uid}-pattern`} className="label mb-1.5 block">
            {kind ? targetKindLabel(kind) : "Detail"}
          </label>
          <input
            id={`${uid}-pattern`}
            className="input min-h-[44px]"
            value={pattern}
            disabled={!kind}
            placeholder={kind ? TARGET_PLACEHOLDER[kind] : "Choose what to limit it to first"}
            onChange={(e) => setPattern(e.target.value)}
          />
        </div>
      </div>

      <div className="grid gap-3.5 sm:grid-cols-2">
        <fieldset>
          <legend className="label mb-1.5">Rule</legend>
          <EffectControl value={effect} onChange={setEffect} label="Rule effect" />
        </fieldset>
        <div>
          <label htmlFor={`${uid}-duration`} className="label mb-1.5 block">
            How long
          </label>
          <select
            id={`${uid}-duration`}
            className="input min-h-[44px]"
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          >
            {DURATIONS.map((d, i) => (
              <option key={d.label} value={i}>
                {d.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap justify-end gap-2">
        <button type="button" className="btn-quiet min-h-[44px]" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={!canSave} className="btn-primary min-h-[44px]">
          {saving ? <Spinner /> : <Plus size={14} aria-hidden />} Add rule
        </button>
      </div>
    </form>
  );
}
