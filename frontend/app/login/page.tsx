"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Smartphone } from "lucide-react";
import {
  API_BASE,
  API_BASE_IS_FALLBACK,
  AuthConfig,
  api,
  markGoogleSignInStarted,
  setToken,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import Mascot from "@/components/Mascot";
import { Spinner } from "@/components/ui";

/**
 * The desktop app opens /login#key=… — the key that proves this browser was
 * opened by the person who started AURA on this computer. It's kept for this
 * tab only and taken out of the address bar at once.
 */
const LOCAL_KEY = "aura_local_key";

function takeLocalKey(): string {
  try {
    const fromHash = new URLSearchParams(window.location.hash.slice(1)).get("key");
    const fromQuery = new URLSearchParams(window.location.search).get("key");
    const key = fromHash || fromQuery || "";
    if (key) {
      sessionStorage.setItem(LOCAL_KEY, key);
      window.history.replaceState({}, "", "/login");
      return key;
    }
    return sessionStorage.getItem(LOCAL_KEY) || "";
  } catch {
    return "";
  }
}

function defaultDeviceName(): string {
  if (typeof navigator === "undefined") return "Phone";
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android phone";
  if (/Mac/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  return "Phone";
}

export default function LoginPage() {
  const router = useRouter();
  const { refresh, user, loading } = useAuth();

  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [mode, setMode] = useState<"signin" | "signup" | "forgot">("signin");
  const [localKey, setLocalKey] = useState("");
  const [pairCode, setPairCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [notice, setNotice] = useState("");
  const autoTried = useRef(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  // Read ?error= without useSearchParams so the page needs no Suspense boundary.
  const [error, setError] = useState("");

  useEffect(() => {
    setLocalKey(takeLocalKey());
    setDeviceName(defaultDeviceName());
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
        missing_token: "Google sign-in didn't finish. Try again.",
        cancelled: "Sign-in cancelled. Nothing changed.",
        local_install:
          "AURA on your own computer has one account, yours. Sign in with “Open AURA on this computer”, then connect Google from Settings.",
      };
      // Unknown reasons get a fixed sentence: the URL is anyone's to write,
      // and echoing it let a link put its own words on the sign-in page.
      setError(
        Object.prototype.hasOwnProperty.call(messages, reason)
          ? messages[reason]
          : "Google sign-in didn't finish. Try again."
      );
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
   * a response — backend down, wrong port, CORS refusal, wrong host. They look
   * identical from here, and the raw string tells the user nothing.
   *
   * The one case we can identify precisely is worth separating out: a deployed
   * page still pointing at localhost. That means NEXT_PUBLIC_API_URL was missing
   * when the bundle was built, so the browser is dialling the visitor's own
   * machine. Sending someone to go and check uvicorn logs for that is a wild
   * goose chase — no backend change can fix it.
   */
  const describeFailure = (e: unknown): string => {
    const raw = e instanceof Error ? e.message : String(e);
    if (!/failed to fetch|networkerror|load failed/i.test(raw)) return raw;

    const origin = typeof window !== "undefined" ? window.location.origin : "?";
    const host = typeof window !== "undefined" ? window.location.hostname : "";
    const isLocalPage =
      host === "localhost" || host === "127.0.0.1" || host === "";

    if (!isLocalPage && API_BASE_IS_FALLBACK) {
      return (
        `No API address is configured, so requests went to this same origin ` +
        `(${origin}/api) — and nothing there answered.\n\n` +
        "Either of these fixes it:\n" +
        "1. Route /api to the backend from this domain. vercel.json already " +
        "declares that rewrite, so if it isn't working the backend service " +
        "either failed to build or is crashing — check the deployment logs. A " +
        "500 from /api/auth/config means it deployed but crashed on startup, " +
        "and the usual cause is DATABASE_URL still pointing at SQLite, which " +
        "cannot work on a read-only serverless filesystem.\n" +
        "2. Host the backend separately and set NEXT_PUBLIC_API_URL to its " +
        "public URL.\n\n" +
        "NEXT_PUBLIC_ values are baked into the JavaScript at build time, so " +
        "adding the variable alone changes nothing until you rebuild."
      );
    }

    if (!isLocalPage) {
      return (
        `Couldn't reach the API at ${API_BASE}. Two likely causes:\n` +
        "1. The backend is down or still waking up. Free hosting tiers sleep " +
        "when idle — open it directly and wait for it to respond, then retry.\n" +
        `2. CORS. This page is on ${origin}; the backend's CORS_ORIGINS must ` +
        "contain that exact origin — scheme and host, no trailing slash."
      );
    }

    return (
      `Couldn't reach the API at ${API_BASE}. Three things to check:\n` +
      "1. Is the backend running?  cd backend && uvicorn app.main:app --reload\n" +
      `2. This page is on ${origin} — the backend must allow that exact origin ` +
      "in CORS_ORIGINS.\n" +
      "3. Restart the backend after any .env change — it only reads it at startup."
    );
  };

  const submit = async () => {
    if (mode === "signup" && password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res =
        mode === "signup"
          ? await api.register(email, password, name)
          : await api.login(email, password);
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(describeFailure(e));
      setBusy(false);
    }
  };

  const signInDemo = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.demoLogin(email || "demo@aura.ai", name || "Demo User");
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(describeFailure(e));
      setBusy(false);
    }
  };

  /**
   * On a local install there's one person — whoever is at the keyboard — so
   * there's nothing to sign in *to*. One button, no account to invent. The
   * backend only honours it from this machine.
   */
  const openLocally = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.localLogin(localKey);
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(describeFailure(e));
      setBusy(false);
    }
  };

  // The desktop app's own link carries the key: sign straight in, once, so a
  // double-click on AURA lands in AURA rather than on a button to press.
  useEffect(() => {
    if (autoTried.current || loading || user || !config?.local_mode || config.remote_client) return;
    if (config.local_login_key_required && !localKey) return;
    if (!localKey) return; // a source install: the button is enough
    autoTried.current = true;
    void openLocally();
    // openLocally is recreated each render; the guard makes this run once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, localKey, loading, user]);

  const pair = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await api.remotePair(pairCode, deviceName.trim() || defaultDeviceName());
      setToken(res.access_token);
      await refresh();
      router.push("/dashboard");
    } catch (e) {
      setError(describeFailure(e));
      setBusy(false);
    }
  };

  const sendReset = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const res = await api.forgotPassword(email);
      setNotice(res.message);
    } catch (e) {
      setError(describeFailure(e));
    } finally {
      setBusy(false);
    }
  };

  const signInGoogle = async () => {
    setBusy(true);
    try {
      const { authorization_url } = await api.googleStart();
      markGoogleSignInStarted();
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

      <Link
        href="/"
        className="absolute left-4 top-4 z-10 inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] text-muted transition hover:bg-raised/60 hover:text-ink sm:left-6 sm:top-6"
      >
        <ArrowLeft size={13} /> AURA home
      </Link>

      <div className="relative w-full max-w-sm animate-fade-up py-16">
        <div className="mb-8 flex flex-col items-center text-center">
          <Mascot colourway="violet" stage="acquaintance" size={76} className="mb-5" />
          <h1 className="display text-shine text-[26px]">
            {config?.remote_client
              ? "Pair this device"
              : config?.local_mode
                ? "AURA on this computer"
                : mode === "signup"
                  ? "Create your AURA account"
                  : mode === "forgot"
                    ? "Set a new password"
                    : "Sign in to AURA"}
          </h1>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            {config?.remote_client
              ? "Use the AURA running on your computer from here."
              : "Your email, calendar, tasks and memory — in one assistant that remembers."}
          </p>
        </div>

        {config?.remote_client ? (
          <div className="glass space-y-4 p-6">
            <div className="flex items-start gap-3 rounded-xl border border-line bg-raised/40 p-3 text-[12.5px] leading-relaxed text-muted">
              <Smartphone size={16} className="mt-0.5 shrink-0 text-accent-soft" />
              <span>
                On the computer running AURA, open <b className="text-ink">Settings → Use AURA from
                your phone</b> and press <b className="text-ink">Pair a device</b>. Type the code
                it shows here.
              </span>
            </div>
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy) void pair();
              }}
            >
              <div>
                <label htmlFor="pair-code" className="label mb-1.5 block">
                  Pairing code
                </label>
                <input
                  id="pair-code"
                  className="input text-center font-mono text-[18px] uppercase tracking-[0.3em]"
                  value={pairCode}
                  onChange={(e) => setPairCode(e.target.value.toUpperCase())}
                  placeholder="ABCD-EFGH"
                  autoComplete="one-time-code"
                  autoCapitalize="characters"
                  maxLength={12}
                  required
                />
              </div>
              <div>
                <label htmlFor="pair-name" className="label mb-1.5 block">
                  Call this device
                </label>
                <input
                  id="pair-name"
                  className="input"
                  value={deviceName}
                  onChange={(e) => setDeviceName(e.target.value)}
                  maxLength={120}
                />
              </div>
              <button type="submit" disabled={busy || pairCode.length < 8} className="btn-primary w-full">
                {busy ? <Spinner /> : null} Pair
              </button>
            </form>
            {error && (
              <p role="alert" className="whitespace-pre-line rounded-lg border border-rose/30 bg-rose/10 px-3 py-2.5 text-[12px] leading-relaxed text-rose">
                {error}
              </p>
            )}
          </div>
        ) : (
        <div className="glass space-y-4 p-6">
          {config?.local_mode && (
            <>
              <button onClick={openLocally} disabled={busy || (config.local_login_key_required && !localKey)} className="btn-primary w-full">
                {busy ? <Spinner /> : null} Open AURA on this computer
              </button>
              <p className="text-center text-[11.5px] leading-relaxed text-faint">
                {config.local_login_key_required && !localKey
                  ? "To sign in, press “Open AURA” in the AURA AI window on this computer."
                  : config.device_control
                    ? "This is your own install. AURA will work as you, on this machine."
                    : "This is your own install — no account needed."}
              </p>
            </>
          )}

          {/* A local install has one account, its owner's, so there is no
              other form to offer: the backend refuses sign-up, passwords and
              demo sign-in there. */}
          {!config?.local_mode && (
          <>

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

          {/* Email + password is always available — it needs no configuration,
              unlike Google OAuth. Enter submits, because a login form that
              ignores the return key is the single most irritating thing a login
              form can do. */}
          {mode === "forgot" ? (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                if (!busy) void sendReset();
              }}
            >
              <p className="text-[12.5px] leading-relaxed text-muted">
                Enter your account&apos;s email. If it has an account, a link to choose a new
                password arrives in a minute.
              </p>
              <div>
                <label htmlFor="forgot-email" className="label mb-1.5 block">
                  Email
                </label>
                <input
                  id="forgot-email"
                  className="input"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  autoComplete="email"
                />
              </div>
              <button type="submit" disabled={busy || !email} className="btn-primary w-full">
                {busy ? <Spinner /> : "Email me a link"}
              </button>
              {notice && (
                <p role="status" className="rounded-lg border border-sage/30 bg-sage/10 px-3 py-2.5 text-[12px] leading-relaxed text-sage">
                  {notice}
                </p>
              )}
              <button
                type="button"
                onClick={() => {
                  setMode("signin");
                  setNotice("");
                  setError("");
                }}
                className="block w-full text-center text-[12px] text-accent-soft underline-offset-2 hover:underline"
              >
                Back to sign in
              </button>
            </form>
          ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!busy) void submit();
            }}
          >
            {mode === "signup" && (
              <div>
                <label htmlFor="login-name" className="label mb-1.5 block">
                  Name
                </label>
                <input
                  id="login-name"
                  className="input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  autoComplete="name"
                />
              </div>
            )}
            <div>
              <label htmlFor="login-email" className="label mb-1.5 block">
                Email
              </label>
              <input
                id="login-email"
                className="input"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                autoComplete="email"
              />
            </div>
            <div>
              <label htmlFor="login-password" className="label mb-1.5 block">
                Password
              </label>
              <input
                id="login-password"
                className="input"
                type="password"
                minLength={mode === "signup" ? 8 : undefined}
                maxLength={72}
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
                // Tells a password manager whether to offer a saved password or
                // to generate a new one.
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
              />
            </div>
            <button
              type="submit"
              disabled={busy || !email || !password}
              className="btn-primary w-full"
            >
              {busy ? <Spinner /> : mode === "signup" ? "Create account" : "Sign in"}
            </button>
            {mode === "signin" && config?.password_reset_enabled && (
              <button
                type="button"
                onClick={() => {
                  setMode("forgot");
                  setError("");
                  setPassword("");
                }}
                className="block w-full text-center text-[12px] text-faint underline-offset-2 hover:text-muted hover:underline"
              >
                Forgot your password?
              </button>
            )}
          </form>
          )}

          {mode !== "forgot" && (
          <p className="text-center text-[12px] text-muted">
            {mode === "signup" ? "Already have an account?" : "No account yet?"}{" "}
            <button
              type="button"
              onClick={() => {
                setMode(mode === "signup" ? "signin" : "signup");
                setError("");
                setPassword("");
              }}
              className="text-accent-soft underline-offset-2 hover:underline"
            >
              {mode === "signup" ? "Sign in" : "Create one"}
            </button>
          </p>
          )}
          </>
          )}

          {/* Only when the backend still accepts it. On a deployed instance this
              should be off — it is a password-free login. */}
          {config?.demo_login_enabled && !config.local_mode && (
            <>
              <div className="flex items-center gap-3 text-[11px] uppercase tracking-widest text-faint">
                <span className="h-px flex-1 bg-line" /> or <span className="h-px flex-1 bg-line" />
              </div>
              <button onClick={signInDemo} disabled={busy} className="btn-ghost w-full">
                Explore the demo — no password
              </button>
            </>
          )}

          {error && (
            <p
              role="alert"
              className="whitespace-pre-line rounded-lg border border-rose/30 bg-rose/10 px-3 py-2.5 text-[12px] leading-relaxed text-rose"
            >
              {error}
            </p>
          )}
        </div>
        )}

        {/* Told to every visitor, so it's written for them: this used to say
            "add an API key in .env", which only the person running the
            server can act on. */}
        <p className="mt-5 text-center text-[11.5px] leading-relaxed text-faint">
          {config
            ? config.llm_provider === "mock"
              ? "Demo mode: AI replies are simulated on this server for now. Everything else works."
              : null
            : "Connecting…"}
        </p>
      </div>
    </div>
  );
}
