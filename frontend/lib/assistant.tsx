"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Assistant, api, getToken } from "./api";
import { useAuth } from "./auth";

interface AssistantState {
  assistant: Assistant | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

const AssistantContext = createContext<AssistantState>({
  assistant: null,
  loading: true,
  refresh: async () => {},
});

export function AssistantProvider({ children }: { children: React.ReactNode }) {
  const { user, loading: authLoading } = useAuth();
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setAssistant(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setAssistant(await api.assistant());
    } catch {
      setAssistant(null);
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
      setAssistant(null);
      setLoading(false);
      return;
    }
    void refresh();
  }, [authLoading, userId, refresh]);

  return (
    <AssistantContext.Provider value={{ assistant, loading, refresh }}>
      {children}
    </AssistantContext.Provider>
  );
}

export const useAssistant = () => useContext(AssistantContext);
