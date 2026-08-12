"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Assistant, api, getToken } from "./api";

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
  const [assistant, setAssistant] = useState<Assistant | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setAssistant(null);
      setLoading(false);
      return;
    }
    try {
      setAssistant(await api.assistant());
    } catch {
      setAssistant(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <AssistantContext.Provider value={{ assistant, loading, refresh }}>
      {children}
    </AssistantContext.Provider>
  );
}

export const useAssistant = () => useContext(AssistantContext);
