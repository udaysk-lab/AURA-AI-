"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Check,
  Code2,
  Download,
  FileCheck2,
  Globe,
  HardDrive,
  Monitor,
  Smartphone,
} from "lucide-react";
import {
  CHECKSUMS_URL,
  DOWNLOADS,
  PlatformKey,
  RELEASES_URL,
  REPO_URL,
  formatSize,
  useDetectedPlatform,
  useLatestRelease,
} from "@/lib/downloads";

type Os = "windows" | "mac" | "linux";

const OS_OF: Record<PlatformKey, Os> = {
  windows: "windows",
  "mac-arm": "mac",
  "mac-intel": "mac",
  linux: "linux",
};

/** What happens after the download, per system — including the warning it'll show. */
const STEPS: Record<Os, Array<{ title: string; body: string }>> = {
  windows: [
    {
      title: "Run the installer",
      body:
        "Windows may say it protected your PC: AURA isn't code-signed yet. Choose More info, then Run anyway. It installs for you alone, so no administrator password.",
    },
    {
      title: "AURA opens in your browser",
      body:
        "Already signed in — it's your computer, so there's no account to make. A small window stays open to reopen or stop it.",
    },
    {
      title: "Connect a model",
      body:
        "In Settings, paste a Claude or OpenAI key, or point it at a free model running on your machine (Ollama). Until then it runs a limited demo.",
    },
  ],
  mac: [
    {
      title: "Unzip and move it to Applications",
      body:
        "The first time, macOS can't check the app because it isn't notarised yet. Right-click AURA AI and choose Open — or open it from System Settings → Privacy & Security → Open Anyway.",
    },
    {
      title: "AURA opens in your browser",
      body:
        "Already signed in, with a small window to reopen or stop it. To let it see the screen, allow Screen Recording and Accessibility when macOS asks.",
    },
    {
      title: "Connect a model",
      body:
        "In Settings, paste a Claude or OpenAI key, or point it at a free model running on your Mac (Ollama). Until then it runs a limited demo.",
    },
  ],
  linux: [
    {
      title: "Unpack and run it",
      body: "tar xzf AURA-AI-Linux-x64.tar.gz, then ./aura-ai/aura-ai. Screen control needs an X11 session.",
    },
    {
      title: "AURA opens in your browser",
      body: "Already signed in, with a small window to reopen or stop it.",
    },
    {
      title: "Connect a model",
      body:
        "In Settings, paste a Claude or OpenAI key, or point it at a local model (Ollama, LM Studio). Until then it runs a limited demo.",
    },
  ],
};

const OS_TABS: Array<{ key: Os; label: string }> = [
  { key: "windows", label: "Windows" },
  { key: "mac", label: "Mac" },
  { key: "linux", label: "Linux" },
];

