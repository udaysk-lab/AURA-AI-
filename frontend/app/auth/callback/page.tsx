"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui";

/**
 * Google redirects here with the JWT in the URL fragment. Fragments never reach
 * the server or proxy logs, which is why the token is passed that way.
 */
export default function AuthCallback() {
  const router = useRouter();
  const { refresh } = useAuth();

  useEffect(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    if (!token) {
      router.replace("/login?error=missing_token");
      return;
    }
    setToken(token);
    window.history.replaceState({}, "", "/auth/callback");
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
