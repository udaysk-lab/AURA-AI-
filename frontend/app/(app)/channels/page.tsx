"use client";

import { useCallback, useEffect, useId, useState } from "react";
import {
  Check,
  Copy,
  Globe,
  Hash,
  Link2,
  type LucideIcon,
  Mail,
  MessageCircle,
  Phone,
  RefreshCw,
  Terminal,
  Unlink,
} from "lucide-react";
import { ApiError, ChannelItem, api } from "@/lib/api";
import { useAction, useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  fmtRelative,
} from "@/components/ui";

const ICONS: Record<string, LucideIcon> = {
  web: Globe,
  email: Mail,
  telegram: MessageCircle,
  slack: Hash,
  cli: Terminal,
  sms: MessageCircle,
  voice: Phone,
};

function OwnerControl({
  channel,
  draft,
  onDraft,
  busy,
  error,
  onSave,
}: {
  channel: ChannelItem;
  draft: string;
  onDraft: (v: string) => void;
  busy: boolean;
  error?: string;
  onSave: (c: ChannelItem, sender: string, bridgeOnly: boolean) => void;
}) {
  const id = useId();
  const confirmed = Boolean(channel.owner_sender) || Boolean(channel.bridge_owner_only);
  return (
    <div className="mt-3 rounded-xl border border-line bg-raised/50 p-3">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <span className="label">Who is you on {channel.name}</span>
        {confirmed ? (
          <Badge tone="success">
            <Check size={9} />{" "}
            {channel.bridge_owner_only ? "bridge forwards only you" : `owner: ${channel.owner_sender}`}
          </Badge>
        ) : (
          <Badge tone="warning">not confirmed</Badge>
        )}
      </div>
      <p className="text-[12.5px] leading-relaxed text-muted">
        AURA starts a task from a message only when it comes from you. Anyone else&apos;s
        message is kept as a notification.
        {confirmed
          ? ""
          : " Until you confirm this, every message on this channel only creates a notification."}
      </p>
      <form
        className="mt-2 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) onSave(channel, draft, false);
        }}
      >
        <div className="min-w-[14rem] flex-1">
          <label htmlFor={id} className="mb-1 block text-[12.5px] text-muted">
            Your address, handle or number on this channel
          </label>
          <input
            id={id}
            className="input min-h-[44px] w-full"
            value={draft}
            maxLength={320}
            onChange={(e) => onDraft(e.target.value)}
            autoComplete="off"
          />
        </div>
        <button
          type="submit"
          className="btn-primary min-h-[44px]"
          disabled={busy || !draft.trim()}
        >
          {busy ? <Spinner /> : <Check size={13} />} Confirm me as owner
        </button>
      </form>
      {channel.kind === "email" ? (
        <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
          A sender address on email can be faked, so a request that arrives by email always asks
          you first, whatever mode you use.
        </p>
      ) : (
        <button
          type="button"
          className="btn-ghost mt-2 min-h-[44px]"
          disabled={busy}
          aria-pressed={Boolean(channel.bridge_owner_only)}
          onClick={() => onSave(channel, "", true)}
        >
          This bridge forwards only my messages
        </button>
      )}
      {error && (
        <p role="alert" className="mt-2 text-[12.5px] leading-relaxed text-rose">
          {error}
        </p>
      )}
    </div>
  );
}

