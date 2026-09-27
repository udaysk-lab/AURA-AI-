"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CalendarPlus, Clock, Trash2, Users } from "lucide-react";
import { CalendarEvent, Conflict, FreeSlot, api } from "@/lib/api";
import { useToast } from "@/components/Toast";
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
  fmtDayLabel,
  fmtTime,
  toDate,
} from "@/components/ui";

const EMPTY_FORM = { title: "", start_at: "", duration: 30, location: "", attendees: "" };

export default function CalendarPage() {
  const toast = useToast();
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [conflicts, setConflicts] = useState<Conflict[]>([]);
  const [slots, setSlots] = useState<FreeSlot[]>([]);
  const [loading, setLoading] = useState(true);
  const [slotsLoading, setSlotsLoading] = useState(true);
  const [brief, setBrief] = useState<Record<string, any> | null>(null);
  const [briefError, setBriefError] = useState("");
  const [briefOpen, setBriefOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [duration, setDuration] = useState(30);
  const [form, setForm] = useState(EMPTY_FORM);

  const load = useCallback(async () => {
    try {
      const [e, c] = await Promise.all([api.events(14), api.conflicts()]);
      setEvents(e);
      setConflicts(c);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't load your calendar");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  // Free slots are fetched on their own: switching 30m → 45m used to reload
  // the whole calendar (and blank it) just to recompute one card.
  const loadSlots = useCallback(async () => {
    setSlotsLoading(true);
    try {
      setSlots(await api.freeSlots(duration, 5));
    } catch {
      setSlots([]);
    } finally {
      setSlotsLoading(false);
    }
  }, [duration]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void loadSlots();
  }, [loadSlots]);

  const grouped = events.reduce<Record<string, CalendarEvent[]>>((acc, e) => {
    const key = toDate(e.start_at).toDateString();
    (acc[key] ||= []).push(e);
    return acc;
  }, {});

  const openBrief = async (id: string) => {
    setBriefOpen(true);
    setBrief(null);
    setBriefError("");
    try {
      setBrief(await api.meetingBrief(id));
    } catch (e) {
      setBriefError(e instanceof Error ? e.message : "Couldn't prepare a brief for that meeting.");
    }
  };

  const remove = async (e: CalendarEvent) => {
    const where = e.source === "google" ? " It will be removed from Google Calendar too." : "";
    if (!window.confirm(`Delete "${e.title}"?${where}`)) return;
    setDeleting(e.id);
    try {
      await api.deleteEvent(e.id);
      toast.success("Event deleted");
      await Promise.all([load(), loadSlots()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't delete that event");
    } finally {
      setDeleting(null);
    }
  };

  const minutes = Number.isFinite(form.duration) ? form.duration : 0;
  const formError = !form.title.trim()
    ? "Give it a title."
    : !form.start_at
      ? "Pick a start time."
      : minutes < 5 || minutes > 1440
        ? "Length must be between 5 and 1440 minutes."
        : "";

  const create = async () => {
    if (formError || creating) return;
    const start = new Date(form.start_at);
    setCreating(true);
    try {
      await api.createEvent({
        title: form.title.trim(),
        start_at: start.toISOString(),
        end_at: new Date(start.getTime() + minutes * 60000).toISOString(),
        location: form.location.trim(),
        attendees: form.attendees
          .split(",")
          .map((a) => a.trim())
          .filter(Boolean),
      });
      setCreateOpen(false);
      setForm(EMPTY_FORM);
      toast.success("Event created");
      await Promise.all([load(), loadSlots()]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't create that event");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="mx-auto max-w-6xl space-y-5 p-4 sm:p-8">
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

      {/* minmax(0, …) so a long title truncates instead of pushing the column,
          and on a phone the page, past the edge of the screen. */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-5">
          {loading ? (
            <Skeleton className="h-72 w-full" />
          ) : Object.keys(grouped).length === 0 ? (
            <Card>
              <EmptyState title="Nothing scheduled" hint="The next 14 days are clear." />
            </Card>
          ) : (
            Object.entries(grouped).map(([day, list]) => (
              <div key={day}>
                <div className="label mb-2 px-1">
                  {fmtDayLabel(list[0].start_at)}
                  {["Today", "Tomorrow"].includes(fmtDayLabel(list[0].start_at)) && (
                    <span className="ml-2 normal-case tracking-normal text-faint/80">
                      {fmtDay(list[0].start_at)}
                    </span>
                  )}
                </div>
                <Card className="space-y-1 p-2 sm:p-3">
                  {list.map((e) => (
                    <div
                      key={e.id}
                      className="group flex items-start gap-3 rounded-xl border-l-2 border-l-transparent px-2.5 py-2.5 transition hover:border-l-accent hover:bg-raised/60"
                    >
                      <div className="w-[68px] shrink-0 text-[12px] tabular-nums leading-snug">
                        <div className="whitespace-nowrap text-accent-soft">{fmtTime(e.start_at)}</div>
                        <div className="whitespace-nowrap text-faint">{fmtTime(e.end_at)}</div>
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13.5px] text-ink/90" title={e.title}>
                          {e.title}
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11.5px] text-faint">
                          {e.location && <span className="truncate">{e.location}</span>}
                          {e.attendees.length > 0 && (
                            <span className="flex items-center gap-1" title={e.attendees.join(", ")}>
                              <Users size={10} /> {e.attendees.length}
                            </span>
                          )}
                          {e.source === "google" && <Badge>google</Badge>}
                        </div>
                      </div>
                      {/* Always visible on touch screens, which have no hover. */}
                      <div className="reveal-on-hover flex shrink-0 gap-1">
                        <button onClick={() => openBrief(e.id)} className="btn-quiet px-2 py-1 text-[11.5px]">
                          Brief
                        </button>
                        <button
                          onClick={() => remove(e)}
                          disabled={deleting === e.id}
                          className="btn-quiet px-2 py-1"
                          aria-label={`Delete ${e.title}`}
                          title="Delete event"
                        >
                          {deleting === e.id ? <Spinner /> : <Trash2 size={12} />}
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
                aria-pressed={duration === d}
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
          {slotsLoading ? (
            <div className="space-y-1.5">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : slots.length === 0 ? (
            <EmptyState
              title="No gaps found"
              hint="Nothing that long is open in working hours this week."
              icon={<Clock size={16} />}
            />
          ) : (
            <div className="space-y-1">
              {slots.map((s, i) => (
                <div
                  key={i}
                  className="rounded-xl border border-line bg-raised/40 px-3 py-2 text-[12.5px] text-muted transition-colors hover:border-teal/40"
                >
                  <div className="text-faint">{fmtDayLabel(s.start_at)}</div>
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
        {briefError ? (
          <p className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2.5 text-[13px] text-rose">
            {briefError}
          </p>
        ) : !brief ? (
          <div className="flex items-center gap-2 text-[13px] text-muted">
            <Spinner /> Preparing…
          </div>
        ) : (
          <div className="space-y-4 text-[13px]">
            <div>
              <div className="text-[15px] font-semibold text-ink">{brief.event?.title}</div>
              <div className="text-[12px] text-muted">
                {fmtDay(brief.event?.start_at)} · {fmtTime(brief.event?.start_at)}
              </div>
            </div>

            {brief.contacts?.length > 0 && (
              <div>
                <div className="label mb-1.5">Who&apos;s coming</div>
                {brief.contacts.map((c: any, i: number) => (
                  <div key={i} className="text-ink/85">
                    {[c.name, [c.role, c.company].filter(Boolean).join(", ")]
                      .filter(Boolean)
                      .join(" — ")}
                    {c.notes && <span className="text-faint"> · {c.notes}</span>}
                  </div>
                ))}
              </div>
            )}

            {brief.relevant_memories?.length > 0 && (
              <div>
                <div className="label mb-1.5">Context</div>
                <ul className="space-y-1 text-muted">
                  {brief.relevant_memories.map((m: string, i: number) => (
                    <li key={i}>• {m}</li>
                  ))}
                </ul>
              </div>
            )}

            {brief.related_emails?.length > 0 && (
              <div>
                <div className="label mb-1.5">Recent email</div>
                <ul className="space-y-1 text-muted">
                  {brief.related_emails.slice(0, 4).map((e: any) => (
                    <li key={e.id}>• {e.subject}</li>
                  ))}
                </ul>
              </div>
            )}

            {brief.suggested_agenda?.length > 0 && (
              <div>
                <div className="label mb-1.5">Suggested agenda</div>
                <ol className="space-y-1 text-ink/85">
                  {brief.suggested_agenda.map((a: string, i: number) => (
                    <li key={i}>
                      {i + 1}. {a}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New event">
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <div>
            <label htmlFor="event-title" className="label mb-1.5 block">
              Title
            </label>
            <input
              id="event-title"
              className="input"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              autoFocus
            />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_120px]">
            <div>
              <label htmlFor="event-start" className="label mb-1.5 block">
                Starts
              </label>
              <input
                id="event-start"
                type="datetime-local"
                className="input"
                value={form.start_at}
                onChange={(e) => setForm({ ...form, start_at: e.target.value })}
              />
            </div>
            <div>
              <label htmlFor="event-minutes" className="label mb-1.5 block">
                Minutes
              </label>
              <input
                id="event-minutes"
                type="number"
                min={5}
                max={1440}
                step={5}
                className="input"
                value={Number.isFinite(form.duration) ? form.duration : ""}
                onChange={(e) => setForm({ ...form, duration: e.target.valueAsNumber })}
              />
            </div>
          </div>
          <div>
            <label htmlFor="event-location" className="label mb-1.5 block">
              Location
            </label>
            <input
              id="event-location"
              className="input"
              value={form.location}
              onChange={(e) => setForm({ ...form, location: e.target.value })}
            />
          </div>
          <div>
            <label htmlFor="event-attendees" className="label mb-1.5 block">
              Attendees (comma separated)
            </label>
            <input
              id="event-attendees"
              className="input"
              value={form.attendees}
              onChange={(e) => setForm({ ...form, attendees: e.target.value })}
            />
          </div>
          {formError && (form.title || form.start_at) && (
            <p className="text-[12px] text-amber">{formError}</p>
          )}
          <button type="submit" disabled={!!formError || creating} className="btn-primary w-full">
            {creating ? <Spinner /> : null} Create event
          </button>
        </form>
      </Modal>
    </div>
  );
}
