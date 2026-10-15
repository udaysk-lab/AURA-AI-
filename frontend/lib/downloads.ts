"use client";

import { useEffect, useState } from "react";

/**
 * The desktop app's downloads, as the release workflow publishes them.
 *
 * Every link points at GitHub's "latest release" address for a fixed file
 * name, so a new release changes nothing here: the same link starts serving
 * the new build. The release details (version, sizes) are fetched only to
 * show them; the buttons work without that request.
 */

export const REPO = "udaysk-lab/AURA-AI-";
export const REPO_URL = `https://github.com/${REPO}`;
export const RELEASES_URL = `${REPO_URL}/releases/latest`;
const LATEST = `${REPO_URL}/releases/latest/download/`;

export type PlatformKey = "windows" | "mac-arm" | "mac-intel" | "linux";

export interface DownloadOption {
  key: PlatformKey;
  /** What the button says after "Download for". */
  label: string;
  /** Who it's for, in a few words. */
  detail: string;
  file: string;
  href: string;
  /** The other build for the same OS (Windows portable zip). */
  alternative?: { label: string; file: string; href: string };
}

export const DOWNLOADS: DownloadOption[] = [
  {
    key: "windows",
    label: "Windows",
    detail: "Windows 10 or 11, 64-bit",
    file: "AURA-AI-Setup-Windows-x64.exe",
    href: `${LATEST}AURA-AI-Setup-Windows-x64.exe`,
    alternative: {
      label: "Portable zip (no installer)",
      file: "AURA-AI-Windows-x64-portable.zip",
      href: `${LATEST}AURA-AI-Windows-x64-portable.zip`,
    },
  },
  {
    key: "mac-arm",
    label: "Mac (Apple silicon)",
    detail: "M1 or later, macOS 11+",
    file: "AURA-AI-macOS-AppleSilicon.zip",
    href: `${LATEST}AURA-AI-macOS-AppleSilicon.zip`,
  },
  {
    key: "mac-intel",
    label: "Mac (Intel)",
    detail: "Intel processor, macOS 11+",
    file: "AURA-AI-macOS-Intel.zip",
    href: `${LATEST}AURA-AI-macOS-Intel.zip`,
  },
  {
    key: "linux",
    label: "Linux",
    detail: "x86-64, with a desktop",
    file: "AURA-AI-Linux-x64.tar.gz",
    href: `${LATEST}AURA-AI-Linux-x64.tar.gz`,
  },
];

export const CHECKSUMS_URL = `${LATEST}SHA256SUMS.txt`;

export type Detected = PlatformKey | "phone" | "unknown";

/** Best guess at the visitor's platform. Macs can't say which chip they have. */
export function detectPlatform(): Detected {
  if (typeof navigator === "undefined") return "unknown";
  const nav = navigator as Navigator & { userAgentData?: { platform?: string; mobile?: boolean } };
  const ua = nav.userAgent || "";
  if (nav.userAgentData?.mobile || /Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return "phone";
  const platform = (nav.userAgentData?.platform || nav.platform || ua).toLowerCase();
  if (platform.includes("win")) return "windows";
  if (platform.includes("mac")) return "mac-arm";
  if (platform.includes("linux") || platform.includes("x11")) return "linux";
  return "unknown";
}

export interface ReleaseInfo {
  version: string;
  publishedAt: string;
  sizes: Record<string, number>;
  url: string;
}

let cached: Promise<ReleaseInfo | null> | null = null;

function fetchRelease(): Promise<ReleaseInfo | null> {
  if (!cached) {
    cached = fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" },
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!data?.tag_name) return null;
        const sizes: Record<string, number> = {};
        for (const asset of data.assets ?? []) sizes[asset.name] = asset.size;
        return {
          version: String(data.tag_name).replace(/^v/, ""),
          publishedAt: data.published_at,
          sizes,
          url: data.html_url,
        } as ReleaseInfo;
      })
      .catch(() => null);
  }
  return cached;
}

/** The latest release's details, or null while loading / if unavailable. */
export function useLatestRelease(): ReleaseInfo | null {
  const [release, setRelease] = useState<ReleaseInfo | null>(null);
  useEffect(() => {
    let live = true;
    void fetchRelease().then((r) => live && setRelease(r));
    return () => {
      live = false;
    };
  }, []);
  return release;
}

export function useDetectedPlatform(): Detected {
  const [platform, setPlatform] = useState<Detected>("unknown");
  useEffect(() => setPlatform(detectPlatform()), []);
  return platform;
}

export function formatSize(bytes?: number): string {
  if (!bytes) return "";
  return `${Math.round(bytes / 1_000_000)} MB`;
}
