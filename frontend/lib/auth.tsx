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
  const zoneSynced = useRef(false);

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
      // Fill it in once, quietly; an explicit choice is never overwritten.
      const zone = browserTimeZone();
      if (!zoneSynced.current && me.timezone === "UTC" && zone && zone !== "UTC") {
        zoneSynced.current = true;
        try {
          me = await api.updateMe({ timezone: zone });
        } catch {
          /* not worth interrupting anyone over */
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
