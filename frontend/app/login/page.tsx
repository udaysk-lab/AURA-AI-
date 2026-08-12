"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { API_BASE, api, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Mascot from "@/components/Mascot";
import { Spinner } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const { refresh, user, loading } = useAuth();

  const [config, setConfig] = useState<{
    google_enabled: boolean;
    demo_login_enabled: boolean;
    llm_provider: string;
  } | null>(null);
  const [email, setEmail] = useState("demo@aura.ai");
  const [name, setName] = useState("Demo User");
  const [busy, setBusy] = useState(false);
  // Read ?error= without useSearchParams so the page needs no Suspense boundary.
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    // The OAuth callback sends ?google=error&reason=… ; older links used ?error=.
    const reason = params.get("reason") || params.get("error");
    if (reason) {
      const messages: Record<string, string> = {
        denied: "You cancelled the Google sign-in. Nothing changed.",
        bad_state:
          "That sign-in link expired or was already used. Links are valid for 10 minutes — try again.",
        exchange_failed:
          "Google accepted the sign-in but the token exchange failed. Check GOOGLE_CLIENT_SECRET and that the redirect URI matches exactly.",
        no_email: "Google didn't return an email address. Check the requested scopes.",
        no_code: "Google didn't send an authorisation code. Try again.",
      };
      setError(messages[reason] ?? `Sign-in failed: ${reason}`);
      window.history.replaceState({}, "", "/login");
    }
    api
      .authConfig()
      .then(setConfig)
      .catch((e) => setError(describeFailure(e)));
    // describeFailure is stable for the life of the component.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!loading && user) router.replace("/dashboard");
  }, [loading, user, router]);

  /**
   * "Failed to fetch" is what the browser says for *any* request that never got
   * a response — backend down, wrong port, CORS refusal. All three look
   * identical from here, and the raw string tells the user nothing, so name the
   * three candidates instead of repeating it.
   */
  const describeFailure = (e: unknown): string => {
    const raw = e instanceof Error ? e.message : String(e);
    if (!/failed to fetch|networkerror|load failed/i.test(raw)) return raw;
    return (
      `Couldn't reach the API at ${API_BASE}. Three things to check:\n` +
      "1. Is the backend running?  cd backend && uvicorn app.main:app --reload\n" +
      `2. This page is on ${typeof window !== "undefined" ? window.location.origin : "?"}` +
      " — the backend must allow that exact origin in CORS_ORIGINS.\n" +
      "3. Restart the backend after any .env change — it only reads it at startup."
    );
  };

  const signInDemo = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.demoLogin(email, name);
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(describeFailure(e));
      setBusy(false);
    }
  };

  const signInGoogle = async () => {
    setBusy(true);
    try {
      const { authorization_url } = await api.googleStart();
      window.location.href = authorization_url;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Google sign-in unavailable");
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-6">
      <div
        className="aurora-spot left-1/2 top-[-10%] h-[520px] w-[720px] -translate-x-1/2"
        style={{ background: "rgb(var(--accent) / 0.34)" }}
      />
      <div
        className="aurora-spot bottom-[-15%] right-[6%] h-[380px] w-[380px] animate-float"
        style={{ background: "rgb(var(--c-azure) / 0.28)" }}
      />

      <div className="relative w-full max-w-sm animate-fade-up">
        <div className="mb-8 flex flex-col items-center text-center">
          <Mascot colourway="violet" stage="acquaintance" size={76} className="mb-5" />
          <h1 className="display text-shine text-[26px]">Sign in to AURA</h1>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            Your email, calendar, tasks and memory — in one assistant that remembers.
          </p>
        </div>

        <div className="glass space-y-4 p-6">
          {config?.google_enabled && (
            <>
              <button onClick={signInGoogle} disabled={busy} className="btn-ghost w-full">
                Continue with Google
              </button>
              <div className="flex items-center gap-3 text-[11px] uppercase tracking-widest text-faint">
                <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
              </div>
            </>
          )}

          <div className="space-y-3">
            <div>
              <label className="label mb-1.5 block">Email</label>
              <input
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>
            <div>
              <label className="label mb-1.5 block">Name</label>
              <input
                className="input"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
              />
            </div>
            <button
              onClick={signInDemo}
              disabled={busy || !email}
              className="btn-primary w-full"
            >
              {busy ? <Spinner /> : "Continue"}
            </button>
          </div>

          {error && (
            <p className="whitespace-pre-line rounded-lg border border-rose/30 bg-rose/10 px-3 py-2.5 text-[12px] leading-relaxed text-rose">
              {error}
            </p>
          )}
        </div>

        <p className="mt-5 text-center text-[11.5px] leading-relaxed text-faint">
          {config
            ? `Model provider: ${config.llm_provider}${
                config.llm_provider === "mock"
                  ? " — add an API key in .env for the full agent."
                  : ""
              }`
            : "Connecting to API…"}
        </p>
      </div>
    </div>
  );
}
