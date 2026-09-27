"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { setToken, takeGoogleSignInStarted } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui";

/**
 * Google redirects here with the JWT in the URL fragment. Fragments never reach
 * the server or proxy logs, which is why the token is passed that way.
 *
 * The token is only taken if a Google sign-in was started from this tab's
 * sign-in page. Otherwise anyone could send a link carrying *their* token and
 * sign the reader into their account without the reader noticing.
 */
export default function AuthCallback() {
  const router = useRouter();
  const { refresh } = useAuth();
  const handled = useRef(false); // Strict Mode runs effects twice in dev

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    window.history.replaceState({}, "", "/auth/callback");
    const started = takeGoogleSignInStarted();
    if (!token) {
      router.replace("/login?reason=missing_token");
      return;
    }
    if (!started) {
      router.replace("/login?reason=not_started");
      return;
    }
    setToken(token);
    void refresh().then(() => router.replace("/dashboard"));
  }, [router, refresh]);

  return (
    <div className="relative flex h-screen flex-col items-center justify-center gap-3 overflow-hidden">
      <div
        className="aurora-spot left-1/2 top-1/2 h-[420px] w-[560px] -translate-x-1/2 -translate-y-1/2 animate-glow-pulse"
        style={{ background: "rgb(var(--accent) / 0.32)" }}
      />
      <Spinner className="relative h-5 w-5 text-accent-soft" />
      <p className="relative text-[13px] text-muted">Finishing sign-in…</p>
    </div>
  );
}
