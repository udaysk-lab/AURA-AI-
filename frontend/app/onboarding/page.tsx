"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, Check } from "lucide-react";
import Mascot from "@/components/Mascot";
import { Spinner } from "@/components/ui";
import { AutonomyTier, Colourway, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";

const COLOURWAYS: Colourway[] = ["teal", "amber", "rose", "violet", "sage"];

const PERSONALITIES = [
  { key: "concise", label: "Concise", blurb: "Short. Factual. No small talk." },
  { key: "warm", label: "Warm", blurb: "Friendly and human, without the gush." },
  { key: "dry", label: "Dry", blurb: "Understated, lightly wry." },
  { key: "formal", label: "Formal", blurb: "Precise and professional throughout." },
  { key: "encouraging", label: "Encouraging", blurb: "Leads with progress, then problems." },
];

const TIERS: Array<{ key: AutonomyTier; label: string; blurb: string }> = [
  { key: "strict", label: "Strict", blurb: "Asks before every action." },
  { key: "conservative", label: "Conservative", blurb: "Handles the safe stuff alone." },
  { key: "relaxed", label: "Relaxed", blurb: "Only checks in for big decisions." },
  { key: "full", label: "Full access", blurb: "Complete autonomy. Nothing is held." },
];

const SUGGESTED_GOALS = [
  "Get to inbox zero every day",
  "Protect my mornings for deep work",
  "Never miss a follow-up",
  "Keep my calendar free of conflicts",
  "Stay on top of investor comms",
  "Prepare me before every meeting",
];

export default function OnboardingPage() {
  const router = useRouter();
  const { user, loading } = useAuth();
  const { assistant, refresh } = useAssistant();

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("Aura");
  const [avatar, setAvatar] = useState<Colourway>("teal");
  const [personality, setPersonality] = useState("concise");
  const [pronoun, setPronoun] = useState("it");
  const [role, setRole] = useState("");
  const [about, setAbout] = useState("");
  const [goals, setGoals] = useState<string[]>([]);
  const [tier, setTier] = useState<AutonomyTier>("conservative");

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  useEffect(() => {
    if (assistant?.onboarded) router.replace("/dashboard");
  }, [assistant, router]);

  const toggleGoal = (goal: string) =>
    setGoals((prev) =>
      prev.includes(goal) ? prev.filter((g) => g !== goal) : [...prev, goal]
    );

  const finish = async () => {
    setBusy(true);
    try {
      await api.hatch({
        name: name.trim() || "Aura",
        personality,
        avatar,
        pronoun,
        goals,
        role,
        about,
        autonomy_level: tier,
      });
      await refresh();
      router.push("/dashboard");
    } catch {
      setBusy(false);
    }
  };

  const steps = [
    {
      title: "Give them a name",
      blurb: "You're not configuring a tool. You're hiring someone.",
      body: (
        <div className="space-y-6">
          <div className="flex justify-center">
            <Mascot colourway={avatar} stage="stranger" size={104} />
          </div>
          <div>
            <label className="label mb-1.5 block">Name</label>
            <input
              className="input text-center text-lg"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={40}
              autoFocus
            />
          </div>
          <div>
            <label className="label mb-2 block">Look</label>
            <div className="flex justify-center gap-3">
              {COLOURWAYS.map((c) => (
                <button
                  key={c}
                  onClick={() => setAvatar(c)}
                  className={`rounded-2xl p-1.5 transition ${
                    avatar === c ? "ring-2 ring-accent ring-offset-2 ring-offset-canvas" : ""
                  }`}
                >
                  <Mascot colourway={c} stage="stranger" size={40} />
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="label mb-2 block">Refer to them as</label>
            <div className="flex justify-center gap-2">
              {["it", "she", "he", "they"].map((p) => (
                <button
                  key={p}
                  onClick={() => setPronoun(p)}
                  className={`rounded-full border px-3.5 py-1.5 text-[13px] transition ${
                    pronoun === p
                      ? "border-accent/60 bg-accent-dim text-accent-soft shadow-glow-sm"
                      : "border-line text-muted hover:border-accent/30 hover:text-ink"
                  }`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: "How should they talk to you?",
      blurb: "This shapes every message they write, including drafts sent as you.",
      body: (
        <div className="space-y-2">
          {PERSONALITIES.map((p) => (
            <button
              key={p.key}
              onClick={() => setPersonality(p.key)}
              className={`w-full rounded-2xl border p-4 text-left transition ${
                personality === p.key
                  ? "border-accent/50 bg-accent-dim shadow-glow-sm"
                  : "border-line bg-raised/30 hover:border-accent/25 hover:bg-raised/60"
              }`}
            >
              <div className="mb-0.5 text-[14px] font-medium">{p.label}</div>
              <div className="text-[12.5px] text-muted">{p.blurb}</div>
            </button>
          ))}
        </div>
      ),
    },
    {
      title: "Tell them about you",
      blurb: "Everything here becomes memory immediately, so your first conversation isn't from scratch.",
      body: (
        <div className="space-y-4">
          <div>
            <label className="label mb-1.5 block">What do you do?</label>
            <input
              className="input"
              placeholder="Founder at a seed-stage fintech"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            />
          </div>
          <div>
            <label className="label mb-1.5 block">Anything they should know?</label>
            <textarea
              className="input min-h-[110px]"
              placeholder="I'm raising a Series A. I write in British English and hate exclamation marks. Fridays are for deep work."
              value={about}
              onChange={(e) => setAbout(e.target.value)}
            />
          </div>
          <div>
            <label className="label mb-2 block">What should they take off your plate?</label>
            <div className="flex flex-wrap gap-2">
              {SUGGESTED_GOALS.map((g) => (
                <button
                  key={g}
                  onClick={() => toggleGoal(g)}
                  className={`rounded-full border px-3 py-1.5 text-[12.5px] transition ${
                    goals.includes(g)
                      ? "border-accent/60 bg-accent-dim text-accent-soft shadow-glow-sm"
                      : "border-line text-muted hover:border-accent/30 hover:text-ink"
                  }`}
                >
                  {goals.includes(g) && <Check size={11} className="mr-1 inline" />}
                  {g}
                </button>
              ))}
            </div>
          </div>
        </div>
      ),
    },
    {
      title: "How much rope?",
      blurb: "You can change this at any time, and grant one-off exceptions as you go.",
      body: (
        <div className="space-y-2">
          {TIERS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTier(t.key)}
              className={`w-full rounded-2xl border p-4 text-left transition ${
                tier === t.key
                  ? "border-accent/50 bg-accent-dim shadow-glow-sm"
                  : "border-line bg-raised/30 hover:border-accent/25 hover:bg-raised/60"
              }`}
            >
              <div className="mb-0.5 text-[14px] font-medium">{t.label}</div>
              <div className="text-[12.5px] text-muted">{t.blurb}</div>
            </button>
          ))}
          <p className="pt-2 text-[12px] leading-relaxed text-faint">
            Below <span className="text-muted">Full access</span>, anything irreversible —
            sending mail, deleting events — is held for your approval no matter what else
            they&apos;re allowed to do.
          </p>
        </div>
      ),
    },
  ];

  const current = steps[step];
  const isLast = step === steps.length - 1;

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-muted">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-6 py-12">
      <div
        className="aurora-spot left-1/2 top-[-12%] h-[520px] w-[760px] -translate-x-1/2"
        style={{ background: "rgb(var(--accent) / 0.32)" }}
      />
      <div
        className="aurora-spot bottom-[-12%] left-[8%] h-[340px] w-[340px] animate-float"
        style={{ background: "rgb(var(--c-azure) / 0.24)" }}
      />

      <div className="glass relative w-full max-w-lg p-7 sm:p-9">
        <div className="mb-7 flex items-center gap-1.5">
          {steps.map((_, i) => (
            <div
              key={i}
              className={`h-1 flex-1 rounded-full transition-all duration-500 ${
                i <= step
                  ? "bg-gradient-to-r from-accent to-accent-soft shadow-glow-sm"
                  : "bg-raised"
              }`}
            />
          ))}
        </div>

        <div key={step} className="animate-fade-up">
          <span className="label mb-3 block">
            Step {step + 1} of {steps.length}
          </span>
          <h1 className="display text-shine mb-2 text-[27px]">{current.title}</h1>
          <p className="mb-7 text-[13.5px] leading-relaxed text-muted">{current.blurb}</p>
          {current.body}
        </div>

        <div className="mt-8 flex items-center justify-between">
          <button
            onClick={() => setStep((s) => Math.max(0, s - 1))}
            disabled={step === 0}
            className="btn-quiet"
          >
            <ArrowLeft size={14} /> Back
          </button>
          {isLast ? (
            <button onClick={finish} disabled={busy} className="btn-primary">
              {busy ? <Spinner /> : <Check size={15} />} Wake {name.trim() || "them"} up
            </button>
          ) : (
            <button onClick={() => setStep((s) => s + 1)} className="btn-primary">
              Continue <ArrowRight size={15} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
