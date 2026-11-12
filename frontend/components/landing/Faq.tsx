"use client";

import { useState } from "react";
import { Plus } from "lucide-react";

const ITEMS = [
  {
    q: "What does AURA do differently from a chatbot?",
    a: "It works through tasks. You describe what you need; it plans the steps, does them with the systems you have assigned, checks the result and reports back. It asks you only when a task is unclear or risky. There is no training period: it starts working from your first message.",
  },
  {
    q: "Can it send email or delete things without asking?",
    a: "Only if you let it. You choose a permission mode. In Ask first, anything that changes something waits for your approval. In Auto, each such action goes to a separate safety check instead. In Skip approvals, nothing waits except actions that always need you. Rules let you block, ask about or allow specific tools, and a block holds in every mode. Only you can change the mode or the rules; AURA cannot.",
  },
  {
    q: "What are skills, and why can I switch them off?",
    a: "Named capabilities, each with its own code — twenty-one in the browser, and seven more in the desktop app for working your computer. Turning one off doesn't hide a button: the assistant genuinely loses that ability, and will tell you so when it can't help.",
  },
  {
    q: "What does the desktop app add?",
    a: "Everything in the browser version, running on your own machine — plus the ability to work on the computer itself, in the folders and apps you allow: run commands, find and edit files, open apps, check the system and, for programs with no command line, see the screen and use the mouse and keyboard. Your chats and memory stay on your computer.",
  },
  {
    q: "Is it safe to let an assistant use my computer?",
    a: "It's built to ask. In the default mode, running a command or using the mouse and keyboard waits for your approval, showing exactly what it will do. File tools are fenced to the folders you allow, and never touch the folders that hold your keys and credentials, or anything that runs at startup. Commands you never want run can be refused outright, and only your computer — and phones you pair — can reach it.",
  },
  {
    q: "Why does Windows or macOS warn me when I open it?",
    a: "Because the app isn't code-signed yet, and signing certificates cost money a free project doesn't have. On Windows choose More info, then Run anyway. On a Mac, right-click the app and choose Open. The source code and SHA-256 checksums for every download are public, so you can check what you're running.",
  },
  {
    q: "Do I need an API key?",
    a: "Not to start: without one, AURA runs a limited demo that understands a few kinds of request. For the real assistant, paste a Claude or OpenAI key in Settings → AI model — or point it at a free model running on your own machine with Ollama or LM Studio.",
  },
  {
    q: "What does it do while I'm not there?",
    a: "On a heartbeat it triages new mail, captures commitments you made in replies, preps context for your next meeting, catches double-bookings and flags what's slipping. You wake up to a briefing, not a backlog.",
  },
  {
    q: "How does the memory work?",
    a: "AURA keeps a record of each task you assign, and remembers how you like each kind of task done. Before it starts something similar, it looks back at both. You can view, edit and delete what it remembers.",
  },
  {
    q: "Do I need to connect anything to try it?",
    a: "No. You can start with sample data and see how it works straight away. Assign Gmail, Calendar and the other systems whenever you want it operating on the real thing.",
  },
];

export default function Faq() {
  const [open, setOpen] = useState<number | null>(0);

  return (
    <section id="faq" className="mx-auto max-w-3xl scroll-mt-24 px-6 py-24">
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
