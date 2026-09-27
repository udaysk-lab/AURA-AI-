"use client";

import { useCallback, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { RefreshCw, WifiOff } from "lucide-react";
import Sidebar, { MobileNav, MobileTopBar } from "@/components/Sidebar";
import CommandPalette from "@/components/CommandPalette";
import ErrorBoundary from "@/components/ErrorBoundary";
import { ToastProvider, useHotkey } from "@/components/Toast";
import { Spinner } from "@/components/ui";
import { getToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAssistant } from "@/lib/assistant";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, error, refresh, logout } = useAuth();
  const { assistant, loading: assistantLoading } = useAssistant();
  const router = useRouter();
  const pathname = usePathname();

  const [paletteOpen, setPaletteOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [retrying, setRetrying] = useState(false);

  const togglePalette = useCallback(() => setPaletteOpen((v) => !v), []);
  useHotkey("k", togglePalette);

  // A token the server couldn't check (API down, slow, rate-limited) is not a
  // signed-out user: offer a retry rather than the sign-in form.
  const unreachable = !loading && !user && !!error && !!getToken();

  useEffect(() => {
    if (loading || unreachable) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    // First run: hatch an assistant before anything else.
    if (!assistantLoading && assistant && !assistant.onboarded) {
      router.replace("/onboarding");
    }
  }, [loading, unreachable, user, assistant, assistantLoading, router]);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => setNavOpen(false), [pathname]);

  if (unreachable) {
    return (
      <div className="flex h-dvh items-center justify-center p-6">
        <div className="panel max-w-sm p-6 text-center">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-amber/12 text-amber">
            <WifiOff size={20} />
          </div>
          <h1 className="mb-1.5 text-[16px] font-semibold">Can&apos;t reach AURA right now</h1>
          <p className="mb-4 text-[13px] leading-relaxed text-muted">{error}</p>
          <div className="flex justify-center gap-2">
            <button onClick={logout} className="btn-ghost">
              Sign out
            </button>
            <button
              onClick={async () => {
                setRetrying(true);
                await refresh();
                setRetrying(false);
              }}
              disabled={retrying}
              className="btn-primary"
            >
              {retrying ? <Spinner /> : <RefreshCw size={14} />} Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (loading || !user || (assistantLoading && !assistant)) {
    return (
      <div className="flex h-dvh items-center justify-center text-muted">
        <Spinner className="h-5 w-5" />
      </div>
    );
  }

  return (
    <ToastProvider>
      <div className="relative flex h-dvh overflow-hidden">
        {/* Faint blueprint grid — gives the glass panels something to float over */}
        <div className="pointer-events-none absolute inset-0 bg-grid-fade bg-grid opacity-60" />

        <Sidebar onOpenPalette={togglePalette} className="hidden lg:flex" />
        <div className="relative flex min-w-0 flex-1 flex-col">
          <MobileTopBar onOpenNav={() => setNavOpen(true)} onOpenPalette={togglePalette} />
          {/* overflow-x is pinned so the header aurora blooms can bleed past the
              content column without producing a horizontal scrollbar. */}
          <main className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
            {/* Keyed on the route: after one screen crashes, moving to another
                must render that screen, not the same error card. Without the
                key the boundary kept its error for the rest of the session. */}
            <ErrorBoundary key={pathname}>{children}</ErrorBoundary>
          </main>
        </div>
      </div>

      <MobileNav open={navOpen} onClose={() => setNavOpen(false)} />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </ToastProvider>
  );
}
