import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import Mascot from "@/components/Mascot";
import LandingNav from "@/components/landing/LandingNav";
import HeroPreview from "@/components/landing/HeroPreview";
import LogoMarquee from "@/components/landing/LogoMarquee";
import Bento from "@/components/landing/Bento";
import Pricing from "@/components/landing/Pricing";
import Faq from "@/components/landing/Faq";
import DownloadCta from "@/components/landing/DownloadCta";
import GetAura from "@/components/landing/GetAura";
import OnYourComputer from "@/components/landing/OnYourComputer";
import DesktopHome from "@/components/landing/DesktopHome";
import { SkillCount } from "@/components/landing/SkillCount";
import { REPO_URL } from "@/lib/downloads";

// `edge` keeps the divider rules static so Tailwind can see every class.
//
// These are claims about the product, so they have to stay true. Sources:
//   skills   → GET /api/skills/catalog (backend/app/agent/skills.py CATALOG)
//   channels → backend/app/services/channels.py KINDS, available ones only
//   modes    → the three permission modes: Ask first, Auto, Skip approvals
const STATS = [
  { value: <SkillCount />, label: "Named skills", edge: "" },
  { value: "5", label: "Ways to reach it", edge: "border-l border-line" },
  {
    value: "3",
    label: "Permission modes",
    edge: "border-t border-line sm:border-t-0 sm:border-l",
  },
  {
    value: "0",
    label: "Keys needed to start",
    edge: "border-l border-t border-line sm:border-t-0",
  },
];

const PILLARS = [
  {
    tag: "Systems",
    title: "You assign the systems",
    body: "Mail, calendar, files, the web: AURA only uses the systems you have assigned to it, and nothing else.",
  },
  {
    tag: "Control",
    title: "You set the permission mode",
    body: "Choose Ask first, Auto or Skip approvals, and add rules for what to block, ask about or allow. Only you can change them. AURA can never change its own.",
  },
  {
    tag: "Follow-through",
    title: "It carries tasks through",
    body: "Give it a task and it plans, acts, checks its own work and reports back. It asks only when something is unclear or risky.",
  },
  {
    tag: "Memory",
    title: "It remembers how you like it done",
    body: "AURA keeps a record of past tasks and how you like each kind of task handled, and uses both the next time something similar comes up.",
  },
];