export default function GetAura() {
  const detected = useDetectedPlatform();
  const release = useLatestRelease();
  const [os, setOs] = useState<Os>("windows");

  useEffect(() => {
    if (detected in OS_OF) setOs(OS_OF[detected as PlatformKey]);
  }, [detected]);

  const primary =
    DOWNLOADS.find((d) => d.key === detected) ??
    DOWNLOADS.find((d) => OS_OF[d.key] === os) ??
    DOWNLOADS[0];

  return (
    <section id="download" className="relative mx-auto max-w-6xl scroll-mt-24 px-6 py-24">
      <div
        className="aurora-spot left-[8%] top-16 h-[420px] w-[520px]"
        style={{ background: "rgb(var(--accent) / 0.26)" }}
      />

      <div className="relative mb-12 max-w-2xl">
        <h2 className="display text-shine mb-4 text-[34px] sm:text-[46px]">Get AURA</h2>
        <p className="text-[15px] leading-relaxed text-muted">
          Two ways to use it, both free. On your computer it can also work the computer
          itself. In the browser there&apos;s nothing to install.
        </p>
      </div>

      <div className="relative grid gap-5 lg:grid-cols-[1.45fr_1fr]">
        {/* On your computer */}
        <div className="glow-card min-w-0">
          <div className="h-full p-7 sm:p-9">
            <div className="mb-5 flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-line bg-raised/70 text-accent-soft">
                <Monitor size={18} />
              </span>
              <div>
                <h3 className="text-[19px] font-semibold tracking-tight">On your computer</h3>
                <p className="text-[12.5px] text-faint">
                  {release ? `Version ${release.version}` : "Windows, Mac and Linux"}
                </p>
              </div>
            </div>

            <p className="mb-6 max-w-xl text-[14px] leading-relaxed text-muted">
              The whole of AURA, running on your machine. Your chats, memory and files stay
              there. It can run commands, find and edit files, open apps and read the screen
              for you — and below the trust level you choose, it asks first.
            </p>

            <div className="mb-3 flex flex-wrap items-center gap-3">
              <a href={primary.href} className="btn-primary !px-6 !py-3 text-[14px]" rel="nofollow">
                <Download size={16} /> Download for {primary.label}
              </a>
              {primary.alternative && (
                <a
                  href={primary.alternative.href}
                  className="text-[12.5px] text-muted underline-offset-2 hover:text-ink hover:underline"
                  rel="nofollow"
                >
                  {primary.alternative.label}
                </a>
              )}
            </div>
            <p className="mb-7 text-[12px] text-faint">
              {primary.detail}
              {formatSize(release?.sizes[primary.file]) &&
                `, ${formatSize(release?.sizes[primary.file])}`}
            </p>

            {/* Every build, so nobody has to trust the guess above */}
            <div className="mb-8 grid gap-2 sm:grid-cols-2">
              {DOWNLOADS.filter((d) => d.key !== primary.key).map((d) => (
                <a
                  key={d.key}
                  href={d.href}
                  rel="nofollow"
                  className="flex items-center gap-3 rounded-xl border border-line bg-raised/35 px-3.5 py-2.5 transition hover:border-accent/35 hover:bg-raised/60"
                >
                  <HardDrive size={14} className="shrink-0 text-faint" />
                  <span className="min-w-0">
                    <span className="block text-[13px] text-ink">{d.label}</span>
                    <span className="block truncate text-[11.5px] text-faint">
                      {d.detail}
                      {formatSize(release?.sizes[d.file]) && `, ${formatSize(release?.sizes[d.file])}`}
                    </span>
                  </span>
                </a>
              ))}
            </div>

            <div className="border-t border-line pt-6">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <h4 className="text-[14px] font-semibold">After you download</h4>
                <div role="tablist" aria-label="Instructions for" className="flex gap-1 rounded-full border border-line bg-raised/30 p-0.5">
                  {OS_TABS.map((tab) => (
                    <button
                      key={tab.key}
                      role="tab"
                      aria-selected={os === tab.key}
                      onClick={() => setOs(tab.key)}
                      className={`rounded-full px-3 py-1 text-[12px] transition ${
                        os === tab.key ? "bg-accent text-white" : "text-muted hover:text-ink"
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
              </div>
              <ol className="space-y-4">
                {STEPS[os].map((step, i) => (
                  <li key={step.title} className="flex gap-3.5">
                    <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent-dim text-[12px] font-semibold text-accent-soft">
                      {i + 1}
                    </span>
                    <div>
                      <div className="text-[13.5px] font-medium">{step.title}</div>
                      <p className="mt-0.5 text-[12.5px] leading-relaxed text-muted">{step.body}</p>
                    </div>
                  </li>
                ))}
              </ol>
              <div className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-[12px] text-faint">
                <a href={RELEASES_URL} className="inline-flex items-center gap-1.5 hover:text-muted">
                  <Download size={12} /> Release notes
                </a>
                <a href={CHECKSUMS_URL} className="inline-flex items-center gap-1.5 hover:text-muted" rel="nofollow">
                  <FileCheck2 size={12} /> SHA-256 checksums
                </a>
                <a href={REPO_URL} className="inline-flex items-center gap-1.5 hover:text-muted">
                  <Code2 size={12} /> Source code
                </a>
              </div>
            </div>
          </div>
        </div>

        {/* In your browser */}
        <div className="glass flex min-w-0 flex-col p-7 sm:p-9 lg:self-start">
          <div className="mb-5 flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-line bg-raised/70 text-teal">
              <Globe size={18} />
            </span>
            <div>
              <h3 className="text-[19px] font-semibold tracking-tight">In your browser</h3>
              <p className="text-[12.5px] text-faint">Any device, nothing to install</p>
            </div>
          </div>
          <p className="mb-6 text-[14px] leading-relaxed text-muted">
            Make an account and start on realistic sample data, then connect Gmail and
            Google Calendar when you want it working on the real thing.
          </p>
          <ul className="mb-8 space-y-2.5 text-[13px] text-muted">
            {[
              "Email, calendar, tasks, memory and documents",
              "Schedules and automations that run while you're away",
              "The same skills and trust levels as the desktop app",
            ].map((line) => (
              <li key={line} className="flex items-start gap-2.5">
                <Check size={14} className="mt-0.5 shrink-0 text-sage" />
                {line}
              </li>
            ))}
            <li className="flex items-start gap-2.5 text-faint">
              <Monitor size={14} className="mt-0.5 shrink-0" />
              It can&apos;t reach your computer from here — that&apos;s what the download is for.
            </li>
          </ul>
          <div>
            <Link href="/login" className="btn-ghost w-full !py-2.5">
              <Globe size={15} /> Use AURA in your browser
            </Link>
            <p className="mt-4 flex items-start gap-2 text-[12px] leading-relaxed text-faint">
              <Smartphone size={13} className="mt-0.5 shrink-0" />
              Already running AURA on your computer? Pair your phone with it from Settings and
              use it from anywhere on your network.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
