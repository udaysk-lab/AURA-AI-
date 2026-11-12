import { StreamEvent, continueChat } from "@/lib/api";

/**
 * How many times one page load may continue the same task on its own.
 * Spec section 7: ceil(max_task_steps / max_agent_steps), with the defaults
 * 40 and 8. The server's own step cap is the real limit; this one only stops
 * a page from looping if the server keeps answering "continue".
 */
export const MAX_AUTO_CONTINUES = Math.ceil(40 / 8);

const continued = new Map<string, number>();

/** Continuations this page load has already made for a task. */
export function continuesUsed(workItemId: string): number {
  return continued.get(workItemId) ?? 0;
}

export function canContinue(workItemId: string): boolean {
  return continuesUsed(workItemId) < MAX_AUTO_CONTINUES;
}

export interface DriveResult {
  /** Segments that actually ran. */
  segments: number;
  /** True when the cap stopped us while the task still wanted more. */
  capped: boolean;
  /** True when the server answered 409 (someone else holds it, or not ready). */
  busy: boolean;
  error?: string;
}

/**
 * Continue a task and keep going while the server says there is more to do
 * (a `continue` event, or `done` with work_status "ready"), up to the cap.
 * Used wherever an approval is answered, so a decision on any screen carries
 * the task on, not only in the chat.
 */
export async function driveTask(
  workItemId: string,
  onEvent: (e: StreamEvent) => void = () => {},
  signal?: AbortSignal
): Promise<DriveResult> {
  const result: DriveResult = { segments: 0, capped: false, busy: false };
  while (true) {
    if (signal?.aborted) return result;
    if (!canContinue(workItemId)) {
      result.capped = true;
      return result;
    }
    continued.set(workItemId, continuesUsed(workItemId) + 1);
    let more = false;
    let ran = false;
    try {
      ran = await continueChat(
        workItemId,
        (e) => {
          if (e.type === "continue") more = true;
          if (e.type === "done" && e.work_status === "ready") more = true;
          if (e.type === "waiting" || e.type === "approval_needed") more = false;
          onEvent(e);
        },
        signal
      );
    } catch (err) {
      result.error = err instanceof Error ? err.message : "The task couldn't carry on.";
      return result;
    }
    if (!ran) {
      result.busy = true;
      return result;
    }
    result.segments += 1;
    if (!more) return result;
  }
}

/** The person chose to carry on after the cap: give the task a fresh allowance. */
export function resetContinues(workItemId: string) {
  continued.delete(workItemId);
}