export default function Landing() {
  // Inside the desktop app this page is a doorway into the app, not an advert
  // for it. The value is fixed at build time, so the hosted build carries none
  // of this and the desktop build none of the rest.
  if (process.env.NEXT_PUBLIC_AURA_DESKTOP === "1") return <DesktopHome />;

  return (
    <div className="relative overflow-hidden">
      <LandingNav />

      {/* ---------------------------------------------------------------- Hero */}
      <section className="relative px-6 pb-20 pt-36 sm:pt-44">
        <div
          className="aurora-spot left-1/2 top-[-120px] h-[520px] w-[860px] -translate-x-1/2"
          style={{ background: "rgb(var(--accent) / 0.32)" }}
        />
        <div
          className="aurora-spot right-[6%] top-10 h-[360px] w-[360px] animate-float"
          style={{ background: "rgb(var(--c-azure) / 0.3)" }}
        />

        <div className="relative mx-auto max-w-3xl text-center">
          <a href="#download" className="pill mb-7 animate-fade-up hover:text-ink">
            <Sparkles size={12} className="text-accent-soft" />
            Now on Windows, Mac and Linux
            <ArrowRight size={12} className="text-faint" />
          </a>

          <h1
            className="display text-shine mb-6 animate-fade-up text-[46px] sm:text-[76px]"
            style={{ animationDelay: "60ms" }}
          >
            Your senior hire,
            <br />
            <span className="text-accent-gradient">working from day one.</span>
          </h1>

          <p
            className="mx-auto mb-9 max-w-xl animate-fade-up text-[16px] leading-relaxed text-muted"
            style={{ animationDelay: "120ms" }}
          >
            Give AURA a task and it plans, acts, checks and reports back, using only the
            systems you assign and the permission mode you choose. The desktop app can also
            work on your computer, in the folders and apps you allow.
          </p>

          <div className="animate-fade-up" style={{ animationDelay: "180ms" }}>
            <DownloadCta />
          </div>
        </div>

        <div
          className="relative mx-auto mt-16 max-w-5xl animate-fade-up"
          style={{ animationDelay: "280ms" }}
        >
          <HeroPreview />
        </div>
      </section>

      {/* --------------------------------------------------------------- Stats */}
      <section className="relative mx-auto max-w-5xl px-6 py-10">
        <div className="panel hairline-top grid grid-cols-2 overflow-hidden sm:grid-cols-4">
          {STATS.map((s) => (
            <div key={s.label} className={`px-6 py-7 text-center ${s.edge}`}>
              <div className="display text-shine mb-1 text-[34px]">{s.value}</div>
              <div className="text-[12px] tracking-wide text-faint">{s.label}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------- Logo strip */}
      <section className="mx-auto max-w-5xl px-6 py-12">
        <p className="mb-7 text-center text-[12.5px] text-faint">
          Works across the tools you already live in
        </p>
        <LogoMarquee />
      </section>

      {/* ------------------------------------------------------------ How it works */}
      <section id="how" className="relative mx-auto max-w-6xl scroll-mt-24 px-6 py-24">
        <div
          className="aurora-spot left-0 top-20 h-[380px] w-[380px]"
          style={{ background: "rgb(var(--c-violet) / 0.24)" }}
        />

        <div className="relative mb-12 max-w-2xl">
          <span className="pill mb-5">How it works</span>
          <h2 className="display text-shine mb-4 text-[34px] sm:text-[46px]">
            You stay in charge.
            <br />
            It does the work.
          </h2>
          <p className="text-[15px] leading-relaxed text-muted">
            There is no setup phase and nothing to train first. AURA starts working from
            your first message, inside the limits you set.
          </p>
        </div>

        <ol className="relative grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {PILLARS.map((s, i) => (
            <li key={s.tag} className="glass group">
              <div className="p-7">
                <div className="mb-5 flex items-center justify-between">
                  <span className="chip">{s.tag}</span>
                  <span className="font-mono text-[11px] text-faint">0{i + 1}</span>
                </div>
                <h3 className="mb-2 text-[17px] font-semibold tracking-tight">{s.title}</h3>
                <p className="text-[13.5px] leading-relaxed text-muted">{s.body}</p>
                <div className="mt-6 h-px w-full bg-gradient-to-r from-accent/50 via-accent/10 to-transparent" />
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* ------------------------------------------------------------- Skills */}
      <Bento />

      {/* ------------------------------------------------- On your computer */}
      <OnYourComputer />

      {/* ----------------------------------------------------------- Download */}
      <GetAura />

      {/* ------------------------------------------------------------ Pricing */}
      <Pricing />

      {/* ---------------------------------------------------------------- FAQ */}
      <Faq />

      {/* ----------------------------------------------------------- Final CTA */}
      <section className="relative mx-auto max-w-5xl px-6 pb-24">
        <div className="glass relative overflow-hidden text-center">
          <div
            className="aurora-spot left-1/2 top-0 h-[300px] w-[600px] -translate-x-1/2 animate-glow-pulse"
            style={{ background: "rgb(var(--accent) / 0.45)" }}
          />
          <div className="relative px-8 py-16">
            <Mascot colourway="violet" size={64} className="mx-auto mb-6" />
            <h2 className="display text-shine mx-auto mb-4 max-w-lg text-[30px] sm:text-[40px]">
              Hand it the first task today.
              <br />
              Get the result back, checked.
            </h2>
            <p className="mx-auto mb-8 max-w-md text-[14.5px] leading-relaxed text-muted">
              No onboarding and no training period. Describe what you need, and AURA gets to
              work.
            </p>
            <DownloadCta compact />
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------- Footer */}
      <footer className="relative border-t border-line px-6 pb-[max(2.5rem,env(safe-area-inset-bottom))] pt-10">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-5 text-center sm:flex-row sm:justify-between sm:text-left">
          <div className="flex items-center gap-2.5">
            <Mascot colourway="violet" size={26} />
            <span className="text-[14px] font-semibold tracking-tight">AURA</span>
          </div>
          <p className="max-w-md text-[12.5px] leading-relaxed text-faint">
            A personal employee that works from your first message, in your browser or on
            your own computer.
          </p>
          <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-5 text-[12.5px] text-faint">
            <a href="#download" className="transition hover:text-muted">
              Download
            </a>
            <a href="#skills" className="transition hover:text-muted">
              Skills
            </a>
            <a href="#pricing" className="transition hover:text-muted">
              Pricing
            </a>
            <a href={REPO_URL} className="transition hover:text-muted">
              Source code
            </a>
            <Link href="/login" className="transition hover:text-muted">
              Sign in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
