"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "@/lib/api";
import { Spinner } from "@/components/ui";

/**
 * The home page inside the desktop app. Someone who has installed AURA doesn't
 * need to be offered the download: straight into the app, or to the sign-in
 * screen (which signs the owner in by itself from the app's own link).
 */
export default function DesktopHome() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? "/dashboard" : `/login${window.location.hash}`);
  }, [router]);
  return (
    <div className="flex h-dvh items-center justify-center text-muted">
      <Spinner className="h-5 w-5" />
    </div>
  );
}