export default function ChannelsPage() {
  const run = useAction();
  const toast = useToast();
  const [channels, setChannels] = useState<ChannelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  // Tokens are returned exactly once, on connect or rotate. Held in memory only.
  const [tokens, setTokens] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setChannels(await api.channels());
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load channels");
    } finally {
      setLoading(false);
    }
    // toast is stable (memoised in ToastProvider).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const connect = async (channel: ChannelItem) => {
    setBusy(channel.kind);
    await run(async () => {
      const result = await api.connectChannel(channel.kind);
      if (result.token) setTokens((t) => ({ ...t, [channel.kind]: result.token! }));
      await load();
    }, `${channel.name} connected`);
    setBusy(null);
  };

  const rotate = async (channel: ChannelItem) => {
    setBusy(channel.kind);
    await run(async () => {
      const result = await api.rotateChannelToken(channel.kind);
      if (result.token) setTokens((t) => ({ ...t, [channel.kind]: result.token! }));
      await load();
    }, "New token issued — the old one stopped working");
    setBusy(null);
  };

  const disconnect = async (channel: ChannelItem) => {
    if (
      !window.confirm(
        `Disconnect ${channel.name}? Its token stops working immediately; connecting again issues a new one.`
      )
    ) {
      return;
    }
    setBusy(channel.kind);
    await run(async () => {
      await api.disconnectChannel(channel.kind);
      setTokens((t) => {
        const next = { ...t };
        delete next[channel.kind];
        return next;
      });
      await load();
    }, `${channel.name} disconnected`);
    setBusy(null);
  };

  const [ownerDraft, setOwnerDraft] = useState<Record<string, string>>({});
  const [ownerError, setOwnerError] = useState<Record<string, string>>({});

  const saveOwner = async (channel: ChannelItem, sender: string, bridgeOnly: boolean) => {
    setBusy(channel.kind);
    setOwnerError((m) => ({ ...m, [channel.kind]: "" }));
    try {
      await api.setChannelOwner(channel.kind, sender.trim(), bridgeOnly);
      toast.success(`Owner confirmed on ${channel.name}`);
      await load();
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 403
          ? `${e.message} Open AURA on the computer it runs on, from its app window, to change this.`
          : e instanceof Error
            ? e.message
            : "Couldn't save";
      setOwnerError((m) => ({ ...m, [channel.kind]: msg }));
    } finally {
      setBusy(null);
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied");
    } catch {
      toast.error("Couldn't copy — select it manually");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-8">
      <PageHeader
        title="Channels"
        glow="azure"
        blurb={
          <>
            Where you can reach your assistant. Every channel runs the same agent with the
            same skills and the same permission rules — a message from Telegram
            isn&apos;t treated as more trusted than one typed here.
          </>
        }
      />

      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-32" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {channels.map((c) => {
            const Icon = ICONS[c.kind] ?? Globe;
            const token = tokens[c.kind];
            return (
              <div
                key={c.kind}
                className={`panel p-5 transition-all duration-200 ${
                  c.available ? "hover:border-accent/25" : "opacity-55"
                } ${c.connected ? "border-accent/25" : ""}`}
              >
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-start gap-3">
                    <div
                      className={`flex h-9 w-9 items-center justify-center rounded-xl border ${
                        c.connected
                          ? "border-accent/30 bg-accent-dim text-accent-soft shadow-glow-sm"
                          : "border-line bg-raised/60 text-faint"
                      }`}
                    >
                      <Icon size={16} />
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-[15px] font-semibold">{c.name}</span>
                        {c.connected && c.verified && (
                          <Badge tone="success">
                            <Check size={9} /> live
                          </Badge>
                        )}
                        {c.connected && !c.verified && (
                          <Badge tone="warning">awaiting first message</Badge>
                        )}
                      </div>
                      <p className="mt-0.5 text-[12.5px] text-muted">{c.blurb}</p>
                      {c.message_count > 0 && (
                        <p className="mt-1 text-[11.5px] text-faint">
                          {c.message_count} message{c.message_count === 1 ? "" : "s"}
                          {c.last_seen_at ? ` · last ${fmtRelative(c.last_seen_at)}` : ""}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="flex gap-2">
                    {!c.available ? null : c.kind === "web" ? (
                      <Badge>always on</Badge>
                    ) : c.connected ? (
                      <>
                        <button
                          onClick={() => rotate(c)}
                          disabled={busy === c.kind}
                          className="btn-ghost py-1.5"
                          title="Issue a new token and invalidate the old one"
                          aria-label={`Issue a new ${c.name} token`}
                        >
                          {busy === c.kind ? <Spinner /> : <RefreshCw size={13} />}
                        </button>
                        <button
                          onClick={() => disconnect(c)}
                          disabled={busy === c.kind}
                          className="btn-quiet py-1.5"
                        >
                          <Unlink size={13} /> Disconnect
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={() => connect(c)}
                        disabled={busy === c.kind}
                        className="btn-primary py-1.5"
                      >
                        {busy === c.kind ? <Spinner /> : <Link2 size={13} />} Connect
                      </button>
                    )}
                  </div>
                </div>

                {!c.available && (
                  <div className="rounded-xl border border-dashed border-line px-3 py-2 text-[12px] text-faint">
                    {c.unavailable_reason}
                  </div>
                )}

                {token && (
                  <div className="mt-3 rounded-xl border border-amber/35 bg-amber/10 p-3">
                    <div className="label mb-1.5 text-amber">
                      Token — shown once, copy it now
                    </div>
                    <div className="flex items-center gap-2">
                      <code className="min-w-0 flex-1 truncate rounded-lg bg-panel px-2.5 py-1.5 font-mono text-[11.5px]">
                        {token}
                      </code>
                      <button onClick={() => copy(token)} className="btn-ghost py-1.5">
                        <Copy size={12} /> Copy
                      </button>
                    </div>
                    <p className="mt-2 text-[11.5px] leading-relaxed text-muted">
                      This is the credential for inbound messages. It isn&apos;t recoverable
                      — rotate to get a new one.
                    </p>
                  </div>
                )}

                {c.connected && c.inbound && c.kind !== "web" && (
                  <OwnerControl
                    channel={c}
                    draft={ownerDraft[c.kind] ?? c.owner_sender ?? ""}
                    onDraft={(v) => setOwnerDraft((m) => ({ ...m, [c.kind]: v }))}
                    busy={busy === c.kind}
                    error={ownerError[c.kind]}
                    onSave={saveOwner}
                  />
                )}

                {c.connected && c.setup && (
                  <div className="mt-3 rounded-xl border border-line bg-raised/50 p-3">
                    <div className="label mb-1.5">Setup</div>
                    <pre className="whitespace-pre-wrap break-words font-mono text-[11.5px] leading-relaxed text-muted">
                      {c.setup}
                    </pre>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <Card>
        <SectionTitle>How inbound works</SectionTitle>
        <p className="text-[12.5px] leading-relaxed text-muted">
          A webhook has no login session, so each channel gets its own token instead. It is
          compared in constant time, scoped to one channel of one account, and rotatable.
          Approvals still apply: if a message from Telegram asks your assistant to send an
          email, it queues for you here rather than going out.
        </p>
      </Card>
    </div>
  );
}
