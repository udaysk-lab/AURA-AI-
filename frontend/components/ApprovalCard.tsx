"use client";

import { useState } from "react";
import {
  AppWindow,
  Check,
  Clock4,
  FolderOpen,
  ShieldAlert,
  Trash2,
  X,
} from "lucide-react";
import {
  ApprovalOption,
  DecisionOutcome,
  PendingAction,
  StreamEvent,
  api,
  continueChat,
} from "@/lib/api";
import {
  OPTION_ORDER,
  describeProposedRule,
  humanTool,
  levelLabel,
} from "@/lib/permissions";
import { useToast } from "./Toast";
import { Badge, Spinner, fmtRelative, refreshBadges, toDate } from "./ui";

const ORIGIN_LABEL: Record<string, string> = {
  schedule: "From a schedule",
  automation: "From an automation",
  inbound: "From a message you sent in",
};

/** The preview ends with "Why I'm asking: …"; the card shows the reason on its own line. */
function previewBody(action: PendingAction): string {
  const text = action.preview || "";
  if (!action.reason) return text;
  const at = text.search(/\n*Why I.m asking:/i);
  return (at >= 0 ? text.slice(0, at) : text).trim();
}

function expiryText(expiresAt: string | null | undefined): { text: string; expired: boolean } | null {
  if (!expiresAt) return null;
  const when = toDate(expiresAt).getTime();
  if (Number.isNaN(when)) return null;
  if (when <= Date.now()) return { text: "This request has expired.", expired: true };
  return { text: `Waiting. Expires ${fmtRelative(expiresAt)}.`, expired: false };
}

export interface ApprovalCardProps {
  action: PendingAction;
  /** Called after the answer is recorded (and any continue call has finished). */
  onDecided?: (action: PendingAction, outcome: DecisionOutcome) => void;
  /** Gets the events of a continued task, if the screen wants to show them. */
  onContinueEvent?: (event: StreamEvent) => void;
}

/**
 * One approval, wherever it appears (chat, Today, notifications, automations).
 * It shows what AURA wants to do, why, when the question lapses, and exactly
 * the answers the server offered. Nothing is added on this side.
 */
