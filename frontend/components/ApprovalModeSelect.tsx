"use client";

import { useEffect, useId, useState } from "react";
import { ApprovalMode, api } from "@/lib/api";
import { AUTO_UNAVAILABLE_REASON } from "@/lib/permissions";

/**
 * "When it needs an approval and you're away": Ask first or Auto. Used by the
 * schedule and automation forms and on each saved item.
 */
export default function ApprovalModeSelect({
  value,
  onChange,
  disabled = false,
  compact = false,
}: {
  value: ApprovalMode;
  onChange: (next: ApprovalMode) => void;
  disabled?: boolean;
  /** Label above on forms; inline and shorter on a saved item. */
  compact?: boolean;
}) {
  const id = useId();
  const [autoAvailable, setAutoAvailable] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void api
      .permissions()
      .then((p) => !cancelled && setAutoAvailable(p.auto_available))
      .catch(() => {
        /* leave Auto selectable; the server decides */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className={compact ? "flex flex-wrap items-center gap-2" : ""}>
      <label htmlFor={id} className={compact ? "text-[12px] text-muted" : "label mb-1.5 block"}>
        {compact ? "Approvals" : "When it needs an approval"}
      </label>
      <select
        id={id}
        className={`input ${compact ? "w-auto min-w-[9rem]" : ""} min-h-[44px] py-1.5`}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as ApprovalMode)}
        aria-describedby={`${id}-help`}
      >
        <option value="ask">Ask first</option>
        <option value="auto" disabled={!autoAvailable && value !== "auto"}>
          Auto{!autoAvailable ? " (not available)" : ""}
        </option>
      </select>
      {!compact && (
        <p id={`${id}-help`} className="mt-1.5 text-[12.5px] leading-relaxed text-muted">
          Ask first: it waits and tells you. Auto: a separate safety check decides.
          {!autoAvailable && ` ${AUTO_UNAVAILABLE_REASON}`}
        </p>
      )}
      {compact && (
        <span id={`${id}-help`} className="sr-only">
          Ask first waits for you. Auto lets a separate safety check decide.
        </span>
      )}
    </div>
  );
}
