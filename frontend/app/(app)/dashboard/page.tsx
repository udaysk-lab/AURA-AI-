"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Clock,
  Inbox,
  Radio,
  RefreshCw,
  Sparkles,
  WifiOff,
} from "lucide-react";
import {
  Briefing,
  Heartbeat,
  PendingAction,
  SkillRun,
  api,
} from "@/lib/api";
import { useAssistant } from "@/lib/assistant";
import { ApprovalCards } from "@/components/ApprovalCard";
import Mascot from "@/components/Mascot";
import { useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  SectionTitle,
  SkillLine,
  Skeleton,
  Spinner,
  fmtRelative,
  fmtTime,
  refreshBadges,
} from "@/components/ui";

export default function DashboardPage() {
  const { assistant, refresh: refreshAssistant } = useAssistant();
  const toast = useToast();
  const [brief, setBrief] = useState<Briefing | null>(null);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [runs, setRuns] = useState<SkillRun[]>([]);
  const [beat, setBeat] = useState<Heartbeat | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  const load = useCallback(async () => {
    try {
      // The briefing is the page; the rest are extras that may fail alone.
      const [b, p, a, h] = await Promise.all([
        api.briefing(),
        api.pendingActions().catch(() => [] as PendingAction[]),
        api.skillActivity(14).catch(() => [] as SkillRun[]),
        api.latestHeartbeat().catch(() => null),
      ]);
      setBrief(b);
      setPending(p);
      setRuns(a);
      setBeat(h);
      setError("");
    } catch (e) {
      // Without this the page rendered an empty greeting and a row of zeros.
      setError(e instanceof Error ? e.message : "Couldn't load today's briefing.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // "N waiting on you" in the sidebar links to #approvals, but that card only
  // exists once the data has loaded — after the browser has given up looking.
  useEffect(() => {
    if (loading || !pending.length || window.location.hash !== "#approvals") return;
    document.getElementById("approvals")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [loading, pending.length]);

  const runHeartbeat = async () => {
    setWorking(true);
    try {
      await api.syncEmail().catch(() => null);
      const report = await api.runHeartbeat();
      setBeat(report);
      toast.success(report.headline);
      await load();
      await refreshAssistant();
      refreshBadges();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't catch up just now");
    } finally {
      setWorking(false);
    }
  };

  const onDecided = async () => {
    toast.success("Recorded");
    await load();
    refreshBadges();
  };

  if (!loading && error && !brief) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-6">
        <div className="panel max-w-sm p-6 text-center">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-amber/12 text-amber">
            <WifiOff size={20} />
          </div>
          <h1 className="mb-1.5 text-[16px] font-semibold">Today didn&apos;t load</h1>
          <p className="mb-4 text-[13px] leading-relaxed text-muted">{error}</p>
          <button
            onClick={() => {
              setLoading(true);
              void load();
            }}
            className="btn-primary"
          >
            <RefreshCw size={14} /> Try again
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-8">
        <Skeleton className="h-40 w-full" />
        <div className="grid gap-4 md:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const stats = [
    { label: "Unread", value: brief?.inbox?.unread ?? 0, href: "/emails" },
    { label: "Meetings today", value: brief?.meetings.length ?? 0, href: "/calendar" },
    { label: "Tasks due", value: brief?.tasks_due.length ?? 0, href: "/tasks" },
    { label: "Needs reply", value: brief?.inbox?.needs_reply ?? 0, href: "/emails" },
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-8">
      {/* Greeting + assistant */}
      <div className="glass hairline-top animate-fade-up p-5 sm:p-7">
        <div
          className="aurora-spot -top-24 right-0 h-64 w-96"
          style={{ background: "rgb(var(--accent) / 0.32)" }}
        />
        <div className="relative flex flex-wrap items-start justify-between gap-5">
          <div className="flex items-start gap-4">
            <Mascot
              colourway={assistant?.avatar ?? "violet"}
              size={62}
              active={working}
            />
            <div>
              <h1 className="display text-shine text-[26px]">{brief?.greeting}</h1>
              <p className="mt-1 text-[14px] text-muted">{brief?.headline}</p>
              {assistant && (
                <p className="mt-2 text-[12.5px] text-faint">
                  {assistant.name}
                </p>
              )}
            </div>
          </div>
          <button onClick={runHeartbeat} disabled={working} className="btn-ghost">
            {working ? <Spinner /> : <RefreshCw size={14} />} Catch me up
          </button>
        </div>

        {brief?.suggested_priorities?.length ? (
          <div className="relative mt-6 border-t border-line pt-5">
            <div className="mb-3 flex items-center gap-2">
              <Sparkles size={13} className="text-accent-soft" />
              <span className="label">What I&apos;d do first</span>
            </div>
            <ol className="space-y-2">
              {brief.suggested_priorities.map((p, i) => (
                <li key={i} className="flex items-start gap-3 text-[13.5px]">
                  <span className="mt-0.5 flex h-[19px] w-[19px] shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent-dim text-[10px] font-semibold text-accent-soft">
                    {i + 1}
                  </span>
                  {p}
                </li>
              ))}
            </ol>
          </div>
        ) : null}
      </div>

      {/* What it did while you were away */}
      {beat && (
        <Card className="animate-fade-up border-accent/20">
          <SectionTitle
            action={
              <span className="text-[11.5px] text-faint">{fmtRelative(beat.created_at)}</span>
            }
          >
            <span className="flex items-center gap-2">
              <Radio size={12} className="text-accent-soft" /> While you were away
            </span>
          </SectionTitle>
          <p className="mb-3 text-[13.5px]">{beat.headline}</p>
          {beat.lines.length > 0 && (
            <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
              {beat.lines.map((line, i) => {
                const match = line.match(/^\[SKILL·([A-Z0-9]+)\]\s*(.*)$/);
                return match ? (
                  <SkillLine key={i} code={match[1]} summary={match[2]} />
                ) : (
                  <div key={i} className="skill-line">
                    {line}
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {stats.map((s) => (
          <Link
            key={s.label}
            href={s.href}
            className="panel p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-accent/30 hover:shadow-lift"
          >
            <div className="display text-shine text-[30px]">{s.value}</div>
            <div className="mt-2 text-[12px] text-muted">{s.label}</div>
          </Link>
        ))}
      </div>

      {/* Approvals */}
      {pending.length > 0 && (
        <Card id="approvals" className="scroll-mt-4 border-amber/35 bg-amber/[0.06]">
          <SectionTitle>
            <span className="flex items-center gap-2 text-amber">
              <AlertTriangle size={13} /> Waiting on you
            </span>
          </SectionTitle>
          <ApprovalCards actions={pending} onDecided={onDecided} />
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <SectionTitle
            action={
              <Link href="/calendar" className="text-[11.5px] text-muted hover:text-ink">
                Calendar →
              </Link>
            }
          >
            Today&apos;s schedule
          </SectionTitle>
          {brief?.meetings.length ? (
            <div className="space-y-0.5">
              {brief.meetings.map((m) => (
                <div
                  key={m.id}
                  className="flex items-start gap-3 rounded-xl px-2 py-2.5 transition hover:bg-raised/60"
                >
                  <div className="w-[62px] shrink-0 whitespace-nowrap pt-0.5 text-[12px] font-medium tabular-nums text-accent-soft">
                    {fmtTime(m.start_at)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13.5px]">{m.title}</div>
                    <div className="mt-0.5 flex items-center gap-2 text-[11.5px] text-faint">
                      {m.location && <span>{m.location}</span>}
                      {m.attendees.length > 0 && <span>{m.attendees.length} attending</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState
              title="No meetings today"
              hint="Enjoy the uninterrupted block."
              icon={<Clock size={18} />}
            />
          )}
        </Card>

        <Card>
          <SectionTitle
            action={
              <Link href="/emails" className="text-[11.5px] text-muted hover:text-ink">
                Inbox →
              </Link>
            }
          >
            Inbox
          </SectionTitle>
          <p className="mb-4 text-[13px] leading-relaxed text-muted">
            {brief?.inbox?.total
              ? brief.inbox.summary
              : "Your inbox is empty. Connect Google in Settings to bring your mail in."}
          </p>
          {brief?.urgent_emails.length ? (
            <div className="space-y-0.5">
              {brief.urgent_emails.map((e) => (
                <Link
                  key={e.id}
                  href="/emails"
                  className="block rounded-xl px-2 py-2.5 transition hover:bg-raised/60"
                >
                  <div className="mb-1 flex items-center gap-2">
                    <Badge tone={e.importance}>{e.importance}</Badge>
                    <span className="truncate text-[12.5px] text-muted">
                      {e.sender_name || e.sender}
                    </span>
                  </div>
                  <div className="truncate text-[13.5px]">{e.subject}</div>
                  {e.ai_summary && (
                    <div className="mt-0.5 line-clamp-2 text-[12px] text-faint">
                      {e.ai_summary}
                    </div>
                  )}
                </Link>
              ))}
            </div>
          ) : (
            <EmptyState title="Nothing urgent" icon={<Inbox size={18} />} />
          )}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <Card>
          <SectionTitle
            action={
              <Link href="/hub" className="text-[12px] text-muted hover:text-ink">
                Systems →
              </Link>
            }
          >
            Recent skill activity
          </SectionTitle>
          {runs.length ? (
            <div className="rounded-xl border border-line bg-raised/50 px-3 py-2">
              {runs.map((r) => (
                <SkillLine
                  key={r.id}
                  code={r.code}
                  summary={r.summary}
                  meta={fmtRelative(r.created_at)}
                  ok={r.status === "success"}
                />
              ))}
            </div>
          ) : (
            <EmptyState
              title="No activity yet"
              hint="Ask for something in Chat and it shows up here."
            />
          )}
        </Card>
      </div>
    </div>
  );
}
