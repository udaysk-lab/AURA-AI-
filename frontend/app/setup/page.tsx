"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, Check, ExternalLink, Info, RefreshCw } from "lucide-react";
import { Preflight, PreflightCheck, api } from "@/lib/api";
import Mascot from "@/components/Mascot";
import { Spinner } from "@/components/ui";

const ICON = {
  ok: Check,
  warn: Info,
  fail: AlertTriangle,
};

const STYLE = {
  ok: "border-sage/35 bg-sage/10 text-sage",
  warn: "border-amber/35 bg-amber/10 text-amber",
  fail: "border-rose/35 bg-rose/10 text-rose",
};

/**
 * Public diagnostics. Reachable at /setup without signing in, because the most
 * likely reason someone can't sign in is that the deployment is misconfigured.
 */
export default function SetupPage() {
  const [report, setReport] = useState<Preflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [unreachable, setUnreachable] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setUnreachable(false);
    try {
      setReport(await api.preflight());
    } catch {
      setUnreachable(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const group = (level: PreflightCheck["level"]) =>
    report?.checks.filter((c) => c.level === level) ?? [];

  return (
    <div className="relative mx-auto min-h-screen max-w-2xl overflow-hidden px-6 py-14">
      <div
        className="aurora-spot left-1/2 top-[-12%] h-[420px] w-[680px] -translate-x-1/2"
        style={{ background: "rgb(var(--accent) / 0.24)" }}
      />

      <div className="relative mb-8 flex items-center gap-4">
        <Mascot colourway="violet" stage="acquaintance" size={52} />
        <div>
          <h1 className="display text-shine text-[24px]">Setup check</h1>
          <p className="text-[13px] text-muted">
            What&apos;s configured, what isn&apos;t, and exactly how to fix it.
          </p>
        </div>
        <button onClick={load} disabled={loading} className="btn-ghost ml-auto">
          {loading ? <Spinner /> : <RefreshCw size={14} />}
        </button>
      </div>

      {unreachable && (
        <div className="panel mb-6 border-rose/35 p-5">
          <h2 className="mb-1.5 flex items-center gap-2 text-[15px] font-semibold text-rose">
            <AlertTriangle size={16} /> Can&apos;t reach the API
          </h2>
          <p className="mb-3 text-[13px] leading-relaxed text-muted">
            The frontend is running but the backend isn&apos;t answering. Start it with:
          </p>
          <pre className="rounded-xl border border-line bg-raised/60 p-3 font-mono text-[12px]">
            cd backend{"\n"}uvicorn app.main:app --reload
          </pre>
          <p className="mt-3 text-[12px] text-faint">
            If it is running, check that <code>NEXT_PUBLIC_API_URL</code> matches its
            address.
          </p>
        </div>
      )}

      {loading && !report && (
        <div className="flex items-center gap-2 text-[13px] text-muted">
          <Spinner /> Checking…
        </div>
      )}

      {report && (
        <>
          <div
            className={`panel mb-6 border p-5 ${
              report.ready ? "border-sage/35" : "border-rose/35"
            }`}
          >
            <div className="mb-1.5 flex items-center gap-2">
              {report.ready ? (
                <Check size={17} className="text-sage" />
              ) : (
                <AlertTriangle size={17} className="text-rose" />
              )}
              <h2 className="text-[16px] font-semibold">
                {report.ready ? "Ready to use" : `${report.failures} thing(s) to fix first`}
              </h2>
            </div>
            <p className="text-[13px] text-muted">
              Environment: <code className="font-mono">{report.environment}</code>
              {report.warnings > 0 &&
                ` · ${report.warnings} optional feature${
                  report.warnings === 1 ? "" : "s"
                } not configured`}
            </p>
            {report.ready && (
              <Link href="/login" className="btn-primary mt-4">
                Open AURA <ArrowRight size={15} />
              </Link>
            )}
          </div>

          {(["fail", "warn", "ok"] as const).map((level) => {
            const items = group(level);
            if (!items.length) return null;
            const Icon = ICON[level];
            const heading = {
              fail: "Must fix",
              warn: "Optional — works without these",
              ok: "Configured",
            }[level];

            return (
              <section key={level} className="mb-6">
                <h3 className="label mb-2.5">{heading}</h3>
                <div className="space-y-2">
                  {items.map((c) => (
                    <div key={c.key} className="panel p-4">
                      <div className="mb-1.5 flex items-start gap-2.5">
                        <span
                          className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-lg border ${STYLE[level]}`}
                        >
                          <Icon size={11} />
                        </span>
                        <div className="min-w-0">
                          <div className="text-[13.5px] font-medium">{c.label}</div>
                          <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">
                            {c.detail}
                          </p>
                        </div>
                      </div>
                      {c.fix && (
                        <pre className="mt-2 whitespace-pre-wrap break-words rounded-xl border border-line bg-raised/60 p-2.5 font-mono text-[11.5px] leading-relaxed text-muted">
                          {c.fix}
                        </pre>
                      )}
                      {c.docs && (
                        <a
                          href={c.docs.startsWith("http") ? c.docs : undefined}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-accent-soft hover:underline"
                        >
                          {c.docs} <ExternalLink size={11} />
                        </a>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </>
      )}

      <footer className="mt-10 border-t border-line pt-5 text-[12px] leading-relaxed text-faint">
        This page names missing settings but never shows their values, so it&apos;s safe
        to leave reachable. Set <code>ENVIRONMENT=production</code> and the backend
        refuses to start while anything in &ldquo;Must fix&rdquo; is outstanding.
      </footer>
    </div>
  );
}
