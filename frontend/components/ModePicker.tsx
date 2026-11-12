"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { PermissionMode, PermissionState, api } from "@/lib/api";
import { AUTO_UNAVAILABLE_REASON, MODES } from "@/lib/permissions";
import { useToast } from "./Toast";
import { Modal, Spinner } from "./ui";

/**
 * Reads and writes the permission mode through the API. Two looks:
 *   compact   the composer's row of three buttons
 *   detailed  the Permissions page: one card per mode with its line
 * Auto is disabled, with the reason beside it, when the API says it can't run.
 * Switching to Skip approvals asks once, in a dialog, before it is saved.
 */
export default function ModePicker({
  variant = "compact",
  onChange,
}: {
  variant?: "compact" | "detailed";
  onChange?: (state: PermissionState) => void;
}) {
  const toast = useToast();
  const [state, setState] = useState<PermissionState | null>(null);
  const [failed, setFailed] = useState(false);
  const [saving, setSaving] = useState<PermissionMode | null>(null);
  const [confirmSkip, setConfirmSkip] = useState(false);
  const groupId = useId();
  const cancelRef = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.permissions();
      setState(next);
      setFailed(false);
      onChange?.(next);
    } catch {
      setFailed(true);
    }
    // onChange is only a notification; reloading on its identity would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (confirmSkip) cancelRef.current?.focus();
  }, [confirmSkip]);

  const save = async (mode: PermissionMode) => {
    setSaving(mode);
    try {
      const next = await api.setPermissionMode(mode);
      setState(next);
      onChange?.(next);
    } catch (e) {
      // 409 when Auto can't run; the message says why.
      toast.error(e instanceof Error ? e.message : "Couldn't change the permission mode");
      await load();
    } finally {
      setSaving(null);
    }
  };

  const choose = (mode: PermissionMode) => {
    if (!state || saving || mode === state.mode) return;
    if (mode === "auto" && !state.auto_available) return;
    if (mode === "skip") {
      setConfirmSkip(true);
      return;
    }
    void save(mode);
  };

  // Arrow keys move through the radio group, as a native group would.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!state) return;
    const keys = ["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"];
    if (!keys.includes(e.key)) return;
    e.preventDefault();
    const enabled = MODES.filter((m) => m.key !== "auto" || state.auto_available);
    const at = enabled.findIndex((m) => m.key === state.mode);
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1;
    const next = enabled[(at + step + enabled.length) % enabled.length];
    choose(next.key);
    const el = document.querySelector<HTMLElement>(`[data-mode-group="${groupId}"] [data-mode="${next.key}"]`);
    el?.focus();
  };

  if (failed && !state) {
    return (
      <p className="text-[12.5px] text-muted">
        Couldn&apos;t read the permission mode.{" "}
        <button type="button" onClick={() => void load()} className="min-h-[44px] text-accent-soft underline">
          Try again
        </button>
      </p>
    );
  }
  if (!state) {
    return <div className="h-11 w-60 animate-pulse rounded-full bg-raised/60" aria-hidden />;
  }

  const autoOff = !state.auto_available;
  const actingAs = state.mode === "auto" && !state.auto_effective;

  const confirm = (
    <Modal open={confirmSkip} onClose={() => setConfirmSkip(false)} title="Switch to Skip approvals?">
      <div className="space-y-3">
        <p className="text-[13px] leading-relaxed text-muted">
          AURA will stop asking before it changes or sends things, in every chat, schedule
          and automation set to follow this mode. It will still ask for the things that
          always need you, and for anything your Ask or Block rules cover.
        </p>
        <p className="text-[13px] leading-relaxed text-muted">
          You can switch back at any time from the box where you write to AURA.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            className="btn-ghost min-h-[44px]"
            onClick={() => setConfirmSkip(false)}
          >
            Keep {state.mode_label}
          </button>
          <button
            type="button"
            className="btn-primary min-h-[44px]"
            onClick={() => {
              setConfirmSkip(false);
              void save("skip");
            }}
          >
            Switch to Skip approvals
          </button>
        </div>
      </div>
    </Modal>
  );

  if (variant === "detailed") {
    return (
      <div>
        <div
          role="radiogroup"
          aria-label="Permission mode"
          data-mode-group={groupId}
          onKeyDown={onKeyDown}
          className="grid gap-2.5 sm:grid-cols-3"
        >
          {MODES.map((m) => {
            const selected = state.mode === m.key;
            const disabled = m.key === "auto" && autoOff;
            return (
              <button
                key={m.key}
                type="button"
                role="radio"
                aria-checked={selected}
                aria-disabled={disabled || undefined}
                aria-describedby={`${groupId}-${m.key}`}
                data-mode={m.key}
                tabIndex={selected ? 0 : -1}
                onClick={() => choose(m.key)}
                className={`min-h-[44px] rounded-2xl border p-4 text-left transition ${
                  selected
                    ? "border-accent/50 bg-accent-dim shadow-glow-sm"
                    : disabled
                      ? "cursor-not-allowed border-line opacity-55"
                      : "border-line hover:bg-raised"
                }`}
              >
                <div className="mb-1 flex items-center gap-2 text-[14px] font-medium">
                  {m.label}
                  {saving === m.key && <Spinner />}
                </div>
                <div id={`${groupId}-${m.key}`} className="text-[13px] leading-relaxed text-muted">
                  {m.line}
                </div>
                {disabled && (
                  <div className="mt-2 text-[12.5px] leading-relaxed text-amber">
                    Not available: {AUTO_UNAVAILABLE_REASON}
                  </div>
                )}
              </button>
            );
          })}
        </div>
        {actingAs && (
          <p className="mt-2.5 text-[13px] leading-relaxed text-amber">
            Auto can&apos;t run right now, so AURA is acting as Ask first. {AUTO_UNAVAILABLE_REASON}
          </p>
        )}
        {confirm}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
      <span id={`${groupId}-label`} className="text-[12px] text-muted">
        Permission mode
      </span>
      <div
        role="radiogroup"
        aria-labelledby={`${groupId}-label`}
        data-mode-group={groupId}
        onKeyDown={onKeyDown}
        className="inline-flex items-center gap-0.5 rounded-full border border-line bg-raised/40 p-0.5"
      >
        {MODES.map((m) => {
          const selected = state.mode === m.key;
          const disabled = m.key === "auto" && autoOff;
          return (
            <button
              key={m.key}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-disabled={disabled || undefined}
              aria-describedby={disabled ? `${groupId}-auto-why` : undefined}
              data-mode={m.key}
              tabIndex={selected ? 0 : -1}
              onClick={() => choose(m.key)}
              title={disabled ? AUTO_UNAVAILABLE_REASON : m.line}
              className={`inline-flex min-h-[44px] items-center gap-1.5 rounded-full px-3 text-[12.5px] font-medium transition ${
                selected
                  ? "bg-accent-dim text-accent-soft shadow-glow-sm"
                  : disabled
                    ? "cursor-not-allowed text-muted opacity-55"
                    : "text-muted hover:text-ink"
              }`}
            >
              {m.label}
              {saving === m.key && <Spinner />}
            </button>
          );
        })}
      </div>
      {autoOff && (
        <span id={`${groupId}-auto-why`} className="text-[12px] text-muted">
          Auto is off: {AUTO_UNAVAILABLE_REASON}
        </span>
      )}
      {actingAs && (
        <span className="text-[12px] text-amber">Auto can&apos;t run now; acting as Ask first.</span>
      )}
      {confirm}
    </div>
  );
}
