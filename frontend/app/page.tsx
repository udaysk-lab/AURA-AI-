import Link from "next/link";
import { ArrowRight, PlayCircle, Sparkles } from "lucide-react";
import Mascot from "@/components/Mascot";
import LandingNav from "@/components/landing/LandingNav";
import HeroPreview from "@/components/landing/HeroPreview";
import LogoMarquee from "@/components/landing/LogoMarquee";
import Bento from "@/components/landing/Bento";
import Pricing from "@/components/landing/Pricing";
import Faq from "@/components/landing/Faq";

// `edge` keeps the divider rules static so Tailwind can see every class.
//
// These are claims about the product, so they have to stay true. Sources:
//   skills   → backend/app/agent/skills.py CATALOG
//   channels → backend/app/services/channels.py KINDS, available ones only
//   trust    → backend/app/agent/autonomy.py TIERS
// If you add a skill or a channel, update the number here in the same commit.
const STATS = [
  { value: "21", label: "Named skills", edge: "" },
  { value: "5", label: "Ways to reach it", edge: "border-l border-line" },
  {
    value: "4",
    label: "Levels of trust",
    edge: "border-t border-line sm:border-t-0 sm:border-l",
  },
  {
    value: "0",
    label: "Keys needed to start",
    edge: "border-l border-t border-line sm:border-t-0",
  },
];

const STAGES = [
  {
    stage: "stranger" as const,
    day: "Day 1",
    title: "Stranger",
    body: "It just met you. Eager, clueless. Tell it your role, your style, how you like things done.",
  },
  {
    stage: "acquaintance" as const,
    day: "Day 2",
    title: "Acquaintance",
    body: "It's picking up patterns. Gets things half-right, asks better questions. Correct it and the correction sticks.",
  },
  {
    stage: "colleague" as const,
    day: "Day 3",
    title: "Colleague",
    body: "It finishes your thought. Acts before you ask. You stop managing it and start relying on it.",
  },
];

export default function Landing() {
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
          <Link href="/login" className="pill mb-7 animate-fade-up hover:text-ink">
            <Sparkles size={12} className="text-accent-soft" />
            Skills v2 — all fourteen live
            <ArrowRight size={12} className="text-faint" />
          </Link>

          <h1
            className="display text-shine mb-6 animate-fade-up text-[46px] sm:text-[76px]"
            style={{ animationDelay: "60ms" }}
          >
            An assistant
            <br />
            <span className="text-accent-gradient">you raise.</span>
          </h1>

          <p
            className="mx-auto mb-9 max-w-xl animate-fade-up text-[16px] leading-relaxed text-muted"
            style={{ animationDelay: "120ms" }}
          >
            AURA starts as a stranger. It reads your inbox, holds your calendar,
            tracks what you owe people, and learns how you like things done — until
            you stop managing it and start relying on it.
          </p>

          <div
            className="flex animate-fade-up flex-wrap items-center justify-center gap-3"
            style={{ animationDelay: "180ms" }}
          >
            <Link href="/login" className="btn-primary !px-6 !py-2.5">
              Wake one up <ArrowRight size={15} />
            </Link>
            <Link href="#skills" className="btn-ghost !px-6 !py-2.5">
              <PlayCircle size={15} /> See it work
            </Link>
          </div>

          <p
            className="mt-5 animate-fade-up text-[12.5px] text-faint"
            style={{ animationDelay: "220ms" }}
          >
            Runs on realistic sample data out of the box. No setup, no card.
          </p>
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
        <p className="mb-7 text-center text-[12px] uppercase tracking-[0.2em] text-faint">
          Works across the tools you already live in
        </p>
        <LogoMarquee />
      </section>

      {/* -------------------------------------------------------------- Stages */}
      <section id="stages" className="relative mx-auto max-w-6xl px-6 py-24">
        <div
          className="aurora-spot left-0 top-20 h-[380px] w-[380px]"
          style={{ background: "rgb(var(--c-violet) / 0.24)" }}
        />

        <div className="relative mb-12 max-w-2xl">
          <span className="pill mb-5">The relationship</span>
          <h2 className="display text-shine mb-4 text-[34px] sm:text-[46px]">
            You raise them.
            <br />
            They grow with you.
          </h2>
          <p className="text-[15px] leading-relaxed text-muted">
            The stage isn&apos;t decorative. It&apos;s computed from how much
            you&apos;ve talked, how much it knows about you, and how much it has
            actually done.
          </p>
        </div>

        <div className="relative grid gap-4 sm:grid-cols-3">
          {STAGES.map((s, i) => (
            <div key={s.stage} className="glass group">
              <div className="p-7">
                <div className="mb-5 flex items-center justify-between">
                  <Mascot colourway="violet" stage={s.stage} size={56} />
                  <span className="font-mono text-[11px] text-faint">
                    0{i + 1}
                  </span>
                </div>
                <div className="label mb-2">{s.day}</div>
                <h3 className="mb-2 text-[17px] font-semibold tracking-tight">
                  {s.title}
                </h3>
                <p className="text-[13.5px] leading-relaxed text-muted">{s.body}</p>
                <div className="mt-6 h-px w-full bg-gradient-to-r from-accent/50 via-accent/10 to-transparent" />
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ------------------------------------------------------------- Skills */}
      <Bento />

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
            <Mascot
              colourway="violet"
              stage="chief_of_staff"
              size={64}
              className="mx-auto mb-6"
            />
            <h2 className="display text-shine mx-auto mb-4 max-w-lg text-[30px] sm:text-[40px]">
              Wake one up tonight.
              <br />
              Rely on it by Friday.
            </h2>
            <p className="mx-auto mb-8 max-w-md text-[14.5px] leading-relaxed text-muted">
              It boots as a stranger with sample data. By the end of the week it
              knows how you work — and it will have already done some of it.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-3">
              <Link href="/login" className="btn-primary !px-6 !py-2.5">
                Get started <ArrowRight size={15} />
              </Link>
              <Link href="#pricing" className="btn-ghost !px-6 !py-2.5">
                Compare plans
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------------- Footer */}
      <footer className="relative border-t border-line px-6 py-10">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-5 text-center sm:flex-row sm:justify-between sm:text-left">
          <div className="flex items-center gap-2.5">
            <Mascot colourway="violet" stage="colleague" size={26} />
            <span className="text-[14px] font-semibold tracking-tight">AURA</span>
          </div>
          <p className="max-w-md text-[12.5px] leading-relaxed text-faint">
            Email, calendar, tasks, memory and automation — in one assistant that
            remembers.
          </p>
          <div className="flex items-center gap-5 text-[12.5px] text-faint">
            <a href="#skills" className="transition hover:text-muted">
              Skills
            </a>
            <a href="#pricing" className="transition hover:text-muted">
              Pricing
            </a>
            <Link href="/login" className="transition hover:text-muted">
              Sign in
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
