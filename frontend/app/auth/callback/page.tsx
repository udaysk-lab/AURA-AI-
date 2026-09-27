"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { UserCheck } from "lucide-react";
import { API_BASE, setToken, takeGoogleSignInStarted } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Spinner } from "@/components/ui";

/**
 * Google redirects here with the JWT in the URL fragment. Fragments never reach
 * the server or proxy logs, which is why the token is passed that way.
 *
 * Anyone can write a link to this page with *their own* token in it. Taken
 * blindly, it signs the visitor into the sender's account without a word, and
 * whatever they then connect or write lands with the sender. So the token is
 * only taken straight away when this tab started a Google sign-in. Otherwise
 * — a link, or a sign-in begun on another address of the site — the page says
 * whose account it is and waits to be told to continue.
 */
export default function AuthCallback() {
  const router = useRouter();
  const { refresh, user } = useAuth();
  const handled = useRef(false); // Strict Mode runs effects twice in dev
  const [confirm, setConfirm] = useState<{ token: string; email: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const finish = useCallback(
    async (token: string) => {
      setBusy(true);
      setToken(token);
      await refresh();
      router.replace("/dashboard");
    },
    [refresh, router]
  );

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
    if (started) {
      void finish(token);
      return;
    }
    // Look up whose token it is without keeping it.
    fetch(`${API_BASE}/api/auth/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((me: { email?: string }) =>
        me.email ? setConfirm({ token, email: me.email }) : Promise.reject(new Error("no email"))
      )
      .catch(() => router.replace("/login?reason=missing_token"));
  }, [router, finish]);

  if (confirm) {
    const switching = user && user.email !== confirm.email;
    return (
      <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-6">
        <div
          className="aurora-spot left-1/2 top-1/2 h-[420px] w-[560px] -translate-x-1/2 -translate-y-1/2"
          style={{ background: "rgb(var(--accent) / 0.22)" }}
        />
        <div className="panel relative w-full max-w-sm p-6 text-center">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent/12 text-accent-soft">
            <UserCheck size={20} />
          </div>
          <h1 className="mb-1.5 text-[16px] font-semibold">Continue as {confirm.email}?</h1>
          <p className="mb-4 text-[13px] leading-relaxed text-muted">
            This sign-in didn&apos;t start in this tab. Only continue if{" "}
            <span className="text-ink">{confirm.email}</span> is your Google account.
            {switching && (
              <>
                {" "}
                You&apos;re signed in as <span className="text-ink">{user.email}</span> now.
              </>
            )}
          </p>
          <div className="flex justify-center gap-2">
            <button
              onClick={() => router.replace(user ? "/dashboard" : "/login?reason=cancelled")}
              disabled={busy}
              className="btn-ghost"
            >
              Cancel
            </button>
            <button onClick={() => void finish(confirm.token)} disabled={busy} className="btn-primary">
              {busy && <Spinner />} Continue
            </button>
          </div>
        </div>
      </div>
    );
  }

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
