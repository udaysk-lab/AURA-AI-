import { Calendar, CheckSquare, Mail, Radio, Sparkles, Wand2 } from "lucide-react";
import Mascot from "@/components/Mascot";

const LOG = [
  ["EM01", "Processed 47 emails · 3 flagged · 41 handled · 3 archived", "02:14"],
  ["CA02", "Rescheduled 2pm → 3pm · buffer preserved · 4 slots adjusted", "02:16"],
  ["MP01", "Briefed: Northwind partner call · agenda and context ready", "02:19"],
  ["TK01", "Captured 4 commitments from flagged email", "02:21"],
  ["CA03", "Found 1 double-booking · needs a decision", "02:23"],
  ["MM01", "Remembered: protects mornings before 10:00 for deep work", "02:24"],
];

const RAIL = [
  { icon: Radio, label: "Today", active: true },
  { icon: Mail, label: "Email" },
  { icon: Calendar, label: "Calendar" },
  { icon: CheckSquare, label: "Tasks" },
  { icon: Wand2, label: "Skills" },
  { icon: Sparkles, label: "Memory" },
];

/**
 * A miniature of the product, framed like an app window. Static by design —
 * it's a hero image made of DOM so it stays crisp and themes with the palette.
 */
export default function HeroPreview() {
  return (
    <div className="relative">
      {/* Glow pooled beneath the window */}
      <div
        className="aurora-spot inset-x-16 bottom-[-40px] h-40 animate-glow-pulse"
        style={{ background: "rgb(var(--accent) / 0.42)" }}
      />

      <div className="glass relative">
        {/* Window chrome */}
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <span className="h-2.5 w-2.5 rounded-full bg-rose/60" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber/60" />
          <span className="h-2.5 w-2.5 rounded-full bg-sage/60" />
          <div className="mx-auto flex items-center gap-2 rounded-full border border-line bg-raised/60 px-3 py-1 text-[10.5px] text-faint">
            <span className="h-1.5 w-1.5 rounded-full bg-sage" />
            aura.app / today
          </div>
        </div>

        <div className="flex">
          {/* Mini sidebar */}
          <div className="hidden w-[168px] shrink-0 border-r border-line p-3 sm:block">
            <div className="mb-4 flex items-center gap-2 px-1">
              <Mascot colourway="violet" stage="colleague" size={26} />
              <div className="min-w-0">
                <div className="truncate text-[12px] font-semibold">Aura</div>
                <div className="truncate text-[9px] uppercase tracking-[0.14em] text-faint">
                  Colleague
                </div>
              </div>
            </div>
            <div className="mb-4 px-1">
              <div className="h-1 w-full overflow-hidden rounded-full bg-raised">
                <div className="h-full w-[68%] rounded-full bg-accent" />
              </div>
              <div className="mt-1 text-[9px] text-faint">68% to Chief of staff</div>
            </div>
            <div className="space-y-0.5">
              {RAIL.map(({ icon: Icon, label, active }) => (
                <div
                  key={label}
                  className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-[11.5px] ${
                    active ? "bg-raised/80 font-medium text-ink" : "text-faint"
                  }`}
                >
                  <Icon size={12} className={active ? "text-accent-soft" : ""} />
                  {label}
                </div>
              ))}
            </div>
          </div>

          {/* Main pane */}
          <div className="min-w-0 flex-1 p-4 sm:p-5">
            <div className="mb-4 flex items-center gap-2">
              <Radio size={11} className="text-accent-soft" />
              <span className="label">Overnight · while you slept</span>
              <span className="chip ml-auto border-sage/35 bg-sage/12 text-sage">
                6 skills ran
              </span>
            </div>

            <div className="space-y-0.5">
              {LOG.map(([code, text, at]) => (
                <div
                  key={code}
                  className="flex items-baseline gap-2 rounded-lg px-1.5 py-1 transition-colors hover:bg-raised/50"
                >
                  <span className="skill-line skill-code shrink-0">[SKILL·{code}]</span>
                  <span className="skill-line min-w-0 flex-1 truncate text-ink/80">
                    {text}
                  </span>
                  <span className="skill-line hidden shrink-0 text-faint sm:block">
                    {at}
                  </span>
                </div>
              ))}
            </div>

            <div className="mt-4 grid gap-2.5 sm:grid-cols-3">
              {[
                { k: "Inbox", v: "3 left", tone: "text-accent-soft" },
                { k: "Conflicts", v: "1 open", tone: "text-amber" },
                { k: "Commitments", v: "4 new", tone: "text-teal" },
              ].map((s) => (
                <div key={s.k} className="panel-raised px-3 py-2.5">
                  <div className="text-[9.5px] uppercase tracking-[0.14em] text-faint">
                    {s.k}
                  </div>
                  <div className={`text-[15px] font-semibold ${s.tone}`}>{s.v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
