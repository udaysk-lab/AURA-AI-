/**
 * Wording and small helpers shared by the permission screens, the composer's
 * mode selector and the approval card. No network code here (see lib/api.ts).
 */
import type { AppTier, ApprovalOption, PermissionMode, RuleEffect } from "./api";

export const MODES: Array<{
  key: PermissionMode;
  label: string;
  /** One line, shown on the Permissions page. */
  line: string;
}> = [
  {
    key: "ask",
    label: "Ask first",
    line: "AURA reads on its own and asks before it changes or sends anything, unless a rule allows it.",
  },
  {
    key: "auto",
    label: "Auto",
    line: "AURA keeps working. A separate safety check reviews each action first and stops anything unsafe.",
  },
  {
    key: "skip",
    label: "Skip approvals",
    line: "Nothing asks, apart from the list below and your Ask and Block rules.",
  },
];

export const AUTO_UNAVAILABLE_REASON =
  "Needs a connected AI model. The built-in demo model can't review actions.";

/** Decision 5: these ask in every mode, and no rule or mode changes that. */
export const ALWAYS_NEEDS_YOU = [
  "Deleting files for good",
  "Connecting a folder, allowing an app, or allowing deletes in a folder",
  "Changing permissions, rules, systems, schedules, automations or AURA's settings",
];

export const EFFECTS: Array<{ key: RuleEffect; label: string }> = [
  { key: "allow", label: "Always allow" },
  { key: "ask", label: "Ask" },
  { key: "block", label: "Block" },
];

export const APP_LEVELS: Array<{ key: AppTier; label: string; help: string }> = [
  { key: "view", label: "Look only", help: "Can see the app's window. Cannot click or type." },
  { key: "click", label: "Click and scroll", help: "Can look, click and scroll. Cannot type." },
  { key: "full", label: "Click, scroll and type", help: "Can look, click, scroll and type." },
];

export const levelLabel = (tier: string) => APP_LEVELS.find((t) => t.key === tier)?.label ?? tier;

/** Terminals and code editors stay at click and scroll; commands go through the shell tool. */
const TERMINAL_IDS = [
  "terminal", "iterm2", "windows-terminal", "cmd", "powershell", "pwsh", "gnome-terminal",
  "konsole", "alacritty", "kitty", "wezterm", "xterm", "hyper", "warp",
];
const IDE_IDS = [
  "vscode", "cursor", "pycharm", "intellij-idea", "webstorm", "android-studio", "xcode",
  "sublime-text", "zed", "visual-studio",
];
export const isTerminalOrIde = (appId: string) => {
  const id = appId.trim().toLowerCase();
  return TERMINAL_IDS.includes(id) || IDE_IDS.includes(id);
};

/** "send_email" -> "Send email". */
export function humanTool(name: string): string {
  const words = name.replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : name;
}

/** "core-inbox" -> "Inbox" when no system name is known. */
export function humanSystem(id: string): string {
  if (id === "*") return "Every system";
  const words = id.replace(/^core-/, "").replace(/[_-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : id;
}

const TARGET_KIND_LABEL: Record<string, string> = {
  path: "Folder or file",
  command: "Command",
  recipient: "Recipient",
  domain: "Website",
  app: "App",
};
export const targetKindLabel = (kind: string) => TARGET_KIND_LABEL[kind] ?? kind;

/** What a rule is limited to, in words. */
export const describeTarget = (pattern?: string): string => pattern || "Anything";

/** The rule an "always" answer would store, as one sentence. Null if the API didn't say. */
export function describeProposedRule(
  payload: Record<string, any> | undefined,
  toolName: string
): string | null {
  if (!payload) return null;
  const raw = payload.proposed_rule ?? payload.rule_text ?? payload.rule;
  if (typeof raw === "string" && raw.trim()) return raw.trim();
  if (raw && typeof raw === "object") {
    const tool = humanTool(String(raw.tool || toolName)).toLowerCase();
    const pattern = String(raw.pattern || "");
    const where = !pattern
      ? ""
      : raw.target_kind === "recipient"
        ? ` to ${pattern}`
        : raw.target_kind === "path"
          ? ` for files in ${pattern}`
          : raw.target_kind === "command"
            ? ` for the command ${pattern}`
            : raw.target_kind === "domain"
              ? ` for ${pattern}`
              : raw.target_kind === "app"
                ? ` in ${pattern}`
                : ` for ${pattern}`;
    const scope = String(raw.applies_to || "");
    const only = scope.startsWith("schedule:")
      ? ", only in that schedule"
      : scope.startsWith("automation:")
        ? ", only in that automation"
        : "";
    return `Allow ${tool}${where}${only}, until you remove the rule`;
  }
  return null;
}

/** Backend order for buttons, whatever order the API lists them in. */
export const OPTION_ORDER: ApprovalOption[] = ["approve", "once", "window", "always", "reject"];