export default function ApprovalCard({ action, onDecided, onContinueEvent }: ApprovalCardProps) {
  const toast = useToast();
  const [busy, setBusy] = useState<ApprovalOption | null>(null);
  const [error, setError] = useState("");

  const kind = action.card ?? "tool";
  const payload = action.card_payload ?? {};
  const expiry = expiryText(action.expires_at);
  const expired = expiry?.expired ?? false;
  const toolName = humanTool(action.tool_name).toLowerCase();
  const origin = action.origin ? ORIGIN_LABEL[action.origin] : undefined;

  const options = OPTION_ORDER.filter((o) => action.options?.includes(o));
  const rule = describeProposedRule(payload, action.tool_name);

  const path = typeof payload.path === "string" ? payload.path : "";
  const appName = String(payload.display_name || payload.app_name || payload.app_id || payload.app || "");
  const tier = typeof payload.tier === "string" ? payload.tier : "";

  let title = `AURA wants to ${toolName}`;
  let Icon = ShieldAlert;
  if (kind === "folder_access") {
    title = "AURA wants to use a folder";
    Icon = FolderOpen;
  } else if (kind === "app_grant") {
    title = "AURA wants to use an app";
    Icon = AppWindow;
  } else if (kind === "delete_permission") {
    title = "AURA wants to delete files in a folder";
    Icon = Trash2;
  }

  const labelFor = (o: ApprovalOption): string => {
    switch (o) {
      case "approve":
        if (kind === "folder_access") return "Connect this folder";
        if (kind === "app_grant")
          return tier ? `Allow ${levelLabel(tier).toLowerCase()}` : "Allow this app";
        if (kind === "delete_permission") return "Allow deleting here, and delete";
        return "Allow";
      case "once":
        return "Allow once";
      case "window":
        return "Allow for 10 minutes";
      case "always":
        return "Always allow";
      case "reject":
        return "Deny";
    }
  };

  const decide = async (option: ApprovalOption) => {
    if (busy) return;
    setBusy(option);
    setError("");
    try {
      const outcome = await api.decidePending(action.id, option);
      // The answer is recorded; the paused task may now be ready to carry on.
      if (outcome.work_item?.status === "ready" && outcome.work_item.id) {
        try {
          await continueChat(outcome.work_item.id, onContinueEvent);
        } catch (e) {
          toast.error(
            e instanceof Error
              ? `Your answer was saved, but the task couldn't carry on: ${e.message}`
              : "Your answer was saved, but the task couldn't carry on."
          );
        }
      }
      refreshBadges();
      onDecided?.(action, outcome);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't record that answer.");
    } finally {
      setBusy(null);
    }
  };

  const buttonClass = (o: ApprovalOption) =>
    o === "once" || o === "approve"
      ? "btn-primary"
      : o === "reject"
        ? "btn-quiet"
        : "btn-ghost";

  const headingId = `approval-${action.id}`;

  return (
    <section
      aria-labelledby={headingId}
      className="panel-raised space-y-3 border border-amber/35 p-4"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Icon size={15} className="shrink-0 text-amber" aria-hidden />
        <h3 id={headingId} className="min-w-0 flex-1 text-[14px] font-semibold">
          {title}
        </h3>
        {origin && <Badge>{origin}</Badge>}
        {action.always_user && <Badge tone="warning">Always asks you</Badge>}
      </div>

      {kind === "tool" && previewBody(action) && (
        <pre className="whitespace-pre-wrap break-words font-sans text-[13px] leading-relaxed text-muted">
          {previewBody(action)}
        </pre>
      )}

      {kind === "folder_access" && (
        <p className="break-words text-[13px] leading-relaxed text-muted">
          Folder:{" "}
          <code className="rounded-md border border-line bg-canvas/60 px-1.5 py-0.5 text-[12px] text-ink">
            {path || previewBody(action) || "not named"}
          </code>
          <br />
          It will be connected without permission to delete files. You can change that on
          the This computer page.
        </p>
      )}

      {kind === "app_grant" && (
        <p className="break-words text-[13px] leading-relaxed text-muted">
          App: <span className="text-ink">{appName || "not named"}</span>
          {tier && (
            <>
              <br />
              Access: <span className="text-ink">{levelLabel(tier)}</span>
            </>
          )}
        </p>
      )}

      {kind === "delete_permission" && (
        <p className="break-words text-[13px] leading-relaxed text-muted">
          {path ? (
            <>
              Folder:{" "}
              <code className="rounded-md border border-line bg-canvas/60 px-1.5 py-0.5 text-[12px] text-ink">
                {path}
              </code>
              <br />
            </>
          ) : null}
          {previewBody(action) && <span>{previewBody(action)}</span>}
          <br />
          Answering yes allows deleting files in this folder and deletes now. Each later
          delete still asks you.
        </p>
      )}

      {action.reason && (
        <p className="text-[13px] leading-relaxed text-muted">
          <span className="font-medium text-ink">Why I&apos;m asking:</span> {action.reason}
        </p>
      )}

      {expiry && (
        <p
          className={`flex items-center gap-1.5 text-[12.5px] ${
            expired ? "text-rose" : "text-muted"
          }`}
        >
          <Clock4 size={12} aria-hidden /> {expiry.text}
        </p>
      )}

      {options.includes("always") && !expired && (
        <p className="text-[12.5px] leading-relaxed text-muted">
          <span className="font-medium text-ink">Always allow</span> means:{" "}
          {rule ?? "a saved rule for this kind of action. You can change or remove it in Settings, under Permissions."}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-rose/30 bg-rose/10 px-3 py-2 text-[13px] leading-relaxed text-rose"
        >
          {error}
        </p>
      )}

      {options.length === 0 && !expired && (
        <p className="text-[12.5px] text-muted">
          This question can&apos;t be answered from here. Open AURA on the computer it is running on.
        </p>
      )}

      {options.length > 0 && !expired && (
        <div className="flex flex-wrap gap-2">
          {options.map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => void decide(o)}
              disabled={busy !== null}
              className={`${buttonClass(o)} min-h-[44px]`}
            >
              {busy === o ? (
                <Spinner />
              ) : o === "reject" ? (
                <X size={14} aria-hidden />
              ) : o === "window" ? (
                <Clock4 size={14} aria-hidden />
              ) : o === "once" || o === "approve" ? (
                <Check size={14} aria-hidden />
              ) : null}
              {labelFor(o)}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

/** A stack of cards with the standard spacing. */
export function ApprovalCards({
  actions,
  onDecided,
  onContinueEvent,
}: {
  actions: PendingAction[];
  onDecided?: ApprovalCardProps["onDecided"];
  onContinueEvent?: ApprovalCardProps["onContinueEvent"];
}) {
  return (
    <div className="space-y-3">
      {actions.map((a) => (
        <ApprovalCard key={a.id} action={a} onDecided={onDecided} onContinueEvent={onContinueEvent} />
      ))}
    </div>
  );
}
