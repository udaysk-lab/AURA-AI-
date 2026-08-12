"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Check } from "lucide-react";

type Tier = {
  name: string;
  tagline: string;
  monthly: number | null;
  blurb: string;
  cta: string;
  features: string[];
  featured?: boolean;
};

const TIERS: Tier[] = [
  {
    name: "Solo",
    tagline: "Free forever",
    monthly: 0,
    blurb: "For anyone who wants to see what a raised assistant feels like.",
    cta: "Start free",
    features: [
      "6 core skills",
      "Email triage and drafting",
      "Calendar with conflict detection",
      "Memory up to 500 facts",
      "Ask-before-acting trust level",
    ],
  },
  {
    name: "Daily",
    tagline: "Most popular",
    monthly: 18,
    blurb: "The full skill set, running on a heartbeat while you're away.",
    cta: "Get started",
    featured: true,
    features: [
      "All 14 skills",
      "Overnight heartbeat and briefings",
      "Unlimited memory with consolidation",
      "All four trust levels",
      "Automations and schedules",
      "Plugin hub access",
    ],
  },
  {
    name: "Studio",
    tagline: "For teams",
    monthly: 49,
    blurb: "Shared context, per-person assistants, and an audit trail.",
    cta: "Talk to us",
    features: [
      "Everything in Daily",
      "One assistant per teammate",
      "Shared project memory",
      "Custom skills and private plugins",
      "Full action audit log",
      "Priority support",
    ],
  },
];

export default function Pricing() {
  const [yearly, setYearly] = useState(true);

  const price = (t: Tier) => {
    if (t.monthly === null) return "Custom";
    if (t.monthly === 0) return "Free";
    return `$${yearly ? Math.round(t.monthly * 0.8) : t.monthly}`;
  };

  return (
    <section id="pricing" className="relative mx-auto max-w-6xl px-6 py-24">
      <div
        className="aurora-spot left-1/2 top-10 h-[340px] w-[560px] -translate-x-1/2"
        style={{ background: "rgb(var(--accent) / 0.28)" }}
      />

      <div className="relative mb-12 text-center">
        <span className="pill mb-5">Pricing</span>
        <h2 className="display text-shine mb-3 text-[34px] sm:text-[44px]">
          Choose your plan
        </h2>
        <p className="mx-auto max-w-md text-[14.5px] leading-relaxed text-muted">
          Start free and keep the assistant you raised. Upgrade when you want it
          working through the night.
        </p>

        <div className="mt-7 inline-flex items-center gap-3">
          <span
            className={`text-[13px] transition ${!yearly ? "text-ink" : "text-faint"}`}
          >
            Monthly
          </span>
          <button
            role="switch"
            aria-checked={yearly}
            aria-label="Bill yearly"
            onClick={() => setYearly((v) => !v)}
            className={`relative h-6 w-11 shrink-0 rounded-full border border-line transition ${
              yearly ? "bg-accent shadow-glow-sm" : "bg-raised"
            }`}
          >
            <span
              className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow-sm transition-all ${
                yearly ? "left-[24px]" : "left-[3px]"
              }`}
            />
          </button>
          <span className={`text-[13px] transition ${yearly ? "text-ink" : "text-faint"}`}>
            Yearly
          </span>
          <span className="chip border-sage/35 bg-sage/12 text-sage">Save 20%</span>
        </div>
      </div>

      <div className="relative grid gap-5 lg:grid-cols-3">
        {TIERS.map((t) => {
          const body = (
            <div className="flex h-full flex-col p-7">
              <div className="mb-1 flex items-center justify-between gap-2">
                <h3 className="text-[15px] font-semibold">{t.name}</h3>
                {t.featured && (
                  <span className="chip border-accent/35 bg-accent-dim text-accent-soft">
                    {t.tagline}
                  </span>
                )}
              </div>
              <div className="mb-3 h-4">
                {!t.featured && <span className="label">{t.tagline}</span>}
              </div>

              <div className="mb-1 flex items-end gap-1.5">
                <span className="display text-[40px]">{price(t)}</span>
                {typeof t.monthly === "number" && t.monthly > 0 && (
                  <span className="mb-2 text-[13px] text-faint">/month</span>
                )}
              </div>
              <p className="mb-6 min-h-[42px] text-[13px] leading-relaxed text-muted">
                {t.blurb}
              </p>

              <ul className="mb-7 flex-1 space-y-2.5">
                {t.features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5 text-[13px] text-muted">
                    <span
                      className={`mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full ${
                        t.featured ? "bg-accent/20 text-accent-soft" : "bg-raised text-faint"
                      }`}
                    >
                      <Check size={10} strokeWidth={3} />
                    </span>
                    <span>{f}</span>
                  </li>
                ))}
              </ul>

              <Link
                href="/login"
                className={`w-full ${t.featured ? "btn-primary" : "btn-ghost"}`}
              >
                {t.cta} <ArrowRight size={14} />
              </Link>
            </div>
          );

          return t.featured ? (
            <div key={t.name} className="glow-card lg:-my-3">
              <div className="h-full">{body}</div>
            </div>
          ) : (
            <div key={t.name} className="glass">
              {body}
            </div>
          );
        })}
      </div>

      <p className="relative mt-8 text-center text-[12.5px] text-faint">
        Every plan runs on sample data out of the box. Connect your real accounts
        whenever you&apos;re ready.
      </p>
    </section>
  );
}
