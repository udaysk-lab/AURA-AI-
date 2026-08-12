"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Sidebar, { MobileNav, MobileTopBar } from "@/components/Sidebar";
import CommandPalette from "@/components/CommandPalette";
import ErrorBoundary from "@/components/ErrorBoundary";
import { ToastProvider, useHotkey } from "@/components/Toast";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const { assistant, loading: assistantLoading } = useAssistant();
  const router = useRouter();

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);

  const togglePalette = useCallback(() => setPaletteOpen((v) => !v), []);
  useHotkey("k", togglePalette);

  useEffect(() => {
    if (loading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    // First run: hatch an assistant before anything else.
    if (!assistantLoading && assistant && !assistant.onboarded) {
      router.replace("/onboarding");
    }
  }, [loading, user, assistant, assistantLoading, router]);

  if (loading || !user) {
    return (
      <div className="flex h-screen items-center justify-center text-muted">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  return (
    <ToastProvider>
      <div className="relative flex h-screen overflow-hidden">
        {/* Faint blueprint grid — gives the glass panels something to float over */}
        <div className="pointer-events-none absolute inset-0 bg-grid-fade bg-grid opacity-60" />

        <Sidebar onOpenPalette={togglePalette} className="hidden lg:flex" />
        <div className="relative flex min-w-0 flex-1 flex-col">
          <MobileTopBar onOpenNav={() => setNavOpen(true)} onOpenPalette={togglePalette} />
          {/* overflow-x is pinned so the header aurora blooms can bleed past the
              content column without producing a horizontal scrollbar. */}
          <main className="flex-1 overflow-y-auto overflow-x-hidden">
            {/* Keyed on nothing so it resets when the user navigates: a broken
                screen shouldn't poison the rest of the session. */}
            <ErrorBoundary>{children}</ErrorBoundary>
          </main>
        </div>
      </div>

      <MobileNav open={navOpen} onClose={() => setNavOpen(false)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </ToastProvider>
  );
}
