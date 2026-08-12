"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CalendarPlus, Clock, Trash2, Users } from "lucide-react";
import { CalendarEvent, Conflict, FreeSlot, api } from "@/lib/api";
import {
  Badge,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
  fmtDay,
  fmtTime,
} from "@/components/ui";

export default function CalendarPage() {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [slots, setSlots] = useState<FreeSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [brief, setBrief] = useState<Record<string, any> | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [duration, setDuration] = useState(30);

  const [form, setForm] = useState({
    title: "",
    start_at: "",
    duration: 30,
    location: "",
    attendees: "",
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [e, c, s] = await Promise.all([
        api.events(14),
        api.conflicts(),
        api.freeSlots(duration, 5),
      ]);
      setEvents(e);
      setConflicts(c);
      setSlots(s);
    } finally {
      setLoading(false);
    }
  }, [duration]);

  useEffect(() => {
    void load();
  }, [load]);

  const grouped = events.reduce<Record<string, CalendarEvent[]>>((acc, e) => {
    const key = new Date(e.start_at).toDateString();
    (acc[key] ||= []).push(e);
    return acc;
  }, {});

  const openBrief = async (id: string) => {
    setBriefOpen(true);
    setBrief(null);
    setBrief(await api.meetingBrief(id));
  };

  const create = async () => {
    if (!form.title || !form.start_at) return;
    const start = new Date(form.start_at);
    await api.createEvent({
      title: form.title,
      start_at: start.toISOString(),
      end_at: new Date(start.getTime() + form.duration * 60000).toISOString(),
      location: form.location,
      attendees: form.attendees
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean),
    });
    setCreateOpen(false);
    setForm({ title: "", start_at: "", duration: 30, location: "", attendees: "" });
    await load();
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-8">
      <PageHeader
        title="Calendar"
        glow="teal"
        blurb="Real free time, not gaps. Conflicts surface before they cost you a meeting."
        action={
          <button onClick={() => setCreateOpen(true)} className="btn-primary">
            <CalendarPlus size={14} /> New event
          </button>
        }
      />

      {conflicts.length > 0 && (
        <Card className="border-rose/30 bg-rose/[0.07]">
          <SectionTitle>
            <span className="flex items-center gap-2 text-rose">
              <AlertTriangle size={13} /> Double-booked
            </span>
          </SectionTitle>
          <div className="space-y-2">
            {conflicts.map((c, i) => (
              <div key={i} className="text-[13px] text-muted">
                <span className="font-medium text-ink">{c.event_a.title}</span>
                <span className="text-faint"> overlaps </span>
                <span className="font-medium text-ink">{c.event_b.title}</span>
                <span className="text-faint"> by {c.overlap_minutes} min on </span>
                {fmtDay(c.event_b.start_at)}
              </div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_300px]">
        <div className="space-y-5">
          {loading ? (
            <Skeleton className="h-72 w-full" />
          ) : Object.keys(grouped).length === 0 ? (
            <Card>
              <EmptyState title="Nothing scheduled" hint="The next 14 days are clear." />
            </Card>
          ) : (
            Object.entries(grouped).map(([day, list]) => (
              <div key={day}>
                <div className="label mb-2 px-1">{fmtDay(list[0].start_at)}</div>
                <Card className="space-y-1 p-3">
                  {list.map((e) => (
                    <div
                      key={e.id}
                      className="group flex items-start gap-3 rounded-xl border-l-2 border-l-transparent px-2.5 py-2.5 transition hover:border-l-accent hover:bg-raised/60"
                    >
                      <div className="w-[86px] shrink-0 text-[12px] tabular-nums text-accent-soft">
                        {fmtTime(e.start_at)}–{fmtTime(e.end_at)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13.5px] text-ink/90">{e.title}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11.5px] text-faint">
                          {e.location && <span>{e.location}</span>}
                          {e.attendees.length > 0 && (
                            <span className="flex items-center gap-1">
                              <Users size={10} /> {e.attendees.length}
                            </span>
                          )}
                          {e.source === "google" && <Badge>google</Badge>}
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-1 opacity-0 transition group-hover:opacity-100">
                        <button onClick={() => openBrief(e.id)} className="btn-quiet px-2 py-1 text-[11.5px]">
                          Brief
                        </button>
                        <button
                          onClick={async () => {
                            await api.deleteEvent(e.id);
                            await load();
                          }}
                          className="btn-quiet px-2 py-1"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  ))}
                </Card>
              </div>
            ))
          )}
        </div>

        <Card className="h-fit">
          <SectionTitle>Free slots</SectionTitle>
          <div className="mb-3 flex gap-1">
            {[30, 45, 60].map((d) => (
              <button
                key={d}
                onClick={() => setDuration(d)}
                className={`rounded-full border px-3 py-1 text-[12px] transition ${
                  duration === d
                    ? "border-accent/50 bg-accent-dim text-accent-soft"
                    : "border-line text-muted hover:border-accent/30 hover:text-ink"
                }`}
              >
                {d}m
              </button>
            ))}
          </div>
          {slots.length === 0 ? (
            <EmptyState title="No gaps found" icon={<Clock size={16} />} />
          ) : (
            <div className="space-y-1">
              {slots.map((s, i) => (
                <div
                  key={i}
                  className="rounded-xl border border-line bg-raised/40 px-3 py-2 text-[12.5px] text-muted transition-colors hover:border-teal/40"
                >
                  <div className="text-faint">{fmtDay(s.start_at)}</div>
                  <div className="tabular-nums text-ink/90">
                    {fmtTime(s.start_at)} – {fmtTime(s.end_at)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Modal open={briefOpen} onClose={() => setBriefOpen(false)} title="Meeting brief" width="max-w-xl">
        {!brief ? (
          <div className="flex items-center gap-2 text-[13px] text-zinc-500">
            <Spinner /> Preparing…
          </div>
        ) : (
          <div className="space-y-4 text-[13px]">
            <div>
              <div className="text-[15px] font-semibold text-ink">{brief.event?.title}</div>
              <div className="text-[12px] text-zinc-500">
                {fmtDay(brief.event?.start_at)} · {fmtTime(brief.event?.start_at)}
              </div>
            </div>

            {brief.contacts?.length > 0 && (
              <div>
                <div className="label mb-1.5">Who&apos;s coming</div>
                {brief.contacts.map((c: any, i: number) => (
                  <div key={i} className="text-zinc-300">
                    {c.name} — {c.role}, {c.company}
                    {c.notes && <span className="text-zinc-600"> · {c.notes}</span>}
                  </div>
                ))}
              </div>
            )}

            {brief.relevant_memories?.length > 0 && (
              <div>
                <div className="label mb-1.5">Context</div>
                <ul className="space-y-1 text-zinc-400">
                  {brief.relevant_memories.map((m: string, i: number) => (
                    <li key={i}>• {m}</li>
                  ))}
                </ul>
              </div>
            )}

            {brief.related_emails?.length > 0 && (
              <div>
                <div className="label mb-1.5">Recent email</div>
                <ul className="space-y-1 text-zinc-400">
                  {brief.related_emails.slice(0, 4).map((e: any) => (
                    <li key={e.id}>• {e.subject}</li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <div className="label mb-1.5">Suggested agenda</div>
              <ol className="space-y-1 text-zinc-300">
                {brief.suggested_agenda?.map((a: string, i: number) => (
                  <li key={i}>
                    {i + 1}. {a}
                  </li>
                ))}
              </ol>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New event">
        <div className="space-y-3">
          <div>
            <label className="label mb-1.5 block">Title</label>
            <input
              className="input"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label mb-1.5 block">Starts</label>
              <input
                type="datetime-local"
                className="input"
                value={form.start_at}
                onChange={(e) => setForm({ ...form, start_at: e.target.value })}
              />
            </div>
            <div>
              <label className="label mb-1.5 block">Minutes</label>
              <input
                type="number"
                className="input"
                value={form.duration}
                onChange={(e) => setForm({ ...form, duration: Number(e.target.value) })}
              />
            </div>
          </div>
          <div>
            <label className="label mb-1.5 block">Location</label>
            <input
              className="input"
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
            />
          </div>
          <div>
            <label className="label mb-1.5 block">Attendees (comma separated)</label>
            <input
              className="input"
              value={form.attendees}
              onChange={(e) => setForm({ ...form, attendees: e.target.value })}
            />
          </div>
          <button onClick={create} className="btn-primary w-full">
            Create event
          </button>
        </div>
      </Modal>
    </div>
  );
}
