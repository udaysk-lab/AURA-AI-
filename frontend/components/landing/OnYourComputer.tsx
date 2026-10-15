import {
  AppWindow,
  Ban,
  ClipboardList,
  Cpu,
  FolderOpen,
  Lock,
  MousePointerClick,
  Power,
  ShieldCheck,
  Smartphone,
  Terminal,
} from "lucide-react";

/**
 * What the desktop app adds, and what holds it back. Both halves are true to
 * the code: the capabilities are the seven device skills (DV01–DV07 in
 * backend/app/agent/skills.py) and the limits are the five gates in DEVICE.md.
 */
const CAN = [
  { icon: Terminal, title: "Run commands", body: "Anything you'd type in a terminal — git, builds, scripts, installs." },
  { icon: FolderOpen, title: "Work with your files", body: "Find, read, write, move and tidy them, inside the folders you allow." },
  { icon: AppWindow, title: "Open and close apps", body: "Launch what you name; close what's stuck." },
  { icon: MousePointerClick, title: "See and use the screen", body: "For programs with no command line: it looks, then clicks and types." },
  { icon: Cpu, title: "Watch the system", body: "Disk, memory, battery and the processes eating them." },
  { icon: ClipboardList, title: "Clipboard and alerts", body: "Copy results for you, and tap you with a notification when it's done." },
];

const LIMITS = [
  { icon: ShieldCheck, title: "Your trust level", body: "Below full trust, a command, a click or a keystroke waits for your yes." },
  { icon: FolderOpen, title: "A folder fence", body: "File tools stay in the folders you allow, away from keys, credentials and startup items." },
  { icon: Ban, title: "A blocklist", body: "Commands you never want run are refused, even after approval." },
  { icon: Lock, title: "Only you", body: "It answers to this computer alone, unless you pair your phone." },
  { icon: Power, title: "Off in the cloud", body: "None of this exists in the browser version. It can't reach your computer." },
];

export default function OnYourComputer() {
  return (
    <section id="computer" className="relative mx-auto max-w-6xl scroll-mt-24 px-6 py-24">
      <div
        className="aurora-spot right-[4%] top-24 h-[380px] w-[380px]"
        style={{ background: "rgb(var(--c-teal) / 0.16)" }}
      />

      <div className="relative grid gap-12 lg:grid-cols-[1fr_1.05fr] lg:gap-16">
        <div className="min-w-0">
          <h2 className="display text-shine mb-4 text-[34px] sm:text-[44px]">
            On your computer,
            <br />
            it has hands.
          </h2>
          <p className="mb-9 max-w-lg text-[15px] leading-relaxed text-muted">
            The desktop app does more than talk about your work. Ask it to clear space, rename
            a folder of photos or open last week&apos;s report, and it does it — on your
            machine, as you.
          </p>
          <div className="grid gap-x-6 gap-y-5 sm:grid-cols-2">
            {CAN.map(({ icon: Icon, title, body }) => (
              <div key={title} className="flex gap-3">
                <Icon size={16} className="mt-0.5 shrink-0 text-accent-soft" />
                <div>
                  <div className="text-[13.5px] font-medium">{title}</div>
                  <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">{body}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0 space-y-5">
          {/* What asking looks like: the real card, in miniature */}
          <div className="glass p-6" aria-hidden>
            <div className="mb-3 flex items-center gap-2">
              <span className="chip border-accent/25 bg-accent-dim text-accent-soft">
                <span className="font-mono text-[10px] opacity-80">DV01</span> Command Line
              </span>
              <span className="chip border-amber/35 bg-amber/14 text-amber">Needs your say-so</span>
            </div>
            <p className="mb-2 text-[12.5px] text-muted">Run on this computer:</p>
            <pre className="mb-1 overflow-x-auto rounded-xl border border-line bg-canvas/70 px-4 py-3 font-mono text-[12.5px] text-ink">
              du -sh ~/Downloads/* | sort -rh | head
            </pre>
            <p className="mb-4 text-[11.5px] text-faint">in ~/Downloads</p>
            <div className="flex flex-wrap gap-2">
              <span className="btn-primary pointer-events-none py-1.5 text-[12px]">Allow once</span>
              <span className="btn-ghost pointer-events-none py-1.5 text-[12px]">10 min</span>
              <span className="btn-ghost pointer-events-none py-1.5 text-[12px]">Always</span>
              <span className="btn-quiet pointer-events-none py-1.5 text-[12px]">Don&apos;t</span>
            </div>
          </div>

          <div className="panel p-6">
            <h3 className="mb-4 text-[15px] font-semibold">What keeps it in check</h3>
            <ul className="space-y-3.5">
              {LIMITS.map(({ icon: Icon, title, body }) => (
                <li key={title} className="flex gap-3">
                  <Icon size={15} className="mt-0.5 shrink-0 text-faint" />
                  <p className="text-[12.5px] leading-relaxed text-muted">
                    <span className="font-medium text-ink">{title}.</span> {body}
                  </p>
                </li>
              ))}
            </ul>
            <p className="mt-5 flex items-start gap-2 border-t border-line pt-4 text-[12px] leading-relaxed text-faint">
              <Smartphone size={13} className="mt-0.5 shrink-0" />
              Away from your desk? Pair your phone and approve from there.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
