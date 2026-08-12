"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { AlertTriangle, Check, Info, X } from "lucide-react";

type Tone = "success" | "error" | "info";

interface Toast {
  id: number;
  tone: Tone;
  message: string;
}

interface ToastApi {
  push: (message: string, tone?: Tone) => void;
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastApi>({
  push: () => {},
  success: () => {},
  error: () => {},
});

let counter = 0;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((message: string, tone: Tone = "info") => {
    const id = ++counter;
    setToasts((prev) => [...prev.slice(-3), { id, tone, message }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);

  const api: ToastApi = {
    push,
    success: useCallback((m: string) => push(m, "success"), [push]),
    error: useCallback((m: string) => push(m, "error"), [push]),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-5 left-1/2 z-[60] flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4">
        {toasts.map((t) => (
          <ToastRow key={t.id} toast={t} onClose={() => setToasts((p) => p.filter((x) => x.id !== t.id))} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastRow({ toast, onClose }: { toast: Toast; onClose: () => void }) {
  const Icon = toast.tone === "success" ? Check : toast.tone === "error" ? AlertTriangle : Info;
  const tone =
    toast.tone === "success"
      ? "border-sage/40 bg-sage/12 text-sage"
      : toast.tone === "error"
        ? "border-rose/40 bg-rose/12 text-rose"
        : "border-line bg-panel/85 text-ink";

  return (
    <div
      className={`pointer-events-auto flex animate-fade-up items-start gap-2.5 rounded-2xl border px-4 py-3 shadow-lift backdrop-blur-2xl ${tone}`}
      role="status"
    >
      <Icon size={15} className="mt-0.5 shrink-0" />
      <span className="min-w-0 flex-1 text-[13px] leading-relaxed">{toast.message}</span>
      <button onClick={onClose} className="shrink-0 opacity-50 transition hover:opacity-100">
        <X size={13} />
      </button>
    </div>
  );
}

export const useToast = () => useContext(ToastContext);

/** Wrap an async action so failures always surface instead of dying silently. */
export function useAction() {
  const toast = useToast();
  return useCallback(
    async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
      try {
        const result = await fn();
        if (success) toast.success(success);
        return result;
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Something went wrong");
        return undefined;
      }
    },
    [toast]
  );
}

/** Small helper so pages can react to ⌘K etc. without duplicating listeners. */
export function useHotkey(key: string, handler: () => void, meta = true) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const modifier = meta ? e.metaKey || e.ctrlKey : true;
      if (modifier && e.key.toLowerCase() === key.toLowerCase()) {
        e.preventDefault();
        handler();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, handler, meta]);
}
