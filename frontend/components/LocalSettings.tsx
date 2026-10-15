"use client";

import { useCallback, useEffect, useState } from "react";
import { Bot, Check, Copy, Smartphone, Trash2, Wifi, WifiOff } from "lucide-react";
import {
  LocalProvider,
  LocalProviderPatch,
  Pairing,
  PairedDevice,
  ProviderTest,
  RemoteSettings,
  RemoteStatus,
  api,
} from "@/lib/api";
import { useAction, useToast } from "@/components/Toast";
import { Badge, Card, SectionTitle, Spinner, Toggle, fmtRelative } from "@/components/ui";

/**
 * Settings that only exist on AURA running on your own computer.
 *
 * Someone who downloaded the app has no server and no .env to edit: this is
 * where their model key goes, and where they let their phone in. Both write
 * AURA's own settings file; the key never comes back from the server.
 */

type Choice = "anthropic" | "openai" | "local";

export function ModelSettingsCard() {
  const [state, setState] = useState<LocalProvider | null>(null);
  const [choice, setChoice] = useState<Choice>("anthropic");
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<ProviderTest | null>(null);
  const run = useAction();

  const load = useCallback(async () => {
    try {
      const s = await api.localProvider();
      setState(s);
      if (s.openai_base_url) {
        setChoice("local");
        setBaseUrl(s.openai_base_url);
        setModel(s.openai_model);
      } else if (s.openai_key_set && !s.anthropic_key_set) {
        setChoice("openai");
        setModel(s.openai_model);
      } else {
        setChoice("anthropic");
        setModel(s.anthropic_model);
      }
    } catch {
      setState(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!state) return null;

  const connected = state.provider !== "mock";

  const save = async () => {
    const body: LocalProviderPatch = { choice: "auto" };
    if (choice === "anthropic") {
      if (key.trim()) body.anthropic_api_key = key.trim();
      body.anthropic_model = model.trim();
      body.openai_base_url = "";
    } else if (choice === "openai") {
      if (key.trim()) body.openai_api_key = key.trim();
      body.openai_model = model.trim();
      body.openai_base_url = "";
      body.anthropic_api_key = "";
    } else {
      body.openai_base_url = baseUrl.trim();
      body.openai_model = model.trim();
      body.anthropic_api_key = "";
      if (key.trim()) body.openai_api_key = key.trim();
    }
    setBusy(true);
    setTest(null);
    const updated = await run(() => api.updateLocalProvider(body), "Saved");
    setBusy(false);
    if (updated) {
      setState(updated);
      setKey("");
      if (updated.provider !== "mock") {
        setTest(await api.testLocalProvider().catch(() => null));
      }
    }
  };

  const disconnect = async () => {
    if (!window.confirm("Remove the saved key? AURA goes back to its offline demo mode.")) return;
    setBusy(true);
    const updated = await run(
      () =>
        api.updateLocalProvider({ anthropic_api_key: "", openai_api_key: "", openai_base_url: "" }),
      "Key removed"
    );
    setBusy(false);
    setTest(null);
    if (updated) setState(updated);
  };

  const checkNow = async () => {
    setBusy(true);
    setTest(await api.testLocalProvider().catch(() => null));
    setBusy(false);
  };

  const placeholders: Record<Choice, { key: string; model: string }> = {
    anthropic: { key: "sk-ant-…", model: "claude-sonnet-5-5" },
    openai: { key: "sk-…", model: "gpt-4o" },
    local: { key: "(usually not needed)", model: "llama3.1" },
  };

  return (
    <Card id="model">
      <SectionTitle
        action={
          <Badge tone={connected ? "success" : "warning"}>
            {connected ? `Connected · ${state.provider}` : "Demo mode"}
          </Badge>
        }
      >
        <span className="flex items-center gap-2">
          <Bot size={12} className="text-accent-soft" /> AI model
        </span>
      </SectionTitle>
      <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
        AURA thinks with a model you connect. Without one it runs a limited offline demo that
        recognises a few kinds of request. Your key is saved on this computer only, in{" "}
        <code className="rounded bg-raised px-1 font-mono text-[11px]">{state.config_file}</code>,
        and is never shown again — not even to AURA.
      </p>

      {connected && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-xl border border-sage/30 bg-sage/10 px-3 py-2 text-[12.5px] text-sage">
          <Check size={13} />
          <span className="min-w-0 flex-1">
            Using {state.provider === "anthropic" ? `Claude (${state.anthropic_model})` : state.openai_base_url ? `${state.openai_model} at ${state.openai_base_url}` : `OpenAI (${state.openai_model})`}
            {state.anthropic_key_hint && state.provider === "anthropic" && ` · key ${state.anthropic_key_hint}`}
            {state.openai_key_hint && state.provider === "openai" && !state.openai_base_url && ` · key ${state.openai_key_hint}`}
          </span>
          <button onClick={checkNow} disabled={busy} className="btn-quiet py-1 text-[12px]">
            Test
          </button>
          <button onClick={disconnect} disabled={busy} className="btn-quiet py-1 text-[12px] text-rose">
            Remove
          </button>
        </div>
      )}

      <div className="mb-3 grid grid-cols-3 gap-1.5 rounded-xl border border-line bg-raised/30 p-1 text-[12.5px]">
        {(
          [
            ["anthropic", "Claude"],
            ["openai", "OpenAI"],
            ["local", "On this computer"],
          ] as Array<[Choice, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => {
              setChoice(value);
              setModel(
                value === "anthropic"
                  ? state.anthropic_model
                  : value === "local" && !state.openai_base_url
                    ? "llama3.1"
                    : state.openai_model
              );
              if (value === "local" && !baseUrl) setBaseUrl("http://localhost:11434/v1");
            }}
            className={`rounded-lg px-2 py-1.5 transition ${
              choice === value ? "bg-accent text-white shadow-glow-sm" : "text-muted hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy) void save();
        }}
      >
        {choice === "local" && (
          <div>
            <label htmlFor="model-base" className="label mb-1.5 block">
              Endpoint (OpenAI-compatible)
            </label>
            <input
              id="model-base"
              className="input font-mono text-[12.5px]"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434/v1"
            />
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-faint">
              Ollama, LM Studio and similar run models free on your own machine. Start one,
              then point AURA at it.
            </p>
          </div>
        )}
        <div>
          <label htmlFor="model-key" className="label mb-1.5 block">
            {choice === "anthropic" ? "Claude API key" : choice === "openai" ? "OpenAI API key" : "API key"}
            {((choice === "anthropic" && state.anthropic_key_set) || (choice !== "anthropic" && state.openai_key_set)) &&
              " (saved — leave blank to keep it)"}
          </label>
          <input
            id="model-key"
            className="input font-mono text-[12.5px]"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={placeholders[choice].key}
          />
          {choice === "anthropic" && (
            <p className="mt-1.5 text-[11.5px] leading-relaxed text-faint">
              Get one at{" "}
              <a
                href="https://console.anthropic.com/settings/keys"
                target="_blank"
                rel="noreferrer"
                className="text-accent-soft underline-offset-2 hover:underline"
              >
                console.anthropic.com
              </a>{" "}
              → API Keys. Billing must be enabled on the account.
            </p>
          )}
        </div>
        <div>
          <label htmlFor="model-name" className="label mb-1.5 block">
            Model
          </label>
          <input
            id="model-name"
            className="input font-mono text-[12.5px]"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={placeholders[choice].model}
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" disabled={busy || !state.writable} className="btn-primary">
            {busy ? <Spinner /> : <Check size={13} />} Save and connect
          </button>
          {!state.writable && (
            <span className="text-[11.5px] text-rose">AURA can&apos;t write its settings file.</span>
          )}
        </div>
      </form>

      {test && (
        <p
          role="status"
          className={`mt-3 rounded-lg border px-3 py-2 text-[12px] leading-relaxed ${
            test.ok ? "border-sage/30 bg-sage/10 text-sage" : "border-rose/30 bg-rose/10 text-rose"
          }`}
        >
          {test.detail}
        </p>
      )}
    </Card>
  );
}

export function RemoteAccessCard() {
  const [settings, setSettings] = useState<RemoteSettings | null>(null);
  const [status, setStatus] = useState<RemoteStatus | null>(null);
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [hostnames, setHostnames] = useState("");
  const [busy, setBusy] = useState(false);
  const run = useAction();
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [s, st, d] = await Promise.all([
        api.localRemote(),
        api.remoteStatus(),
        api.pairedDevices().catch(() => [] as PairedDevice[]),
      ]);
      setSettings(s);
      setStatus(st);
      setDevices(d);
      setHostnames(s.hostnames);
    } catch {
      setSettings(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!settings || !status) return null;

  const toggle = async (enabled: boolean) => {
    setBusy(true);
    const updated = await run(() => api.updateLocalRemote({ enabled, hostnames }));
    setBusy(false);
    if (updated) {
      setPairing(null);
      await load();
      toast.push(
        enabled
          ? "Saved. Restart AURA (Stop, then open it again) to start listening on your network."
          : "Remote access is off. Paired devices can no longer reach AURA.",
        enabled ? "info" : "success"
      );
    }
  };

  const startPairing = async () => {
    setBusy(true);
    const result = await run(() => api.startPairing());
    setBusy(false);
    if (result) setPairing(result);
  };

  const unpair = async (device: PairedDevice) => {
    if (!window.confirm(`Unpair ${device.name}? It's signed out at once.`)) return;
    await run(() => api.unpairDevice(device.id), "Unpaired");
    await load();
  };

  const live = status.enabled && status.listening;

  return (
    <Card id="remote">
      <SectionTitle
        action={
          <Toggle
            checked={settings.saved}
            onChange={(v) => void toggle(v)}
            label="Remote access"
            disabled={busy || !settings.writable}
          />
        }
      >
        <span className="flex items-center gap-2">
          <Smartphone size={12} className="text-accent-soft" /> Use AURA from your phone
        </span>
      </SectionTitle>
      <p className="mb-3 text-[12.5px] leading-relaxed text-muted">
        Reach the AURA on this computer from a phone or another laptop — chat with it, and
        approve what it wants to do — while this computer is on. Each device pairs once with a
        code shown here; unpair it any time. A paired device can&apos;t change AURA&apos;s
        model or limits, or use the command console.
      </p>
      <p className="mb-4 text-[11.5px] leading-relaxed text-faint">
        Traffic on your network isn&apos;t encrypted. Use it on Wi-Fi you trust — or install{" "}
        <a href="https://tailscale.com/download" target="_blank" rel="noreferrer" className="text-accent-soft underline-offset-2 hover:underline">
          Tailscale
        </a>{" "}
        on both devices, which encrypts the connection and works from anywhere.
      </p>

      {settings.restart_needed && (
        <div className="mb-3 rounded-xl border border-amber/30 bg-amber/10 px-3 py-2 text-[12.5px] text-amber">
          {settings.saved
            ? "Saved — restart AURA to start listening on your network: press Stop AURA in its window, then open AURA again."
            : "Turned off. It stops listening on your network the next time AURA starts."}
        </div>
      )}

      {settings.saved && (
        <div className="mb-3">
          <label htmlFor="remote-hosts" className="label mb-1.5 block">
            Extra names for this computer (optional)
          </label>
          <input
            id="remote-hosts"
            className="input font-mono text-[12.5px]"
            value={hostnames}
            onChange={(e) => setHostnames(e.target.value)}
            onBlur={() => hostnames !== settings.hostnames && void toggle(true)}
            placeholder="my-pc.tailnet-name.ts.net"
          />
        </div>
      )}

      {live ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-[12.5px] text-sage">
            <Wifi size={13} /> Listening on your network
          </div>
          {pairing ? (
            <div className="rounded-2xl border border-accent/30 bg-accent-dim p-4">
              <div className="label mb-2">On the other device, open</div>
              <div className="mb-3 space-y-1">
                {pairing.urls.length === 0 && (
                  <div className="text-[12.5px] text-muted">
                    This computer&apos;s address couldn&apos;t be found — check it&apos;s on a network.
                  </div>
                )}
                {pairing.urls.map((url) => (
                  <div key={url} className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 truncate font-mono text-[13px]">{url}</code>
                    <button
                      onClick={() => {
                        void navigator.clipboard?.writeText(url);
                        toast.success("Copied");
                      }}
                      className="btn-quiet py-1"
                      aria-label={`Copy ${url}`}
                    >
                      <Copy size={12} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="label mb-1">and type this code</div>
              <div className="font-mono text-[28px] tracking-[0.25em] text-ink">{pairing.code}</div>
              <div className="mt-1 text-[11.5px] text-faint">
                Works once, for 10 minutes. Five wrong tries and it stops working.
              </div>
            </div>
          ) : (
            <button onClick={startPairing} disabled={busy} className="btn-primary">
              {busy ? <Spinner /> : <Smartphone size={13} />} Pair a device
            </button>
          )}
        </div>
      ) : (
        !settings.restart_needed && (
          <div className="flex items-center gap-2 text-[12.5px] text-faint">
            <WifiOff size={13} /> Only this computer can reach AURA.
          </div>
        )
      )}

      {devices.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <div className="label mb-2">Paired devices</div>
          <div className="space-y-1.5">
            {devices.map((d) => (
              <div
                key={d.id}
                className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-2 text-[12.5px]"
              >
                <div className="min-w-0">
                  <div className="truncate">{d.name}</div>
                  <div className="text-[11px] text-faint">
                    Paired {fmtRelative(d.created_at)}
                    {d.last_seen_at && ` · last used ${fmtRelative(d.last_seen_at)}`}
                    {d.last_address && ` · ${d.last_address}`}
                  </div>
                </div>
                <button
                  onClick={() => unpair(d)}
                  className="btn-quiet shrink-0 py-1 text-[12px] text-rose"
                  aria-label={`Unpair ${d.name}`}
                >
                  <Trash2 size={12} /> Unpair
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
