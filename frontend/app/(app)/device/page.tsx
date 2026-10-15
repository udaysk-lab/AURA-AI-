"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowUp,
  Cpu,
  FolderOpen,
  HardDrive,
  Keyboard,
  Laptop,
  ListTree,
  MemoryStick,
  Monitor,
  RefreshCw,
  ShieldCheck,
  Square,
  Terminal,
  Timer,
} from "lucide-react";
import {
  DeviceBackgroundCommand,
  DeviceListing,
  DeviceProcess,
  DeviceRunResult,
  DeviceStatus,
  DeviceSystemSnapshot,
  api,
} from "@/lib/api";
import { useAction, useToast } from "@/components/Toast";
import {
  Badge,
  Card,
  EmptyState,
  PageHeader,
  SectionTitle,
  Skeleton,
  Spinner,
} from "@/components/ui";

/** One entry in the console's scrollback. */
interface ConsoleLine {
  id: number;
  command: string;
  result: DeviceRunResult | null;
}

const SKILL_LABELS: Record<string, string> = {
  DV01: "Command line",
  DV02: "File access",
  DV03: "Applications",
  DV04: "System monitor",
  DV05: "Screen control",
  DV06: "Clipboard & alerts",
  DV07: "Web fetch",
};

const MODE_TONE: Record<string, "success" | "warning" | "urgent"> = {
  ask: "success",
  allowlist: "warning",
  full: "urgent",
};

