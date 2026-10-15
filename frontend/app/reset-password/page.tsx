"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, KeyRound } from "lucide-react";
import { api, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Mascot from "@/components/Mascot";
import { Spinner } from "@/components/ui";

/**
 * Where an emailed "set a new password" link lands.
 *
 * The token travels after # so it never reaches a server log or a Referer
 * header, and it's taken out of the address bar as soon as it's read. It works
 * once: setting a password changes what the token was signed against.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [token, setTokenValue] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const value = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
    setTokenValue(value);
    if (value) window.history.replaceState({}, "", "/reset-password");
  }, []);

  const submit = async () => {
    if (password.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Those two passwords don't match.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api.resetPassword(token || "", password);
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work. Ask for a new link.");
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-6">
      <div
        className="aurora-spot left-1/2 top-[-10%] h-[520px] w-[720px] -translate-x-1/2"
        style={{ background: "rgb(var(--accent) / 0.3)" }}
      />
      <Link
        href="/login"
        className="absolute left-4 top-4 z-10 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] text-muted transition hover:bg-raised/60 hover:text-ink sm:left-6 sm:top-6"
      >
        <ArrowLeft size={13} /> Sign in
      </Link>

      <div className="relative w-full max-w-sm animate-fade-up py-16">
        <div className="mb-8 flex flex-col items-center text-center">
          <Mascot colourway="violet" stage="acquaintance" size={68} className="mb-5" />
          <h1 className="display text-shine text-[26px]">Choose a new password</h1>
        </div>

        <div className="glass space-y-4 p-6">
          {token === "" ? (
            <p className="text-[13px] leading-relaxed text-muted">
              This page needs the link from your email. Open the email again, or{" "}
              <Link href="/login" className="text-accent-soft underline-offset-2 hover:underline">
                ask for a new link
              </Link>
              .
            </p>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy) void submit();
              }}
            >
              <div>
                <label htmlFor="new-password" className="label mb-1.5 block">
                  New password
                </label>
                <input
                  id="new-password"
                  className="input"
                  type="password"
                  minLength={8}
                  maxLength={72}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                />
              </div>
              <div>
                <label htmlFor="confirm-password" className="label mb-1.5 block">
                  Same again
                </label>
                <input
                  id="confirm-password"
                  className="input"
                  type="password"
                  maxLength={72}
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  autoComplete="new-password"
                />
              </div>
              <button type="submit" disabled={busy || !password || !confirm} className="btn-primary w-full">
                {busy ? <Spinner /> : <KeyRound size={14} />} Set password and sign in
              </button>
            </form>
          )}
          {error && (
            <p role="alert" className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2.5 text-[12px] leading-relaxed text-rose">
              {error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
