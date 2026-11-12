"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";
import { AppWindow, Ban, FolderOpen, Lock, Monitor, Plus, Trash2 } from "lucide-react";
import {
  AppTier,
  DeviceAppBlock,
  DeviceApps,
  DeviceFolder,
  DeviceStatus,
  api,
} from "@/lib/api";
import { APP_LEVELS, isTerminalOrIde } from "@/lib/permissions";
import { useAction } from "./Toast";
import { Badge, Card, EmptyState, SectionTitle, Skeleton, Spinner } from "./ui";

/** A real button that acts as a switch, with a row tall enough to tap. */
function SwitchRow({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex min-h-[44px] items-center gap-2.5 rounded-xl px-1 text-left text-[13px] disabled:opacity-55"
    >
      <span
        aria-hidden
        className={`relative h-5 w-9 shrink-0 rounded-full border border-line transition ${
          checked ? "bg-accent shadow-glow-sm" : "bg-raised"
        }`}
      >
        <span
          className={`absolute top-[1px] h-4 w-4 rounded-full bg-white shadow-sm transition-all ${
            checked ? "left-[18px]" : "left-0.5"
          }`}
        />
      </span>
      {label}
    </button>
  );
}

const norm = (p: string) => p.replace(/[\\/]+/g, "/").replace(/\/$/, "").toLowerCase();

/** True when this install limits folders to some roots and this one is outside them. */
function outsideInstallLimit(path: string, roots: string[] | undefined): boolean {
  if (!roots || roots.length === 0) return false;
  const p = norm(path);
  return !roots.some((r) => {
    const root = norm(r);
    return p === root || p.startsWith(`${root}/`);
  });
}

// ---------------------------------------------------------------------------

