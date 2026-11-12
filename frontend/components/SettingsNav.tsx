"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Brain, Laptop, Puzzle, ShieldCheck, SlidersHorizontal, Timer } from "lucide-react";
import { api } from "@/lib/api";

/**
 * The sections of Settings. General and Permissions live under /settings; the
 * rest are the screens that used to sit elsewhere, listed here so they are
 * found in one place. On a phone it is a row that scrolls sideways.
 */
export default function SettingsNav() {
  const pathname = usePathname().replace(/\/+$/, "") || "/";
  const [deviceControl, setDeviceControl] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void api
      .authConfig()
      .then((c) => !cancelled && setDeviceControl(c.device_control && !c.remote_client))
      .catch(() => {
        /* the item just stays hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const items = [
    { href: "/settings", label: "General", icon: SlidersHorizontal },
    { href: "/settings/permissions", label: "Permissions", icon: ShieldCheck },
    { href: "/hub", label: "Systems", icon: Puzzle },
    ...(deviceControl ? [{ href: "/device", label: "This computer", icon: Laptop }] : []),
    { href: "/memory", label: "Memory", icon: Brain },
    { href: "/schedules", label: "Scheduled", icon: Timer },
  ];

  return (
    <nav
      aria-label="Settings sections"
      className="shrink-0 border-b border-line px-4 pt-3 lg:sticky lg:top-0 lg:w-56 lg:border-b-0 lg:px-0 lg:pl-8 lg:pt-8"
    >
      <ul className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
        {items.map(({ href, label, icon: Icon }) => {
          const active = pathname === href;
          return (
            <li key={href} className="shrink-0">
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={`flex min-h-[44px] items-center gap-2.5 rounded-xl border px-3 text-[13px] transition ${
                  active
                    ? "border-accent/25 bg-accent/12 font-medium text-ink shadow-glow-sm"
                    : "border-transparent text-muted hover:bg-raised/60 hover:text-ink"
                }`}
              >
                <Icon size={15} className={active ? "text-accent-soft" : ""} aria-hidden />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
