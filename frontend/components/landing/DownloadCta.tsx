"use client";

import Link from "next/link";
import { Download, Globe } from "lucide-react";
import {
  DOWNLOADS,
  formatSize,
  useDetectedPlatform,
  useLatestRelease,
} from "@/lib/downloads";

/**
 * The hero's pair of ways in: download for the visitor's own computer, or use
 * AURA in the browser. Before the browser has said what it runs on (and on a
 * phone, where there's nothing to install), the download button leads to the
 * full list instead.
 */
export default function DownloadCta({ compact = false }: { compact?: boolean }) {
  const platform = useDetectedPlatform();
  const release = useLatestRelease();
  const option = DOWNLOADS.find((d) => d.key === platform);
  const size = option ? formatSize(release?.sizes[option.file]) : "";

  const pad = compact ? "!px-5 !py-2" : "!px-6 !py-2.5";

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="flex flex-wrap items-center justify-center gap-3">
        {platform === "phone" ? (
          <>
            <Link href="/login" className={`btn-primary ${pad}`}>
              <Globe size={15} /> Use AURA in your browser
            </Link>
            <a href="#download" className={`btn-ghost ${pad}`}>
              <Download size={15} /> Get it for your computer
            </a>
          </>
        ) : (
          <>
            {option ? (
              <a href={option.href} className={`btn-primary ${pad}`} rel="nofollow">
                <Download size={15} /> Download for {option.label}
              </a>
            ) : (
              <a href="#download" className={`btn-primary ${pad}`}>
                <Download size={15} /> Download AURA
              </a>
            )}
            <Link href="/login" className={`btn-ghost ${pad}`}>
              <Globe size={15} /> Use it in your browser
            </Link>
          </>
        )}
      </div>
      <p className="text-center text-[12.5px] text-faint">
        Free.{" "}
        {release ? `Version ${release.version}` : "Windows, Mac and Linux"}
        {size && `, ${size}`}.{" "}
        <a href="#download" className="text-muted underline-offset-2 hover:text-ink hover:underline">
          {platform === "mac-arm" ? "Intel Mac, Windows or Linux" : "Other platforms"}
        </a>
      </p>
    </div>
  );
}
