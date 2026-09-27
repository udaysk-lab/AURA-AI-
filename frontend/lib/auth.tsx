"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, User, api, browserTimeZone, clearToken, getToken } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  /**
   * Set when there is a token but the server couldn't confirm it — the API was
   * down, slow, or rate-limiting. That is not the same as being signed out,
   * and the app shows a retry screen instead of the sign-in form.
   */
  error: string;
  refresh: () => Promise<void>;
  logout: () => void;
}

const ZONE_SYNC_KEY = "aura_zone_synced";

function zoneSyncedBefore(userId: string): boolean {
  try {
    return (localStorage.getItem(ZONE_SYNC_KEY) ?? "").split(",").includes(userId);
  } catch {
    return false;
  }
}

function markZoneSynced(userId: string) {
  try {
    const ids = (localStorage.getItem(ZONE_SYNC_KEY) ?? "").split(",").filter(Boolean);
    localStorage.setItem(ZONE_SYNC_KEY, [...ids.filter((id) => id !== userId), userId].slice(-20).join(","));
  } catch {
    /* then it may run again next visit, which is harmless */
  }
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  error: "",
  refresh: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const router = useRouter();
  const zoneSynced = useRef<string | null>(null); // who was checked this session

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setError("");
      setLoading(false);
      return;
    }
    try {
      let me = await api.me();
      setError("");
      // Accounts made before the browser's zone was sent at sign-up are still
      // on the UTC default, which puts their "today" and "5pm" hours off.
      // Fill it in once per account in this browser, quietly. Remembering that
      // it was done is what lets someone who later picks UTC on purpose keep it.
      if (zoneSynced.current !== me.id && !zoneSyncedBefore(me.id)) {
        zoneSynced.current = me.id;
        markZoneSynced(me.id);
        const zone = browserTimeZone();
        if (me.timezone === "UTC" && zone && zone !== "UTC") {
          try {
            me = await api.updateMe({ timezone: zone });
          } catch {
            /* not worth interrupting anyone over */
          }
        }
      }
      setUser(me);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setUser(null);
        setError("");
      } else {
        // Keep whoever was signed in. A 429 or a cold start used to land here
        // and sign the person out of the UI, although their token was fine.
        setError(e instanceof Error ? e.message : "Couldn't reach AURA.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    setError("");
    router.push("/login");
  }, [router]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider value={{ user, loading, error, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