export function ConnectedFolders({ status }: { status: DeviceStatus }) {
  const run = useAction();
  const uid = useId();
  const [folders, setFolders] = useState<DeviceFolder[] | null>(null);
  const [path, setPath] = useState("");
  const [canDelete, setCanDelete] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setFolders(await api.deviceFolders());
    } catch {
      setFolders([]);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const connect = async () => {
    const value = path.trim();
    if (!value || busy) return;
    if (
      canDelete &&
      !window.confirm(
        `Let AURA delete files in ${value}? Each delete will still ask you first.`
      )
    ) {
      return;
    }
    setBusy("new");
    const created = await run(() => api.connectDeviceFolder(value, canDelete), "Folder connected");
    setBusy(null);
    if (created) {
      setPath("");
      setCanDelete(false);
      await load();
    }
  };

  const toggleDelete = async (f: DeviceFolder, next: boolean) => {
    if (
      next &&
      !window.confirm(`Let AURA delete files in ${f.path}? Each delete will still ask you first.`)
    ) {
      return;
    }
    setBusy(f.id);
    await run(() => api.setDeviceFolderDelete(f.id, next));
    setBusy(null);
    await load();
  };

  const remove = async (f: DeviceFolder) => {
    if (!window.confirm(`Remove ${f.path} from AURA's folders? Nothing on disk is changed.`)) return;
    setBusy(f.id);
    await run(() => api.removeDeviceFolder(f.id), "Folder removed");
    setBusy(null);
    await load();
  };

  return (
    <Card id="folders">
      <SectionTitle>
        <span className="flex items-center gap-2">
          <FolderOpen size={12} className="text-accent-soft" aria-hidden /> Connected folders
        </span>
      </SectionTitle>
      <p className="mb-4 text-[13px] leading-relaxed text-muted">
        AURA can read and write files only inside these folders. If it needs another one it
        asks you. Passwords, keys, start-up items and AURA&apos;s own files stay off limits in
        every folder. Deleting files for good always asks you, even where deleting is allowed.
      </p>

      {folders === null ? (
        <Skeleton className="h-20" />
      ) : folders.length === 0 ? (
        <EmptyState title="No folders connected" hint="Connect one below, or wait for AURA to ask." />
      ) : (
        <ul className="space-y-2.5">
          {folders.map((f) => {
            const outside = outsideInstallLimit(f.path, status.policy?.allowed_roots);
            return (
              <li key={f.id} className="rounded-xl border border-line bg-raised/40 p-3.5">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="min-w-0 flex-1 break-all text-[12.5px] text-ink">{f.path}</code>
                  {f.source === "legacy" && <Badge>Carried over</Badge>}
                  {f.source === "request" && <Badge>Added when AURA asked</Badge>}
                </div>
                {outside && (
                  <p className="mt-1.5 text-[12.5px] leading-relaxed text-amber">
                    Outside this install&apos;s limit, so AURA can&apos;t use it.
                  </p>
                )}
                <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                  <SwitchRow
                    checked={f.can_delete}
                    disabled={busy === f.id}
                    onChange={(next) => void toggleDelete(f, next)}
                    label="May delete files"
                  />
                  <button
                    type="button"
                    onClick={() => void remove(f)}
                    disabled={busy === f.id}
                    className="btn-quiet min-h-[44px]"
                    aria-label={`Remove folder ${f.path}`}
                  >
                    <Trash2 size={14} aria-hidden /> Remove
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      <form
        className="mt-4 space-y-2 border-t border-line pt-4"
        onSubmit={(e) => {
          e.preventDefault();
          void connect();
        }}
      >
        <label htmlFor={`${uid}-path`} className="label block">
          Connect a folder
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id={`${uid}-path`}
            className="input min-h-[44px] min-w-0 flex-1 font-mono text-[12.5px]"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="D:\Work\Finance"
            spellCheck={false}
          />
          <button type="submit" disabled={!path.trim() || busy === "new"} className="btn-primary min-h-[44px] shrink-0">
            {busy === "new" ? <Spinner /> : <Plus size={14} aria-hidden />} Connect
          </button>
        </div>
        <SwitchRow checked={canDelete} onChange={setCanDelete} label="May delete files" />
      </form>
    </Card>
  );
}

// ---------------------------------------------------------------------------

const blockText = (b: DeviceAppBlock) =>
  b.match === "keyword" ? `Anything with "${b.app_id}" in its name` : b.app_id;

function isBlocked(appId: string, blocks: DeviceAppBlock[]) {
  const id = appId.toLowerCase();
  return blocks.some((b) =>
    b.match === "keyword" ? id.includes(b.app_id.toLowerCase()) : id === b.app_id.toLowerCase()
  );
}

export function AppAccess({ status }: { status: DeviceStatus }) {
  const run = useAction();
  const uid = useId();
  const [apps, setApps] = useState<DeviceApps | null>(null);
  const [name, setName] = useState("");
  const [tier, setTier] = useState<AppTier>("click");
  const [blockName, setBlockName] = useState("");
  const [blockMatch, setBlockMatch] = useState<"app" | "keyword">("app");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.deviceApps();
      setApps({
        grants: data.grants ?? [],
        blocks: data.blocks ?? [],
        running: data.running ?? [],
      });
    } catch {
      setApps({ grants: [], blocks: [], running: [] });
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  const grant = async (appId: string, level: AppTier) => {
    setBusy(appId);
    const done = await run(() => api.setDeviceApp(appId, level), "App access saved");
    setBusy(null);
    if (done) await load();
    return Boolean(done);
  };

  const revoke = async (appId: string) => {
    if (!window.confirm(`Stop AURA using ${appId} on screen?`)) return;
    setBusy(appId);
    await run(() => api.removeDeviceApp(appId), "App access removed");
    setBusy(null);
    await load();
  };

  const addBlock = async () => {
    const value = blockName.trim();
    if (!value) return;
    setBusy("block");
    const done = await run(() => api.addDeviceBlock(value, blockMatch), "Added to blocked apps");
    setBusy(null);
    if (done) {
      setBlockName("");
      await load();
    }
  };

  const unblock = async (b: DeviceAppBlock) => {
    if (
      !window.confirm(
        `Stop blocking ${blockText(b)}? AURA could then ask to use it. You would still have to allow each app.`
      )
    ) {
      return;
    }
    setBusy(b.id);
    await run(() => api.removeDeviceBlock(b.id), "Removed from blocked apps");
    setBusy(null);
    await load();
  };

  const running = (apps?.running ?? []).map((r) =>
    typeof r === "string"
      ? { id: "", label: r }
      : { id: r.app_id ?? "", label: r.display_name ?? r.name ?? r.app_id ?? "" }
  ).filter((r) => r.label);

  const levelsFor = (appId: string) =>
    APP_LEVELS.filter((t) => !(t.key === "full" && isTerminalOrIde(appId)));

  return (
    <Card id="apps">
      <SectionTitle>
        <span className="flex items-center gap-2">
          <AppWindow size={12} className="text-accent-soft" aria-hidden /> Apps and screen control
        </span>
      </SectionTitle>

      <div
        className={`mb-4 flex items-start gap-2.5 rounded-xl border p-3.5 text-[13px] leading-relaxed ${
          status.screen_control ? "border-sage/35 bg-sage/10" : "border-line bg-raised/40 text-muted"
        }`}
        role="status"
      >
        <Monitor size={14} className={`mt-0.5 shrink-0 ${status.screen_control ? "text-sage" : ""}`} aria-hidden />
        <div>
          <div className={`font-medium ${status.screen_control ? "text-sage" : "text-ink"}`}>
            {status.screen_control
              ? "Screen control is available on this install"
              : "Screen control is not available on this install"}
          </div>
          <div className="text-muted">
            {status.screen_control
              ? "It works only while AURA is open and you are here, only in apps you allow below, and never in AURA's own window."
              : "It needs a display and the optional device packages. Files and commands still work."}
          </div>
        </div>
      </div>

      <p className="mb-4 text-[13px] leading-relaxed text-muted">
        AURA asks before it uses any app on screen. Terminals and code editors are click and
        scroll only; commands go through the shell so your rules apply. Browsers need their own
        permission.
      </p>

      {apps === null ? (
        <Skeleton className="h-20" />
      ) : (
        <>
          <h3 className="mb-2 text-[13.5px] font-medium">Apps AURA may use</h3>
          {apps.grants.length === 0 ? (
            <EmptyState title="No apps allowed yet" hint="AURA will ask the first time it needs one." />
          ) : (
            <ul className="space-y-2">
              {apps.grants.map((g) => (
                <li
                  key={g.app_id}
                  className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-raised/40 px-3.5 py-2"
                >
                  <span className="min-w-0 flex-1 break-words text-[13.5px]">
                    {g.display_name || g.app_id}
                  </span>
                  <label className="sr-only" htmlFor={`${uid}-g-${g.app_id}`}>
                    Access for {g.display_name || g.app_id}
                  </label>
                  <select
                    id={`${uid}-g-${g.app_id}`}
                    className="input min-h-[44px] w-auto"
                    value={g.tier}
                    disabled={busy === g.app_id}
                    onChange={(e) => void grant(g.app_id, e.target.value as AppTier)}
                  >
                    {levelsFor(g.app_id).map((t) => (
                      <option key={t.key} value={t.key}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    onClick={() => void revoke(g.app_id)}
                    disabled={busy === g.app_id}
                    className="btn-quiet min-h-[44px]"
                    aria-label={`Remove access for ${g.display_name || g.app_id}`}
                  >
                    <Trash2 size={14} aria-hidden /> Remove
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form
            className="mt-4 space-y-2 border-t border-line pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              const value = name.trim();
              if (value)
                void grant(value, tier).then((ok) => {
                  if (ok) setName("");
                });
            }}
          >
            <label htmlFor={`${uid}-app`} className="label block">
              Allow an app
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                id={`${uid}-app`}
                className="input min-h-[44px] min-w-0 flex-1"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  if (tier === "full" && isTerminalOrIde(e.target.value)) setTier("click");
                }}
                placeholder="App name, such as chrome"
                spellCheck={false}
              />
              <label htmlFor={`${uid}-tier`} className="sr-only">
                Access level
              </label>
              <select
                id={`${uid}-tier`}
                className="input min-h-[44px] sm:w-auto"
                value={tier}
                onChange={(e) => setTier(e.target.value as AppTier)}
              >
                {levelsFor(name).map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </select>
              <button type="submit" disabled={!name.trim() || busy === name.trim()} className="btn-primary min-h-[44px] shrink-0">
                <Plus size={14} aria-hidden /> Allow
              </button>
            </div>
            <p className="text-[12.5px] leading-relaxed text-muted">
              {APP_LEVELS.find((t) => t.key === tier)?.help} Apps on the blocked list below can&apos;t be
              allowed.
            </p>
          </form>

          {running.length > 0 && (
            <div className="mt-4 border-t border-line pt-4">
              <h3 className="mb-2 text-[13.5px] font-medium">Open on this computer now</h3>
              <ul className="flex flex-wrap gap-2">
                {running.map((r, i) => {
                  const allowed = apps.grants.some((g) => g.app_id === r.id);
                  const blocked = isBlocked(r.id || r.label, apps.blocks);
                  return (
                    <li
                      key={`${r.label}-${i}`}
                      className="flex items-center gap-2 rounded-full border border-line bg-raised/40 py-1 pl-3.5 pr-1.5 text-[13px]"
                    >
                      {r.label}
                      {blocked ? (
                        <Badge tone="urgent">Blocked</Badge>
                      ) : allowed ? (
                        <Badge tone="success">Allowed</Badge>
                      ) : r.id ? (
                        <button
                          type="button"
                          className="btn-ghost min-h-[44px] py-1"
                          disabled={busy === r.id}
                          onClick={() => void grant(r.id, isTerminalOrIde(r.id) ? "click" : "full")}
                        >
                          Allow
                        </button>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <div className="mt-4 border-t border-line pt-4">
            <h3 className="mb-1 flex items-center gap-2 text-[13.5px] font-medium">
              <Ban size={13} aria-hidden /> Blocked apps
            </h3>
            <p className="mb-3 text-[13px] leading-relaxed text-muted">
              Banking, trading, crypto wallet and password apps start on this list. AURA can
              never use a blocked app. Removing one is your choice; AURA can&apos;t do it.
            </p>
            {apps.blocks.length === 0 ? (
              <p className="text-[13px] text-muted">Nothing is blocked.</p>
            ) : (
              <ul className="flex flex-wrap gap-2">
                {apps.blocks.map((b) => (
                  <li
                    key={b.id}
                    className="flex items-center gap-1 rounded-full border border-line bg-raised/40 py-1 pl-3.5 pr-1 text-[13px]"
                  >
                    <span className="break-all">{blockText(b)}</span>
                    {b.source === "default" && <span className="text-[12px] text-muted">(default)</span>}
                    <button
                      type="button"
                      onClick={() => void unblock(b)}
                      disabled={busy === b.id}
                      className="btn-quiet min-h-[44px] min-w-[44px] px-3"
                      aria-label={`Stop blocking ${blockText(b)}`}
                    >
                      <Trash2 size={13} aria-hidden />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <form
              className="mt-3 space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void addBlock();
              }}
            >
              <label htmlFor={`${uid}-block`} className="label block">
                Block an app
              </label>
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  id={`${uid}-block`}
                  className="input min-h-[44px] min-w-0 flex-1"
                  value={blockName}
                  onChange={(e) => setBlockName(e.target.value)}
                  placeholder="App name or a word in its name"
                  spellCheck={false}
                />
                <label htmlFor={`${uid}-match`} className="sr-only">
                  How to match
                </label>
                <select
                  id={`${uid}-match`}
                  className="input min-h-[44px] sm:w-auto"
                  value={blockMatch}
                  onChange={(e) => setBlockMatch(e.target.value as "app" | "keyword")}
                >
                  <option value="app">This exact app</option>
                  <option value="keyword">Any app with this word</option>
                </select>
                <button type="submit" disabled={!blockName.trim() || busy === "block"} className="btn-ghost min-h-[44px] shrink-0">
                  <Lock size={14} aria-hidden /> Block
                </button>
              </div>
            </form>
          </div>
        </>
      )}

      <p className="mt-4 text-[12.5px] leading-relaxed text-muted">
        Everything AURA does here follows your permission mode and rules.{" "}
        <Link href="/settings/permissions" className="text-accent-soft underline-offset-2 hover:underline">
          Open Permissions
        </Link>
      </p>
    </Card>
  );
}

