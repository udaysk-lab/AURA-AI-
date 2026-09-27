"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ApiError, Assistant, api, getToken } from "./api";
import { useAuth } from "./auth";

interface AssistantState {
  assistant: Assistant | null;
  loading: boolean;
  refresh: () => Promise<void>;
  /** Use an assistant a request just returned, without asking again. */
  replace: (next: Assistant) => void;
}

const AssistantContext = createContext<AssistantState>({
  assistant: null,
  loading: true,
  refresh: async () => {},
  replace: () => {},
});

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [loading, setLoading] = useState(true);
  const loadedFor = useRef<string | null>(null);

  // Chat calls this after every reply. It used to flip `loading` on each call
  // and drop the assistant on any failed request, so one slow or rate-limited
  // refresh reset the sidebar to "Aura" and the default avatar.
  const refresh = useCallback(async () => {
    if (!getToken()) {
      setAssistant(null);
      setLoading(false);
      return;
    }
    try {
      setAssistant(await api.assistant());
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setAssistant(null);
      // Anything else: keep what's on screen and try again next time.
    } finally {
      setLoading(false);
    }
  }, []);

  // Follow whoever is signed in. This used to load once, when the page first
  // mounted — before sign-in on the login page — so after signing up the app
  // still thought there was no assistant, skipped onboarding, and showed
  // "Aura · Assistant" until the next full reload.
  const userId = user?.id ?? null;
  useEffect(() => {
    if (authLoading) return;
    if (!userId) {
      loadedFor.current = null;
      setAssistant(null);
      setLoading(false);
      return;
    }
    if (loadedFor.current !== userId) {
      // Someone else signed in: never show the previous person's assistant.
      loadedFor.current = userId;
      setAssistant(null);
      setLoading(true);
    }
    void refresh();
  }, [authLoading, userId, refresh]);

  return (
    <AssistantContext.Provider value={{ assistant, loading, refresh, replace: setAssistant }}>
      {children}
    </AssistantContext.Provider>
  );
}

export const useAssistant = () => useContext(AssistantContext);
