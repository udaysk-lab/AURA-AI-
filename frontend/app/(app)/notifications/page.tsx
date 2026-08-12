"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Bell, CheckCheck, Radio } from "lucide-react";
import { Activity, Heartbeat, NotificationItem, api } from "@/lib/api";
import { useAction } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  PageHeader,
  SectionTitle,
  SkillLine,
  Skeleton,
  fmtRelative,
} from "@/components/ui";

const TONE: Record<string, string> = {
  info: "normal",
  success: "success",
  warning: "warning",
  urgent: "urgent",
};

export default function NotificationsPage() {
  const run = useAction();
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [beats, setBeats] = useState<Heartbeat[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [n, h, a] = await Promise.all([
        api.notifications(),
        api.heartbeats(8).catch(() => []),
        api.activity().catch(() => []),
      ]);
      setNotifications(n);
      setBeats(h);
      setActivity(a);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const unread = notifications.filter((n) => !n.read).length;

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-6 sm:p-8">
      <PageHeader
        title="Notifications"
        glow="amber"
        blurb={`${
          unread ? `${unread} unread` : "All caught up"
        } · everything your assistant thought was worth interrupting you for.`}
        action={
          unread > 0 ? (
            <button
              onClick={async () => {
                await run(() => api.markNotificationsRead(), "Marked as read");
                await load();
              }}
              className="btn-ghost"
            >
              <CheckCheck size={14} /> Mark all read
            </button>
          ) : undefined
        }
      />

      {loading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : notifications.length === 0 ? (
        <EmptyState
          title="Nothing yet"
          hint="Your assistant only notifies you when something genuinely needs you."
          icon={<Bell size={20} />}
        />
      ) : (
        <div className="space-y-2">
          {notifications.map((n) => {
            const body = (
              <div
                className={`panel p-4 transition-all duration-200 ${
                  n.read ? "opacity-60" : "border-accent/25"
                } ${
                  n.link
                    ? "hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-lift"
                    : ""
                }`}
              >
                {!n.read && (
                  <span className="absolute inset-y-4 left-0 w-0.5 rounded-full bg-accent-soft" />
                )}
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  {!n.read && (
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent shadow-glow-sm" />
                  )}
                  <span className="text-[14px] font-medium">{n.title}</span>
                  <Badge tone={TONE[n.level] ?? "normal"}>{n.level}</Badge>
                  <span className="text-[11px] text-faint">{fmtRelative(n.created_at)}</span>
                </div>
                {n.body && (
                  <p className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-muted">
                    {n.body}
                  </p>
                )}
              </div>
            );
            return n.link ? (
              <Link key={n.id} href={n.link} className="block">
                {body}
              </Link>
            ) : (
              <div key={n.id}>{body}</div>
            );
          })}
        </div>
      )}

      {beats.length > 0 && (
        <Card>
          <SectionTitle>
            <span className="flex items-center gap-2">
              <Radio size={12} className="text-accent-soft" /> Background passes
            </span>
          </SectionTitle>
          <div className="space-y-3">
            {beats.map((b) => (
              <div key={b.id} className="panel-raised p-3.5">
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <span className="text-[12.5px] font-medium">{b.headline}</span>
                  <span className="text-[11px] text-faint">{fmtRelative(b.created_at)}</span>
                  {b.needs_attention > 0 && (
                    <Badge tone="warning">{b.needs_attention} need you</Badge>
                  )}
                </div>
                {b.lines.map((line, i) => {
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
            ))}
          </div>
        </Card>
      )}

      <Card>
        <SectionTitle>Audit trail</SectionTitle>
        {activity.length === 0 ? (
          <EmptyState title="No actions recorded yet" />
        ) : (
          <div className="space-y-0.5">
            {activity.slice(0, 25).map((a) => (
              <div key={a.id} className="flex items-center gap-3 rounded-lg px-2 py-2 text-[12.5px]">
                <span
                  className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                    a.status === "success"
                      ? "bg-sage"
                      : a.status === "error"
                        ? "bg-rose"
                        : "bg-faint"
                  }`}
                />
                <span className="text-muted">{a.actor}</span>
                <span className="font-medium">{a.action}</span>
                <span className="min-w-0 flex-1 truncate text-faint">{a.target}</span>
                <span className="shrink-0 text-[11px] text-faint">
                  {fmtRelative(a.created_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
