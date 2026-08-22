"use client";

import { useState } from "react";
import { Plus } from "lucide-react";

const ITEMS = [
  {
    q: "What does “raising” an assistant actually mean?",
    a: "Its relationship stage is computed, not cosmetic — from how much you've talked to it, how much it has learned about you, and how much work it has genuinely completed. A stranger asks about everything. A colleague acts and tells you afterwards.",
  },
  {
    q: "Can it send email or delete things without asking?",
    a: "Only if you let it. There are four trust levels. Below the top one, anything irreversible — sending mail, deleting an event, spending money — waits for your approval. You can grant an exception once, for ten minutes, or permanently.",
  },
  {
    q: "What are skills, and why can I switch them off?",
    a: "Twenty-one named capabilities, each with its own code. Turning one off doesn't hide a button — the assistant genuinely loses that ability, and will tell you so when it can't help.",
  },
  {
    q: "What does it do while I'm not there?",
    a: "On a heartbeat it triages new mail, captures commitments you made in replies, preps context for your next meeting, catches double-bookings and flags what's slipping. You wake up to a briefing, not a backlog.",
  },
  {
    q: "How does the memory work?",
    a: "Preferences, projects, people and decisions are stored and retrieved by meaning rather than keyword, then periodically consolidated — duplicates merged, stale facts retired — so it gets sharper over time instead of just bigger.",
  },
  {
    q: "Do I need to connect anything to try it?",
    a: "No. It boots with realistic sample data so you can see a full day's work immediately. Connect Gmail, Calendar and the rest whenever you want it operating on the real thing.",
  },
];

export default function Faq() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section id="faq" className="mx-auto max-w-3xl px-6 py-24">
      <div className="mb-10 text-center">
        <span className="pill mb-5">Answers</span>
        <h2 className="display text-shine text-[32px] sm:text-[40px]">
          Frequently asked questions
        </h2>
      </div>

      <div className="space-y-2.5">
        {ITEMS.map((item, i) => {
          const isOpen = open === i;
          return (
            <div
              key={item.q}
              className={`panel overflow-hidden transition-colors ${
                isOpen ? "border-accent/30" : ""
              }`}
            >
              <button
                onClick={() => setOpen(isOpen ? null : i)}
                aria-expanded={isOpen}
                className="flex w-full items-center gap-4 px-5 py-4 text-left"
              >
                <span className="flex-1 text-[14.5px] font-medium">{item.q}</span>
                <Plus
                  size={16}
                  className={`shrink-0 transition-transform duration-300 ${
                    isOpen ? "rotate-45 text-accent-soft" : "text-faint"
                  }`}
                />
              </button>
              <div
                className="grid transition-all duration-300 ease-out"
                style={{ gridTemplateRows: isOpen ? "1fr" : "0fr" }}
              >
                <div className="overflow-hidden">
                  <p className="px-5 pb-5 text-[13.5px] leading-relaxed text-muted">
                    {item.a}
                  </p>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
