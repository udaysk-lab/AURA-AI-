"use client";

import { useCallback, useEffect, useState } from "react";
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
import { ChannelItem, api } from "@/lib/api";
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

export default function ChannelsPage() {
  const run = useAction();
  const toast = useToast();
  const [channels, setChannels] = useState<ChannelItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  // Tokens are returned exactly once, on connect or rotate. Held in memory only.
  const [tokens, setTokens] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setChannels(await api.channels());
    } finally {
      setLoading(false);
    }
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

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied");
    } catch {
      toast.error("Couldn't copy — select it manually");
    }
  };

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6 sm:p-8">
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
