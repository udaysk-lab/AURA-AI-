import {
  Calendar,
  CheckSquare,
  Mail,
  Radio,
  ShieldCheck,
  Sparkles,
  Wand2,
} from "lucide-react";

/**
 * The full skill catalogue, in the order it is declared in
 * backend/app/agent/skills.py.
 *
 * This list previously drifted: it advertised MM02, DR01, AU01 and PL01, none of
 * which exist, and omitted eleven skills that do. Keep it in step with the
 * backend catalogue — or better, when this page can afford a client fetch, read
 * it from GET /api/skills so it cannot drift again.
 */
const SKILL_CODES = [
  "EM01", "EM02", "EM03",
  "CA01", "CA02", "CA03",
  "MP01",
  "TK01", "TK02",
  "MM01",
  "CT01",
  "BR01",
  "NT01",
  "SY01",
  "RS01", "RS02",
  "DC01", "DC02",
  "DL01", "DL02",
  "FG01",
] as const;

/**
 * Asymmetric capability mosaic. Each tile carries a small piece of real product
 * UI rather than an icon on its own, so the grid reads as a product tour.
 */
export default function Bento() {
  return (
    <section id="skills" className="relative mx-auto max-w-6xl px-6 py-24">
      <div
        className="aurora-spot right-0 top-24 h-[420px] w-[420px]"
        style={{ background: "rgb(var(--c-azure) / 0.24)" }}
      />

      <div className="relative mb-12 max-w-2xl">
        <span className="pill mb-5">Capabilities</span>
        <h2 className="display text-shine mb-4 text-[34px] sm:text-[46px]">
          Skills, not features.
        </h2>
        <p className="text-[15px] leading-relaxed text-muted">
          Fourteen named capabilities you can switch on, off, and teach. Turn one
          off and your assistant genuinely can&apos;t use it — and it will say so.
        </p>
      </div>

      <div className="relative grid gap-4 md:grid-cols-3">
        {/* Email — wide tile with a triage preview */}
        <article className="glass group md:col-span-2">
          <div className="p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-accent-soft">
                <Mail size={15} />
              </span>
              <span className="label">EM01 · Inbox</span>
            </div>
            <h3 className="mb-2 text-[19px] font-semibold tracking-tight">
              You stop checking email
            </h3>
            <p className="mb-6 max-w-md text-[13.5px] leading-relaxed text-muted">
              Reads, scores and drafts — so you open your inbox to decisions rather
              than a wall of unread.
            </p>

            <div className="space-y-2">
              {[
                { from: "Priya Raman", subj: "Re: Northwind contract redline", tone: "urgent", tag: "Needs you" },
                { from: "Stripe", subj: "Your invoice is ready", tone: "low", tag: "Archived" },
                { from: "Marcus Vo", subj: "Can we move Thursday?", tone: "medium", tag: "Drafted" },
              ].map((m) => (
                <div
                  key={m.subj}
                  className="flex items-center gap-3 rounded-xl border border-line bg-raised/40 px-3.5 py-2.5 transition-colors group-hover:border-accent/20"
                >
                  <span className="w-[92px] shrink-0 truncate text-[12.5px] font-medium">
                    {m.from}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[12.5px] text-muted">
                    {m.subj}
                  </span>
                  <span
                    className={`chip shrink-0 ${
                      m.tone === "urgent"
                        ? "border-rose/30 bg-rose/10 text-rose"
                        : m.tone === "medium"
                          ? "border-teal/30 bg-teal/10 text-teal"
                          : "border-line bg-raised text-faint"
                    }`}
                  >
                    {m.tag}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </article>

        {/* Heartbeat — tall accent tile */}
        <article className="glass relative overflow-hidden">
          <div
            className="aurora-spot -right-10 -top-10 h-56 w-56"
            style={{ background: "rgb(var(--accent) / 0.45)" }}
          />
          <div className="relative flex h-full flex-col p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-accent-soft">
                <Radio size={15} />
              </span>
              <span className="label">Heartbeat</span>
            </div>
            <h3 className="mb-2 text-[19px] font-semibold tracking-tight">
              It works while you don&apos;t
            </h3>
            <p className="mb-6 text-[13.5px] leading-relaxed text-muted">
              On a timer it triages new mail, captures commitments, preps your next
              meeting and flags what&apos;s slipping.
            </p>

            <div className="mt-auto flex items-end gap-1.5">
              {[38, 22, 61, 44, 78, 33, 90, 52, 68, 41, 84, 29].map((h, i) => (
                <span
                  key={i}
                  className="flex-1 rounded-full bg-gradient-to-t from-accent/25 to-accent-soft/80"
                  style={{ height: `${h}px` }}
                />
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between text-[10.5px] text-faint">
              <span>00:00</span>
              <span className="text-accent-soft">running</span>
              <span>06:00</span>
            </div>
          </div>
        </article>

        {/* Calendar */}
        <article className="glass">
          <div className="p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-teal">
                <Calendar size={15} />
              </span>
              <span className="label">CA02 · Calendar</span>
            </div>
            <h3 className="mb-2 text-[17px] font-semibold tracking-tight">
              A calendar with judgement
            </h3>
            <p className="mb-5 text-[13.5px] leading-relaxed text-muted">
              Finds real free time, catches double-bookings, and briefs you before
              you walk into the room.
            </p>
            <div className="space-y-1.5">
              {[
                { t: "09:00", n: "Deep work — protected", c: "border-l-sage" },
                { t: "11:30", n: "Northwind partner call", c: "border-l-accent" },
                { t: "14:00", n: "Conflict: two holds", c: "border-l-amber" },
              ].map((e) => (
                <div
                  key={e.t}
                  className={`flex items-center gap-3 rounded-lg border-l-2 bg-raised/40 px-3 py-2 ${e.c}`}
                >
                  <span className="font-mono text-[11px] text-faint">{e.t}</span>
                  <span className="truncate text-[12.5px] text-muted">{e.n}</span>
                </div>
              ))}
            </div>
          </div>
        </article>

        {/* Memory */}
        <article className="glass">
          <div className="p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-violet">
                <Sparkles size={15} />
              </span>
              <span className="label">MM01 · Memory</span>
            </div>
            <h3 className="mb-2 text-[17px] font-semibold tracking-tight">
              Memory that compounds
            </h3>
            <p className="mb-5 text-[13.5px] leading-relaxed text-muted">
              Preferences, projects and decisions, retrieved by meaning — and
              consolidated so it gets sharper, not just bigger.
            </p>
            <div className="flex flex-wrap gap-1.5">
              {[
                "Protects mornings",
                "Prefers bullets",
                "Northwind = Q3 priority",
                "Never CC legal first",
                "Flies out Thursdays",
              ].map((m) => (
                <span key={m} className="chip border-violet/25 bg-violet/10 text-violet">
                  {m}
                </span>
              ))}
            </div>
          </div>
        </article>

        {/* Trust */}
        <article className="glass">
          <div className="p-7">
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-amber">
                <ShieldCheck size={15} />
              </span>
              <span className="label">Autonomy</span>
            </div>
            <h3 className="mb-2 text-[17px] font-semibold tracking-tight">
              Trust is earned, not assumed
            </h3>
            <p className="mb-5 text-[13.5px] leading-relaxed text-muted">
              Four levels of freedom. Below the top one, anything irreversible waits
              for you.
            </p>
            <div className="space-y-2">
              {[
                { l: "Ask first", on: true },
                { l: "Act on small things", on: true },
                { l: "Act, then tell me", on: true },
                { l: "Full autonomy", on: false },
              ].map((lvl, i) => (
                <div key={lvl.l} className="flex items-center gap-2.5">
                  <span
                    className={`h-1.5 flex-1 rounded-full ${
                      lvl.on ? "bg-accent" : "bg-raised"
                    }`}
                    style={lvl.on ? { opacity: 0.4 + i * 0.2 } : undefined}
                  />
                  <span
                    className={`w-[132px] shrink-0 text-[11.5px] ${
                      lvl.on ? "text-muted" : "text-faint"
                    }`}
                  >
                    {lvl.l}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </article>

        {/* Tasks + skills — wide closer */}
        <article className="glass md:col-span-3">
          <div className="grid gap-8 p-7 md:grid-cols-2">
            <div>
              <div className="mb-4 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-sage">
                  <CheckSquare size={15} />
                </span>
                <span className="label">TK01 · Commitments</span>
              </div>
              <h3 className="mb-2 text-[17px] font-semibold tracking-tight">
                It remembers what you promised
              </h3>
              <p className="text-[13.5px] leading-relaxed text-muted">
                Every &ldquo;I&apos;ll send that over&rdquo; buried in a reply becomes a
                tracked commitment, with the thread it came from attached.
              </p>
            </div>
            <div>
              <div className="mb-4 flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-raised/70 text-accent-soft">
                  <Wand2 size={15} />
                </span>
                <span className="label">Skill registry</span>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {SKILL_CODES.map((c) => (
                  <span
                    key={c}
                    className="chip border-accent/25 bg-accent-dim font-mono text-accent-soft"
                  >
                    {c}
                  </span>
                ))}
              </div>
              <p className="mt-3 text-[12.5px] text-faint">
                Each one switchable, teachable, and auditable.
              </p>
            </div>
          </div>
        </article>
      </div>
    </section>
  );
}
