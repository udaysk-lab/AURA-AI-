"use client";

import { useEffect } from "react";
import { api } from "@/lib/api";

const EVERY_MS = 30_000;

/**
 * Tells a local install that someone is at the screen: one ping every 30
 * seconds while this page is visible. AURA only uses the mouse and keyboard
 * while it has heard from you in the last 90 seconds. Does nothing on the
 * hosted site, or in a browser reaching a computer from elsewhere (the
 * backend accepts the ping only from the computer itself).
 */
export default function PresencePing() {
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    let cancelled = false;

    const ping = () => {
      if (document.visibilityState !== "visible") return;
      void api.devicePresence().catch(() => {
        /* a missed ping only means AURA treats you as away */
      });
    };
    const onVisible = () => ping();

    void api
      .authConfig()
      .then((c) => {
        if (cancelled || !c.device_control || c.remote_client) return;
        ping();
        timer = setInterval(ping, EVERY_MS);
        document.addEventListener("visibilitychange", onVisible);
      })
      .catch(() => {
        /* no config, no ping */
      });

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  return null;
}
