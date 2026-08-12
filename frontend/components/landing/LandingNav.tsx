"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import Mascot from "@/components/Mascot";

const LINKS = [
  { href: "#stages", label: "How it grows" },
  { href: "#skills", label: "Skills" },
  { href: "#pricing", label: "Pricing" },
  { href: "#faq", label: "FAQ" },
];

/**
 * Floating glass navigation. Sits flush at the top of the hero and condenses
 * into a solid pill once the page scrolls, so it never fights the aurora.
 */
export default function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className="fixed inset-x-0 top-0 z-50 px-4 pt-4">
      <nav
        className={`mx-auto flex max-w-6xl items-center gap-3 rounded-full px-4 py-2.5 transition-all duration-300 ${
          scrolled
            ? "border border-line bg-canvas/70 shadow-soft backdrop-blur-2xl"
            : "border border-transparent"
        }`}
      >
        <Link href="/" className="flex items-center gap-2.5 pr-2">
          <Mascot colourway="violet" stage="colleague" size={30} />
          <span className="text-[15px] font-semibold tracking-tight">AURA</span>
        </Link>

        <div className="hidden flex-1 items-center justify-center gap-1 md:flex">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="rounded-full px-3.5 py-1.5 text-[13.5px] text-muted transition hover:bg-raised/60 hover:text-ink"
            >
              {l.label}
            </a>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-2 md:ml-0">
          <Link
            href="/login"
            className="hidden rounded-full px-3.5 py-1.5 text-[13.5px] text-muted transition hover:text-ink sm:block"
          >
            Sign in
          </Link>
          <Link href="/login" className="btn-primary !px-4 !py-1.5 text-[13px]">
            Get started
          </Link>
          <button
            onClick={() => setOpen((v) => !v)}
            className="rounded-full p-1.5 text-muted transition hover:text-ink md:hidden"
            aria-label="Toggle menu"
          >
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </nav>

      {open && (
        <div className="mx-auto mt-2 max-w-6xl animate-fade-up rounded-3xl border border-line bg-canvas/90 p-3 backdrop-blur-2xl md:hidden">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className="block rounded-2xl px-4 py-2.5 text-sm text-muted transition hover:bg-raised/60 hover:text-ink"
            >
              {l.label}
            </a>
          ))}
        </div>
      )}
    </header>
  );
}
