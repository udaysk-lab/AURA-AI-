"use client";

import { Component, ReactNode } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";

interface State {
  error: Error | null;
}

/**
 * Without this, one bad render — a null where an array was expected — blanks the
 * whole app with nothing in the UI to explain it. Users don't read consoles.
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; label?: string },
  State
> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string | null }) {
    // eslint-disable-next-line no-console
    console.error("Render error", error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-[60vh] items-center justify-center p-8">
        <div className="panel max-w-md p-6 text-center">
          <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-rose/12 text-rose">
            <AlertTriangle size={20} />
          </div>
          <h2 className="mb-1.5 text-[16px] font-semibold">
            {this.props.label ?? "This screen"} stopped working
          </h2>
          <p className="mb-4 text-[13px] leading-relaxed text-muted">
            Your data is fine — this is a display problem, not a data one. Reloading
            usually clears it.
          </p>
          <pre className="mb-4 max-h-32 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-line bg-raised/60 p-3 text-left font-mono text-[11px] text-muted">
            {error.message}
          </pre>
          <div className="flex justify-center gap-2">
            <button onClick={() => this.setState({ error: null })} className="btn-ghost">
              <RotateCcw size={13} /> Try again
            </button>
            <button onClick={() => window.location.reload()} className="btn-primary">
              Reload
            </button>
          </div>
        </div>
      </div>
    );
  }
}
