"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  Download,
  Gauge,
  KeyRound,
  Link2,
  Moon,
  Plus,
  Radio,
  RefreshCw,
  ShieldCheck,
  Sun,
  Trash2,
  Unlink,
  X,
} from "lucide-react";
import {
  AppSettings,
  AutonomyTier,
  Colourway,
  Compaction,
  Grant,
  Integration,
  Secret,
  UsageReport,
  api,
  clearToken,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";
import Mascot from "@/components/Mascot";
import { useAction, useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  Progress,
  SectionTitle,
  Skeleton,
  Spinner,
  Toggle,
  fmtRelative,
} from "@/components/ui";

// What the backend sends back on ?google=… after the OAuth round trip.
const GOOGLE_RESULTS: Record<string, { tone: "ok" | "warn" | "error"; text: string }> = {
  connected: { tone: "ok", text: "Google connected. Your mail and calendar are syncing." },
  partial: {
    tone: "warn",
    text:
      "Google connected, but some permissions were declined. Reconnect and leave every " +
      "box ticked, or the skills that need them will fail.",
  },
};

const GOOGLE_ERRORS: Record<string, string> = {
  denied: "You cancelled the Google sign-in. Nothing changed.",
  bad_state:
    "That sign-in link expired or was already used. Start again — links are valid for 10 minutes.",
  already_linked:
    "That Google account is already connected to a different AURA account. Disconnect it there first.",
  session_expired: "Your session expired mid-connect. Sign in again, then retry.",
  exchange_failed:
    "Google accepted the sign-in but the token exchange failed. Check GOOGLE_CLIENT_SECRET and the redirect URI.",
  no_email: "Google didn't return an email address. Check the requested scopes.",
  no_code: "Google didn't send an authorisation code. Try again.",
};

const COLOURWAYS: Colourway[] = ["teal", "amber", "rose", "violet", "sage"];

const PERSONALITIES = [
  { key: "concise", label: "Concise" },
  { key: "warm", label: "Warm" },
  { key: "dry", label: "Dry" },
  { key: "formal", label: "Formal" },
  { key: "encouraging", label: "Encouraging" },
];

const TIERS: Array<{ key: AutonomyTier; title: string; body: string }> = [
  {
    key: "strict",
    title: "Strict",
    body: "Asks before anything that changes state — including creating a task.",
  },
  {
    key: "conservative",
    title: "Conservative",
    body: "Handles its own data alone. Asks before anything the outside world sees.",
  },
  {
    key: "relaxed",
    title: "Relaxed",
    body: "Books meetings and acts freely. Only stops for irreversible actions.",
  },
  {
    key: "full",
    title: "Full access",
    body: "Complete autonomy, including sending email. Nothing is held for approval.",
  },
];

export default function SettingsPage() {
  const { user } = useAuth();
  const { assistant, refresh: refreshAssistant } = useAssistant();

  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [provider, setProvider] = useState("");
  const [compaction, setCompaction] = useState<Compaction | null>(null);
  const [compacting, setCompacting] = useState(false);
  const [saved, setSaved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [theme, setTheme] = useState<"warm" | "dark">("dark");
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [secretKey, setSecretKey] = useState("");
  const [secretValue, setSecretValue] = useState("");
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [googleBusy, setGoogleBusy] = useState<
    "connect" | "sync" | "disconnect" | null
  >(null);
  const [googleNotice, setGoogleNotice] = useState<{
    tone: "ok" | "warn" | "error";
    text: string;
  } | null>(null);

  const run = useAction();
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, i, c, g, v, u] = await Promise.all([
        api.settings(),
        api.integrations(),
        api.authConfig(),
        api.grants().catch(() => []),
        api.secrets().catch(() => []),
        api.usage().catch(() => null),
      ]);
      setSettings(s);
      setIntegrations(i);
      setProvider(c.llm_provider);
      setGrants(g);
      setSecrets(v);
      setUsage(u);
    } finally {
      setLoading(false);
    }
  }, []);

  const saveSecret = async () => {
    if (!secretKey.trim() || !secretValue.trim()) return;
    await api.putSecret({ key: secretKey.trim(), value: secretValue });
    setSecretKey("");
    setSecretValue("");
    await load();
    flash();
  };

  // --- Google -------------------------------------------------------------

  const connectGoogle = async () => {
    setGoogleBusy("connect");
    try {
      // googleStart carries the bearer token, which is what makes the backend
      // attach Google to *this* account rather than sign in as the Google identity.
      const { authorization_url } = await api.googleStart();
      window.location.href = authorization_url;
    } catch (err) {
      setGoogleBusy(null);
      setGoogleNotice({
        tone: "error",
        text:
          err instanceof Error
            ? err.message
            : "Couldn't start the Google sign-in.",
      });
    }
  };

  const syncGoogle = async () => {
    setGoogleBusy("sync");
    const result = await run(() => api.syncGoogle());
    if (result) {
      setGoogleNotice({
        tone: result.errors.length ? "warn" : "ok",
        text: result.message,
      });
    }
    setGoogleBusy(null);
    await load();
  };

  const disconnectGoogle = async () => {
    if (
      !window.confirm(
        "Disconnect Google? Synced mail and events stay in AURA, but nothing new " +
          "will arrive and it won't be able to send. Access is revoked at Google too."
      )
    ) {
      return;
    }
    setGoogleBusy("disconnect");
    const result = await run(() => api.disconnectGoogle(true));
    if (result) setGoogleNotice({ tone: "ok", text: result.message });
    setGoogleBusy(null);
    await load();
  };

  useEffect(() => {
    void load();
    const stored = (window.localStorage.getItem("aura_theme") as "warm" | "dark") || "dark";
    setTheme(stored);

    // The OAuth callback redirects back here with ?google=… . Read it once,
    // then strip it so a refresh doesn't replay a stale message.
    const params = new URLSearchParams(window.location.search);
    const outcome = params.get("google");
    if (outcome) {
      if (outcome === "error") {
        const reason = params.get("reason") || "";
        setGoogleNotice({
          tone: reason === "denied" ? "warn" : "error",
          text:
            GOOGLE_ERRORS[reason] ??
            `Google sign-in failed${reason ? ` (${reason})` : ""}. Check the backend logs.`,
        });
      } else if (GOOGLE_RESULTS[outcome]) {
        setGoogleNotice(GOOGLE_RESULTS[outcome]);
      }
      window.history.replaceState({}, "", "/settings");
    }
  }, [load]);

  useEffect(() => {
    if (assistant) setName(assistant.name);
  }, [assistant]);

  const flash = () => {
    setSaved(true);
    setTimeout(() => setSaved(false), 1600);
  };

  const update = async (patch: Partial<AppSettings>) => {
    setSettings(await api.updateSettings(patch));
    flash();
  };

  const updateAssistant = async (patch: Record<string, unknown>) => {
    await api.updateAssistant(patch);
    await refreshAssistant();
    flash();
  };

  const applyTheme = (next: "warm" | "dark") => {
    setTheme(next);
    window.localStorage.setItem("aura_theme", next);
    // Dark aurora is the root palette, so only `warm` needs a class.
    document.documentElement.classList.toggle("warm", next === "warm");
    document.documentElement.classList.toggle("dark", next !== "warm");
    void update({ theme: next });
  };

  const compact = async () => {
    setCompacting(true);
    try {
      setCompaction(await api.compactMemory(false));
    } finally {
      setCompacting(false);
    }
  };

  if (loading || !settings) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 p-8">
        <Skeleton className="h-36" />
        <Skeleton className="h-52" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-8">
      <div className="flex items-center justify-between">
        <h1 className="display text-shine text-[26px]">Settings</h1>
        {saved && (
          <span className="flex items-center gap-1.5 text-[12px] text-sage">
            <Check size={13} /> Saved
          </span>
        )}
      </div>

      {/* Assistant identity */}
      <Card id="assistant">
        <SectionTitle>Your assistant</SectionTitle>
        {assistant && (
          <>
            <div className="mb-5 flex items-center gap-4">
              <Mascot colourway={assistant.avatar} stage={assistant.stage} size={58} />
              <div className="min-w-0 flex-1">
                <input
                  className="input mb-2 text-[15px] font-medium"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onBlur={() => name.trim() && name !== assistant.name && updateAssistant({ name: name.trim() })}
                />
                <div className="text-[12px] text-faint">
                  {assistant.stage_label} · {assistant.signals.interactions} conversations ·{" "}
                  {assistant.signals.memories} things known · day {assistant.days_together + 1}
                </div>
              </div>
            </div>

            <div className="mb-4">
              <div className="label mb-2">Look</div>
              <div className="flex gap-2.5">
                {COLOURWAYS.map((c) => (
                  <button
                    key={c}
                    onClick={() => updateAssistant({ avatar: c })}
                    className={`rounded-xl p-1 transition ${
                      assistant.avatar === c
                        ? "ring-2 ring-accent ring-offset-2 ring-offset-panel"
                        : ""
                    }`}
                  >
                    <Mascot colourway={c} stage={assistant.stage} size={34} />
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="label mb-2">Voice</div>
              <div className="flex flex-wrap gap-2">
                {PERSONALITIES.map((p) => (
                  <button
                    key={p.key}
                    onClick={() => updateAssistant({ personality: p.key })}
                    className={`rounded-full border px-3 py-1.5 text-[12.5px] transition ${
                      assistant.personality === p.key
                        ? "border-accent/60 bg-accent-dim text-accent-soft shadow-glow-sm"
                        : "border-line text-muted hover:text-ink"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </Card>

      {/* Autonomy */}
      <Card>
        <SectionTitle>How much freedom</SectionTitle>
        <p className="mb-3.5 text-[12.5px] leading-relaxed text-muted">
          You can change this any time, and grant one-off exceptions when it asks.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          {TIERS.map((t) => (
            <button
              key={t.key}
              onClick={() => update({ autonomy_level: t.key })}
              className={`rounded-2xl border p-4 text-left transition ${
                settings.autonomy_level === t.key
                  ? "border-accent/50 bg-accent-dim"
                  : "border-line hover:bg-raised"
              }`}
            >
              <div className="mb-1 text-[13.5px] font-medium">{t.title}</div>
              <div className="text-[12px] leading-relaxed text-muted">{t.body}</div>
            </button>
          ))}
        </div>

        {grants.length > 0 && (
          <div className="mt-4 border-t border-line pt-4">
            <div className="label mb-2 flex items-center gap-1.5">
              <ShieldCheck size={11} /> Standing permissions
            </div>
            <div className="space-y-1.5">
              {grants.map((g) => (
                <div
                  key={g.id}
                  className="flex items-center justify-between rounded-xl border border-line px-3 py-2 text-[12.5px]"
                >
                  <span className="font-mono text-[11.5px]">{g.tool_name}</span>
                  <div className="flex items-center gap-2">
                    <Badge tone={g.scope === "always" ? "warning" : "normal"}>
                      {g.scope === "always" ? "always" : "temporary"}
                    </Badge>
                    <button
                      onClick={async () => {
                        await api.revokeGrant(g.tool_name);
                        await load();
                      }}
                      className="text-[11.5px] text-faint hover:text-ink"
                    >
                      revoke
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* Heartbeat */}
      <Card>
        <SectionTitle>
          <span className="flex items-center gap-2">
            <Radio size={12} className="text-accent-soft" /> Working in the background
          </span>
        </SectionTitle>
        <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
          On a timer, your assistant triages new mail, captures commitments, prepares your
          next meeting and flags what&apos;s slipping. It only ever runs skills you&apos;ve
          enabled, and never anything irreversible.
        </p>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px]">Enabled</span>
            <Toggle
              checked={settings.heartbeat_enabled}
              onChange={(v) => update({ heartbeat_enabled: v })}
              label="Heartbeat"
            />
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="text-[13px]">Check every</span>
            <select
              className="input w-[140px] py-1.5"
              value={settings.heartbeat_interval_minutes}
              onChange={(e) =>
                update({ heartbeat_interval_minutes: Number(e.target.value) })
              }
            >
              {[15, 30, 60, 120, 240].map((m) => (
                <option key={m} value={m}>
                  {m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? "s" : ""}`}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-between gap-4">
            <span className="text-[13px]">Quiet hours</span>
            <input
              className="input w-[140px] py-1.5 text-center"
              value={settings.quiet_hours}
              onChange={(e) => setSettings({ ...settings, quiet_hours: e.target.value })}
              onBlur={() => update({ quiet_hours: settings.quiet_hours })}
              placeholder="22:00-07:00"
            />
          </div>
        </div>
      </Card>

      {/* Briefing + appearance */}
      <Card>
        <SectionTitle>Daily briefing</SectionTitle>
        <div className="flex flex-wrap items-center gap-5">
          <div className="flex items-center gap-2.5 text-[13px]">
            <Toggle
              checked={settings.briefing_enabled}
              onChange={(v) => update({ briefing_enabled: v })}
              label="Briefing"
            />
            Enabled
          </div>
          <label className="flex items-center gap-2 text-[13px]">
            Time
            <input
              type="time"
              value={settings.briefing_time}
              onChange={(e) => update({ briefing_time: e.target.value })}
              className="input w-[124px] py-1.5"
            />
          </label>
        </div>
      </Card>

      <Card>
        <SectionTitle>Appearance</SectionTitle>
        <div className="flex gap-2">
          {(
            [
              { key: "dark", label: "Aurora", icon: Moon, hint: "Deep dark, violet glow" },
              { key: "warm", label: "Daylight", icon: Sun, hint: "Warm parchment" },
            ] as const
          ).map((t) => (
            <button
              key={t.key}
              onClick={() => applyTheme(t.key)}
              className={`flex-1 rounded-2xl border p-4 text-left transition ${
                theme === t.key
                  ? "border-accent/50 bg-accent-dim shadow-glow-sm"
                  : "border-line bg-raised/30 hover:border-accent/25 hover:bg-raised/60"
              }`}
            >
              <t.icon size={16} className="mb-2 text-accent-soft" />
              <div className="text-[13.5px] font-medium">{t.label}</div>
              <div className="mt-0.5 text-[11.5px] text-faint">{t.hint}</div>
            </button>
          ))}
        </div>
      </Card>

      {/* Memory maintenance */}
      <Card>
        <SectionTitle>Memory maintenance</SectionTitle>
        <p className="mb-3.5 text-[12.5px] leading-relaxed text-muted">
          Merges near-duplicates, promotes what gets used, and lets unused entries fade.
          Runs daily on its own — this is the manual trigger.
        </p>
        <div className="flex items-center gap-3">
          <button onClick={compact} disabled={compacting} className="btn-ghost">
            {compacting ? <Spinner /> : null} Compact now
          </button>
          {compaction && (
            <span className="text-[12.5px] text-muted">
              {compaction.before} → {compaction.after} · {compaction.merged} merged ·{" "}
              {compaction.promoted} promoted · {compaction.dropped} faded
            </span>
          )}
        </div>
      </Card>

      {/* Spend */}
      {usage && (
        <Card>
          <SectionTitle
            action={
              <span className="text-[11.5px] text-faint">
                {usage.today.provider} · {usage.today.model}
              </span>
            }
          >
            <span className="flex items-center gap-2">
              <Gauge size={12} className="text-accent-soft" /> Today&apos;s spend
            </span>
          </SectionTitle>

          <div className="mb-2 flex items-end justify-between">
            <div className="display text-shine text-[28px]">
              ${usage.today.spent_usd.toFixed(3)}
            </div>
            <div className="text-[12px] text-muted">
              of ${usage.today.cap_usd.toFixed(2)} cap
            </div>
          </div>
          <Progress
            value={usage.today.percent}
            tone={usage.today.percent > 80 ? "accent" : "sage"}
          />
          <div className="mt-2 text-[11.5px] text-faint">
            {usage.today.calls} model call{usage.today.calls === 1 ? "" : "s"} ·{" "}
            {usage.today.tokens.toLocaleString()} tokens
            {!usage.today.cap_enabled && " · cap disabled"}
          </div>

          {usage.by_trigger.length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <div className="label mb-2">Last 7 days by source</div>
              <div className="space-y-1">
                {usage.by_trigger.map((t) => (
                  <div
                    key={t.trigger}
                    className="flex items-center justify-between text-[12.5px]"
                  >
                    <span className="text-muted">{t.trigger}</span>
                    <span className="tabular-nums">
                      ${t.cost_usd.toFixed(3)}{" "}
                      <span className="text-faint">({t.calls})</span>
                    </span>
                  </div>
                ))}
              </div>
              <p className="mt-3 text-[11.5px] leading-relaxed text-faint">
                Background work — heartbeat and schedules — is capped at 40% of the
                daily allowance so it can never eat the budget you notice. Costs are
                estimates for the cap, not billing.
              </p>
            </div>
          )}
        </Card>
      )}

      {/* Vault */}
      <Card>
        <SectionTitle>
          <span className="flex items-center gap-2">
            <KeyRound size={12} className="text-accent-soft" /> Credential vault
          </span>
        </SectionTitle>
        <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
          Secrets your assistant can <em>use</em> but never <em>see</em>. It refers to them
          by name — <code className="rounded bg-raised px-1 font-mono text-[11px]">
            {"{{vault:stripe_key}}"}
          </code>{" "}
          — and the real value is substituted in Python at send time, after the model has
          finished deciding. Values are never returned by the API, never enter a prompt,
          and never appear in a log.
        </p>

        <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
          <input
            className="input"
            placeholder="name (e.g. stripe_key)"
            value={secretKey}
            onChange={(e) => setSecretKey(e.target.value)}
          />
          <input
            className="input"
            type="password"
            placeholder="value"
            value={secretValue}
            onChange={(e) => setSecretValue(e.target.value)}
          />
          <button
            onClick={saveSecret}
            disabled={!secretKey.trim() || !secretValue.trim()}
            className="btn-primary"
          >
            <Plus size={13} /> Store
          </button>
        </div>

        {secrets.length === 0 ? (
          <p className="text-[12.5px] text-faint">Nothing stored.</p>
        ) : (
          <div className="space-y-1.5">
            {secrets.map((s) => (
              <div
                key={s.key}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line px-3 py-2"
              >
                <div className="min-w-0">
                  <code className="font-mono text-[12px]">{s.key}</code>
                  <span className="ml-2 font-mono text-[11.5px] text-faint">{s.hint}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Badge>{s.kind}</Badge>
                  <span className="text-[11px] text-faint">used {s.use_count}×</span>
                  <button
                    onClick={async () => {
                      await api.deleteSecret(s.key);
                      await load();
                    }}
                    className="text-[11.5px] text-faint hover:text-rose"
                  >
                    delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Account + integrations */}
      <Card>
        <SectionTitle>Account</SectionTitle>
        <div className="space-y-1.5 text-[13px]">
          <div className="flex justify-between">
            <span className="text-muted">Signed in as</span>
            <span>{user?.email}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted">Model provider</span>
            <Badge tone={provider === "mock" ? "warning" : "success"}>{provider}</Badge>
          </div>
        </div>
        {provider === "mock" && (
          <div className="mt-3 rounded-xl border border-amber/35 bg-amber/10 px-3.5 py-3 text-[12px] leading-relaxed text-amber">
            <p className="mb-2 font-medium">
              Running offline — replies come from a keyword router, not a model.
            </p>
            <ol className="ml-4 list-decimal space-y-1">
              <li>
                Create a key at{" "}
                <a
                  href="https://console.anthropic.com"
                  target="_blank"
                  rel="noreferrer"
                  className="underline underline-offset-2"
                >
                  console.anthropic.com
                </a>{" "}
                → API Keys (billing must be enabled)
              </li>
              <li>
                Put it in <code className="font-mono">.env</code> in the project root:{" "}
                <code className="font-mono">ANTHROPIC_API_KEY=sk-ant-…</code>
              </li>
              <li>Restart the backend</li>
            </ol>
            <p className="mt-2 opacity-90">
              <code className="font-mono">OPENAI_API_KEY</code> is optional and used only
              for embeddings — it improves memory and document search, but Claude does the
              reasoning either way.
            </p>
          </div>
        )}
      </Card>

      <Card>
        <SectionTitle>Connections</SectionTitle>

        {googleNotice && (
          <div
            className={`mb-3 flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-[12.5px] leading-relaxed ${
              googleNotice.tone === "error"
                ? "border-rose/35 bg-rose/10 text-rose"
                : googleNotice.tone === "warn"
                  ? "border-amber/35 bg-amber/10 text-amber"
                  : "border-sage/35 bg-sage/10 text-sage"
            }`}
          >
            {googleNotice.tone === "ok" ? (
              <Check size={14} className="mt-0.5 shrink-0" />
            ) : (
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            )}
            <span className="min-w-0 flex-1">{googleNotice.text}</span>
            <button
              onClick={() => setGoogleNotice(null)}
              className="shrink-0 opacity-60 hover:opacity-100"
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        )}

        <div className="space-y-2">
          {integrations.map((i) => {
            const isGoogle = i.provider === "google";

            return (
              <div key={i.provider} className="rounded-xl border border-line px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium capitalize">
                        {i.provider}
                      </span>
                      {i.connected && !i.needs_reconnect && (
                        <Badge tone="success">
                          <Check size={9} /> connected
                        </Badge>
                      )}
                      {i.connected && i.needs_reconnect && (
                        <Badge tone="warning">limited access</Badge>
                      )}
                    </div>
                    <div className="mt-0.5 text-[11.5px] text-faint">
                      {i.connected
                        ? i.email || "Connected"
                        : isGoogle && !i.available
                          ? "Not configured on this server"
                          : "Not connected"}
                    </div>
                  </div>

                  {isGoogle ? (
                    i.connected ? (
                      <div className="flex flex-wrap gap-2">
                        <button
                          onClick={syncGoogle}
                          disabled={googleBusy !== null}
                          className="btn-ghost py-1.5"
                        >
                          {googleBusy === "sync" ? (
                            <Spinner />
                          ) : (
                            <RefreshCw size={13} />
                          )}{" "}
                          Sync now
                        </button>
                        {i.needs_reconnect && (
                          <button
                            onClick={connectGoogle}
                            disabled={googleBusy !== null}
                            className="btn-primary py-1.5"
                          >
                            {googleBusy === "connect" ? <Spinner /> : <Link2 size={13} />}{" "}
                            Reconnect
                          </button>
                        )}
                        <button
                          onClick={disconnectGoogle}
                          disabled={googleBusy !== null}
                          className="btn-quiet py-1.5"
                        >
                          {googleBusy === "disconnect" ? (
                            <Spinner />
                          ) : (
                            <Unlink size={13} />
                          )}{" "}
                          Disconnect
                        </button>
                      </div>
                    ) : i.available ? (
                      <button
                        onClick={connectGoogle}
                        disabled={googleBusy !== null}
                        className="btn-primary py-1.5"
                      >
                        {googleBusy === "connect" ? <Spinner /> : <Link2 size={13} />}{" "}
                        Connect
                      </button>
                    ) : (
                      <Badge tone="warning">needs setup</Badge>
                    )
                  ) : (
                    <Badge>coming soon</Badge>
                  )}
                </div>

                {/* What this connection can actually do */}
                {isGoogle && i.connected && i.capabilities.length > 0 && (
                  <div className="mt-3 border-t border-line pt-3">
                    <div className="label mb-2">Permissions granted</div>
                    <div className="space-y-1">
                      {i.capabilities.map((c) => (
                        <div key={c.key} className="flex items-center gap-2 text-[12.5px]">
                          {c.granted ? (
                            <Check size={12} className="shrink-0 text-sage" />
                          ) : (
                            <X size={12} className="shrink-0 text-rose" />
                          )}
                          <span className={c.granted ? "text-muted" : "text-rose"}>
                            {c.label}
                          </span>
                        </div>
                      ))}
                    </div>

                    {i.needs_reconnect && (
                      <p className="mt-2.5 text-[11.5px] leading-relaxed text-amber">
                        Some permissions are missing, so those skills will fail. Reconnect
                        and leave every box ticked on Google&apos;s consent screen.
                      </p>
                    )}

                    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-faint">
                      {i.last_sync_at ? (
                        <span>
                          Last synced {fmtRelative(i.last_sync_at)} · {i.last_sync_emails}{" "}
                          email, {i.last_sync_events} events
                        </span>
                      ) : (
                        <span>Not synced yet</span>
                      )}
                      {i.connected_at && <span>Connected {fmtRelative(i.connected_at)}</span>}
                    </div>

                    {i.last_sync_error && (
                      <p className="mt-2 rounded-lg border border-rose/30 bg-rose/10 px-2.5 py-1.5 text-[11.5px] leading-relaxed text-rose">
                        {i.last_sync_error}
                      </p>
                    )}
                  </div>
                )}

                {/* Not configured on the server — say what to do about it */}
                {isGoogle && !i.available && (
                  <p className="mt-2.5 text-[11.5px] leading-relaxed text-faint">
                    Set <code className="font-mono">GOOGLE_CLIENT_ID</code> and{" "}
                    <code className="font-mono">GOOGLE_CLIENT_SECRET</code> in{" "}
                    <code className="font-mono">.env</code>, then restart the backend.
                    See <span className="font-mono">DEPLOY.md</span> §6.
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      {/* Your data */}
      <Card>
        <SectionTitle>Your data</SectionTitle>
        <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
          Everything AURA holds about you, in one JSON file. Vault values and OAuth
          tokens are excluded by name — exporting a decrypted credential would turn a
          data-rights feature into a way to steal one.
        </p>
        <button
          onClick={() => run(() => api.downloadExport(), "Export downloaded")}
          className="btn-ghost"
        >
          <Download size={13} /> Export everything
        </button>
      </Card>

      {/* Danger zone */}
      <Card className="border-rose/30">
        <SectionTitle>
          <span className="flex items-center gap-2 text-rose">
            <AlertTriangle size={12} /> Delete account
          </span>
        </SectionTitle>
        <p className="mb-3 text-[12.5px] leading-relaxed text-muted">
          Removes your account, memories, messages, documents, credentials and every
          skill it learned. There is no undo and no backup. Model providers may keep
          their own request logs under their policies.
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            className="input flex-1 sm:max-w-xs"
            placeholder={`Type ${user?.email ?? "your email"} to confirm`}
            value={deleteConfirm}
            onChange={(e) => setDeleteConfirm(e.target.value)}
          />
          <button
            onClick={async () => {
              if (deleteConfirm.trim().toLowerCase() !== (user?.email ?? "").toLowerCase()) {
                toast.error("The email doesn't match this account.");
                return;
              }
              setDeleting(true);
              const result = await run(() => api.deleteAccount(deleteConfirm.trim()));
              setDeleting(false);
              if (result?.deleted) {
                clearToken();
                window.location.href = "/";
              }
            }}
            disabled={deleting || !deleteConfirm.trim()}
            className="btn bg-rose text-white hover:brightness-105"
          >
            {deleting ? <Spinner /> : <Trash2 size={13} />} Delete permanently
          </button>
        </div>
      </Card>
    </div>
  );
}