export default function DevicePage() {
  const run = useAction();
  const toast = useToast();

  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [snapshot, setSnapshot] = useState<DeviceSystemSnapshot | null>(null);
  const [loading, setLoading] = useState(true);

  const [command, setCommand] = useState("");
  const [lines, setLines] = useState<ConsoleLine[]>([]);
  const [running, setRunning] = useState(false);
  const consoleEnd = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  // Up/down through what you've already run, like a real shell.
  const history = useRef<string[]>([]);
  const historyAt = useRef(-1);

  const [listing, setListing] = useState<DeviceListing | null>(null);
  const [processes, setProcesses] = useState<DeviceProcess[]>([]);
  const [background, setBackground] = useState<DeviceBackgroundCommand[]>([]);

  const load = useCallback(async () => {
    try {
      const device = await api.device();
      setStatus(device);
      if (device.enabled) {
        // Each of these can legitimately be unavailable (no psutil, nothing
        // running yet), so none of them is allowed to break the screen.
        const [snap, files, procs, bg] = await Promise.all([
          api.deviceSystem().catch(() => null),
          api.deviceFiles(device.home).catch(() => null),
          api.deviceProcesses().catch(() => []),
          api.deviceBackground().catch(() => []),
        ]);
        setSnapshot(snap);
        setListing(files);
        setProcesses(procs);
        setBackground(bg);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't read this computer's status");
    } finally {
      setLoading(false);
    }
    // toast is memoised in ToastProvider.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep the background-command list honest while something is running.
  useEffect(() => {
    if (!status?.enabled || background.length === 0) return;
    const timer = setInterval(async () => {
      setBackground(await api.deviceBackground().catch(() => []));
    }, 5000);
    return () => clearInterval(timer);
  }, [status?.enabled, background.length]);

  useEffect(() => {
    consoleEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [lines, running]);

  const execute = async () => {
    const text = command.trim();
    if (!text || running) return;
    setCommand("");
    history.current = [text, ...history.current].slice(0, 50);
    historyAt.current = -1;
    const id = nextId.current++;
    setLines((prev) => [...prev, { id, command: text, result: null }]);
    setRunning(true);
    try {
      const result = await api.deviceRun(text);
      setLines((prev) => prev.map((l) => (l.id === id ? { ...l, result } : l)));
      // A command can start a server or move files; refresh what we show.
      setBackground(await api.deviceBackground().catch(() => []));
      if (listing) setListing(await api.deviceFiles(listing.path).catch(() => listing));
    } catch (e) {
      const message = e instanceof Error ? e.message : "The command couldn't be run";
      setLines((prev) =>
        prev.map((l) =>
          l.id === id
            ? {
                ...l,
                result: {
                  command: text, workdir: "", exit_code: null, ok: false, output: "",
                  truncated: false, duration_ms: 0, timed_out: false, error: message,
                },
              }
            : l
        )
      );
    } finally {
      setRunning(false);
    }
  };

  const browse = async (path: string) => {
    const next = await run(() => api.deviceFiles(path));
    if (next) setListing(next);
  };

  const stopBackground = async (sessionId: string) => {
    await run(async () => {
      await api.deviceStopBackground(sessionId);
      setBackground(await api.deviceBackground().catch(() => []));
    }, "Stopped");
  };

  if (loading) {
    return (
      <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-8">
        <Skeleton className="h-24" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  // --- Device control switched off --------------------------------------
  if (!status?.enabled) {
    return (
      <div className="mx-auto max-w-3xl space-y-5 p-4 sm:p-8">
        <PageHeader
          title="This computer"
          glow="violet"
          blurb="AURA can operate the machine it's installed on — run commands, work with your files, open apps, watch the system. That's switched off here."
        />
        <Card>
          <SectionTitle>Not switched on</SectionTitle>
          <p className="text-[13.5px] leading-relaxed text-muted">
            Device control is a local-only capability: it gives AURA your hands on a
            specific computer, so it only makes sense on a machine you own and are
            sitting at. This instance is running without it — which is the right
            setting for anything hosted or shared.
          </p>
          <div className="mt-4 rounded-xl border border-line bg-raised/40 p-4">
            <p className="mb-2 text-[12.5px] font-medium text-ink">To turn it on</p>
            <ol className="ml-4 list-decimal space-y-1.5 text-[12.5px] leading-relaxed text-muted">
              <li>
                Download the AURA desktop app from{" "}
                <a href="/#download" className="text-accent-soft underline-offset-2 hover:underline">
                  the home page
                </a>{" "}
                and open it. Everything here is set up, and it asks before acting.
              </li>
              <li>
                Or run it from source — <code className="text-accent-soft">start-aura-local.bat</code>{" "}
                on Windows, <code className="text-accent-soft">./aura-local.sh</code> on macOS or Linux.
              </li>
              <li>
                Or set <code className="text-accent-soft">LOCAL_MODE=true</code> in your{" "}
                <code className="text-accent-soft">.env</code> and restart the backend.
              </li>
              <li>
                For screen control, also install the extras:{" "}
                <code className="text-accent-soft">
                  pip install -r backend/requirements-device.txt
                </code>
              </li>
            </ol>
          </div>
        </Card>
      </div>
    );
  }

  const policy = status.policy;
  const missingPackages = Object.entries(status.packages)
    .filter(([, present]) => !present)
    .map(([name]) => name);

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-4 sm:p-8">
      <PageHeader
        title="This computer"
        glow="violet"
        blurb={
          <>
            AURA is running on <span className="text-ink">{status.hostname}</span> and can
            operate it. Everything it does here still goes through your trust tier and the
            same approval card as the rest of its work.
          </>
        }
        action={
          <button onClick={() => void load()} className="btn-ghost">
            <RefreshCw size={14} /> Refresh
          </button>
        }
      />

      {/* What this machine is, and what AURA may do on it */}
      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <SectionTitle>The machine</SectionTitle>
          <dl className="space-y-2 text-[12.5px]">
            <Row icon={<Laptop size={13} />} label="System" value={`${status.os} · ${status.arch}`} />
            <Row icon={<Terminal size={13} />} label="Shell" value={status.shell?.kind ?? "—"} />
            <Row icon={<FolderOpen size={13} />} label="Home" value={status.home} mono />
            <Row icon={<Cpu size={13} />} label="Signed in as" value={status.user} />
          </dl>

          {snapshot && (
            <div className="mt-4 grid grid-cols-3 gap-2 border-t border-line pt-4">
              <Gauge
                icon={<Cpu size={12} />}
                label="CPU"
                value={snapshot.cpu_percent != null ? `${snapshot.cpu_percent}%` : "—"}
              />
              <Gauge
                icon={<MemoryStick size={12} />}
                label="Memory"
                value={snapshot.memory ? `${snapshot.memory.percent}%` : "—"}
              />
              <Gauge
                icon={<HardDrive size={12} />}
                label="Disk free"
                value={snapshot.disk ? `${snapshot.disk.free_gb} GB` : "—"}
              />
            </div>
          )}
          {snapshot?.note && <p className="mt-3 text-[11.5px] text-faint">{snapshot.note}</p>}
        </Card>

        <Card>
          <SectionTitle>What it may do</SectionTitle>
          <div className="mb-3 flex flex-wrap gap-1.5">
            <Capability on={status.shell_control} icon={<Terminal size={11} />} label="Commands" />
            <Capability on={status.file_control} icon={<FolderOpen size={11} />} label="Files" />
            <Capability on={status.process_control} icon={<ListTree size={11} />} label="Processes" />
            <Capability on={status.screen_control} icon={<Monitor size={11} />} label="Screen" />
            <Capability on={status.clipboard} icon={<Keyboard size={11} />} label="Clipboard" />
          </div>

          {policy && (
            <div className="space-y-2.5 border-t border-line pt-3">
              <div className="flex items-start gap-2">
                <ShieldCheck size={13} className="mt-0.5 shrink-0 text-sage" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[12.5px] font-medium text-ink">Shell approval</span>
                    <Badge tone={MODE_TONE[policy.exec_mode] ?? "normal"}>
                      {policy.exec_mode}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-[11.5px] leading-relaxed text-muted">
                    {policy.explanation}
                  </p>
                </div>
              </div>
              <div>
                <p className="text-[11.5px] text-faint">Folders AURA may read and write</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {policy.allowed_roots.map((root) => (
                    <code
                      key={root}
                      className="rounded-md border border-line bg-raised/50 px-1.5 py-0.5 text-[11px] text-muted"
                    >
                      {root}
                    </code>
                  ))}
                </div>
              </div>
              {policy.denylist_active && (
                <p className="text-[11.5px] text-amber">
                  A command denylist is active — some commands are refused even after approval.
                </p>
              )}
            </div>
          )}

          {status.active_skills.length > 0 && (
            <div className="mt-3 border-t border-line pt-3">
              <p className="mb-1.5 text-[11.5px] text-faint">Device skills switched on</p>
              <div className="flex flex-wrap gap-1">
                {status.active_skills.map((code) => (
                  <Badge key={code} tone="violet">
                    {SKILL_LABELS[code] ?? code}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {missingPackages.length > 0 && (
            <p className="mt-3 border-t border-line pt-3 text-[11.5px] leading-relaxed text-amber">
              <AlertTriangle size={11} className="mr-1 inline" />
              Not installed: {missingPackages.join(", ")}. Run{" "}
              <code>pip install -r backend/requirements-device.txt</code> to add process
              stats, screen control and clipboard access.
            </p>
          )}
        </Card>
      </div>

      {/* Your own console — not the agent's */}
      <Card>
        <SectionTitle
          action={
            lines.length > 0 ? (
              <button onClick={() => setLines([])} className="btn-quiet py-1 text-[11.5px]">
                Clear
              </button>
            ) : undefined
          }
        >
          Console
        </SectionTitle>
        <p className="mb-3 text-[12px] leading-relaxed text-muted">
          Commands you type here run as you, straight away — no approval card, because you
          typed them. They&apos;re recorded in the activity trail either way. Ask AURA in
          chat when you want it to decide what to run.
        </p>

        <div className="max-h-80 space-y-3 overflow-y-auto rounded-xl border border-line bg-canvas/60 p-3 font-mono text-[12px]">
          {lines.length === 0 && (
            <p className="py-6 text-center font-sans text-[12.5px] text-faint">
              Nothing run yet. Try <code className="text-accent-soft">git status</code> or{" "}
              <code className="text-accent-soft">python --version</code>.
            </p>
          )}
          {lines.map((line) => (
            <div key={line.id}>
              <div className="flex gap-2 text-accent-soft">
                <span className="select-none text-faint">$</span>
                <span className="min-w-0 break-all">{line.command}</span>
              </div>
              {line.result === null ? (
                <div className="mt-1 flex items-center gap-2 font-sans text-[12px] text-faint">
                  <Spinner /> running…
                </div>
              ) : line.result.error ? (
                <p className="mt-1 whitespace-pre-wrap break-words font-sans text-[12px] text-rose">
                  {line.result.error}
                </p>
              ) : (
                <>
                  <pre className="mt-1 whitespace-pre-wrap break-words text-muted">
                    {line.result.output}
                  </pre>
                  <div className="mt-1 flex flex-wrap items-center gap-2 font-sans text-[10.5px] text-faint">
                    {line.result.timed_out ? (
                      <span className="text-amber">timed out</span>
                    ) : (
                      <span className={line.result.ok ? "text-sage" : "text-rose"}>
                        exit {line.result.exit_code}
                      </span>
                    )}
                    <span>{line.result.duration_ms}ms</span>
                    {line.result.truncated && <span>output truncated</span>}
                  </div>
                </>
              )}
            </div>
          ))}
          <div ref={consoleEnd} />
        </div>

        <div className="panel mt-3 flex items-end gap-2 p-2 transition-colors focus-within:border-accent/40">
          <span className="select-none pb-2.5 pl-2 font-mono text-[13px] text-faint">$</span>
          <input
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void execute();
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                const next = Math.min(historyAt.current + 1, history.current.length - 1);
                if (next >= 0) {
                  historyAt.current = next;
                  setCommand(history.current[next]);
                }
              } else if (e.key === "ArrowDown") {
                e.preventDefault();
                const next = historyAt.current - 1;
                historyAt.current = next;
                setCommand(next >= 0 ? history.current[next] : "");
              }
            }}
            placeholder="Type a command…"
            aria-label="Command to run on this computer"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent px-1 py-2.5 font-mono text-[12.5px] placeholder:font-sans placeholder:text-faint outline-none"
          />
          <button
            onClick={() => void execute()}
            disabled={!command.trim() || running}
            aria-label="Run"
            className="btn-primary h-9 w-9 shrink-0 rounded-full p-0"
          >
            {running ? <Spinner /> : <ArrowUp size={16} />}
          </button>
        </div>
      </Card>

      {/* Long-running work AURA started */}
      <Card>
        <SectionTitle>Running in the background</SectionTitle>
        {background.length === 0 ? (
          <EmptyState
            icon={<Timer size={20} />}
            title="Nothing running"
            hint="When AURA starts a server, a build or a watch, it appears here so you can see and stop it."
          />
        ) : (
          <div className="space-y-2">
            {background.map((job) => (
              <div
                key={job.session_id}
                className="flex flex-wrap items-center gap-2 rounded-xl border border-line bg-raised/40 px-3 py-2.5"
              >
                <Badge tone={job.status === "running" ? "success" : "normal"}>{job.status}</Badge>
                <code className="min-w-0 flex-1 truncate font-mono text-[12px] text-muted">
                  {job.command || job.session_id}
                </code>
                <span className="text-[11px] text-faint">{job.running_seconds}s</span>
                {job.status === "running" && (
                  <button
                    onClick={() => void stopBackground(job.session_id)}
                    className="btn-ghost py-1 text-[11.5px]"
                  >
                    <Square size={11} /> Stop
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Files, fenced to the allowed roots */}
        <Card>
          <SectionTitle>Files</SectionTitle>
          {!listing ? (
            <EmptyState title="Couldn't read that folder" />
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2">
                <button
                  onClick={() => void browse(parentOf(listing.path))}
                  disabled={!parentOf(listing.path)}
                  className="btn-quiet px-2 py-1 text-[11.5px] disabled:opacity-40"
                >
                  ↑ Up
                </button>
                <code className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-muted">
                  {listing.path}
                </code>
              </div>
              <div className="max-h-64 space-y-0.5 overflow-y-auto">
                {listing.entries.length === 0 && (
                  <p className="py-4 text-center text-[12px] text-faint">Empty folder.</p>
                )}
                {listing.entries.map((entry) => (
                  <div
                    key={entry.path}
                    className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] hover:bg-raised/60"
                  >
                    {entry.is_dir ? (
                      <button
                        onClick={() => void browse(entry.path)}
                        className="flex min-w-0 flex-1 items-center gap-2 text-left text-ink"
                      >
                        <FolderOpen size={12} className="shrink-0 text-accent-soft" />
                        <span className="truncate">{entry.name}</span>
                      </button>
                    ) : (
                      <>
                        <span className="w-3 shrink-0" />
                        <span className="min-w-0 flex-1 truncate text-muted">{entry.name}</span>
                        <span className="shrink-0 text-[10.5px] text-faint">
                          {fmtSize(entry.size)}
                        </span>
                      </>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>

        {/* Processes */}
        <Card>
          <SectionTitle
            action={
              <button
                onClick={async () => setProcesses(await api.deviceProcesses().catch(() => []))}
                className="btn-quiet px-2 py-1 text-[11.5px]"
              >
                <RefreshCw size={11} />
              </button>
            }
          >
            Heaviest processes
          </SectionTitle>
          {!status.process_control ? (
            <EmptyState
              icon={<ListTree size={20} />}
              title="Process stats need one more package"
              hint="pip install -r backend/requirements-device.txt"
            />
          ) : processes.length === 0 ? (
            <EmptyState title="Nothing to show" />
          ) : (
            <div className="max-h-64 space-y-0.5 overflow-y-auto">
              {processes.slice(0, 15).map((proc) => (
                <div
                  key={proc.pid}
                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px]"
                >
                  <span className="min-w-0 flex-1 truncate text-muted">{proc.name}</span>
                  <span className="shrink-0 text-[10.5px] text-faint">{proc.pid}</span>
                  <span className="w-12 shrink-0 text-right text-[11px] text-ink">
                    {proc.memory_percent}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bits
// ---------------------------------------------------------------------------

function Row({
  icon,
  label,
  value,
  mono = false,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="shrink-0 text-faint">{icon}</span>
      <dt className="shrink-0 text-faint">{label}</dt>
      <dd
        className={`ml-auto min-w-0 truncate text-right text-ink ${mono ? "font-mono text-[11.5px]" : ""}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

function Gauge({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-line bg-raised/40 px-2 py-2 text-center">
      <div className="flex items-center justify-center gap-1 text-faint">{icon}</div>
      <div className="mt-1 text-[14px] font-medium text-ink">{value}</div>
      <div className="text-[10.5px] text-faint">{label}</div>
    </div>
  );
}

function Capability({
  on,
  icon,
  label,
}: {
  on: boolean;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] ${
        on
          ? "border-sage/35 bg-sage/12 text-sage"
          : "border-line bg-raised/40 text-faint line-through decoration-faint/60"
      }`}
      title={on ? `${label}: available` : `${label}: not available on this machine`}
    >
      {icon}
      {label}
    </span>
  );
}

/** The parent folder, or "" at a root we shouldn't climb past. */
function parentOf(path: string): string {
  const separator = path.includes("\\") ? "\\" : "/";
  const parts = path.split(separator).filter(Boolean);
  if (parts.length <= 1) return "";
  parts.pop();
  const joined = parts.join(separator);
  return path.startsWith(separator) ? separator + joined : joined;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}
