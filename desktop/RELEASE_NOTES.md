## AURA AI 2.0.0 for your computer

Windows, macOS and Linux. Database revision at the end of this release: `tl02_preferences`.

AURA is now a personal employee. It works from the first message: you give it an assignment, it plans, uses the systems you have assigned to it, checks what it changed and reports. You decide what it may do on its own.

### What changed

- **No more stages or trust levels.** AURA no longer has to be "raised", and nothing is unlocked by how long you have used it. The four trust levels are replaced by a **permission mode** that only you can set: **Ask first** (the default for new accounts), **Auto** (a separate safety reviewer checks each action, and AURA asks you after repeated blocks) and **Skip approvals**.
- **Rules.** Block, ask or allow rules per system or tool, optionally limited to a path, command, recipient, address or app. Block is checked first, then ask, then allow. One gate decides every action, from chat, schedules, automations and channels alike.
- **Some things always ask,** in every mode: permanently deleting files, any command that deletes, connecting a folder or app, allowing deletes in a folder, and changing permissions or settings. AURA cannot change its own mode, rules or settings.
- **Tasks.** Each assignment runs as a loop: recall similar past work and your preferences, plan, act, verify, report. Approving an action continues the same task. There is a task panel and a Stop button.
- **Memory.** Two new memories: **past work** (a record of each task) and **preferences** (how you want each type of task done, learned only from your own messages). You can view, edit, turn off, pause and reset them. Secrets are removed before anything is stored.
- **Systems.** Plugins are now systems you assign. A tool runs only if its system is assigned.
- **On this computer, in the app.** Connected folders, delete permission per folder, the apps AURA may see or use (at three levels), the list of blocked apps, and screen control are all managed in the app and apply at once. AURA can ask for a folder or an app with a card only you can answer. Banking, trading, crypto and password manager apps are blocked by default.
- **Schedules and automations** each have their own approval setting (Ask or Auto). They never use the screen, clipboard or apps, and automations and channel messages never run commands.
- **A permission change log** records every decision other than a plain read, every approval, and every change to your permissions. You are notified of each change, and urgently when it gives AURA more room.
- **Channels.** A message starts a task only if it comes from the owner you confirmed on that channel.
- **A sign-in key** is required on every local install, so another account on the same computer cannot sign in as you.

### The first time you open 2.0 on an existing install

- The database is **upgraded after a backup copy** is saved in `~/.aura/backups/`. If the upgrade cannot finish, AURA tells you why, changes nothing and exits.
- Your **existing settings are carried over** as a mode and rules, so AURA keeps behaving the way you chose. Nobody is moved to Auto. Strict, conservative and relaxed become Ask first with matching rules; full access becomes Skip approvals. "Always" approvals become allow rules, and your old command settings become command rules.
- The **folders you had allowed** become connected folders.
- **Channels need their owner confirmed** in Channels before messages on them start tasks. Until you do, they are kept as notifications.
- **Commands that delete files always ask,** whatever your mode or rules.
- Preferences you had taught AURA are moved into the new preference memory.
- Approvals still waiting from 1.x are given an expiry.

### Download

| | File |
|---|---|
| **Windows 10/11** (recommended) | `AURA-AI-Setup-Windows-x64.exe` — installs for you alone, no administrator password |
| Windows, no installer | `AURA-AI-Windows-x64-portable.zip` — unzip anywhere and run `AURA AI.exe` |
| **Mac with Apple silicon** (M1 and later) | `AURA-AI-macOS-AppleSilicon.zip` |
| Mac with an Intel processor | `AURA-AI-macOS-Intel.zip` |
| **Linux** (x86-64) | `AURA-AI-Linux-x64.tar.gz` — `tar xzf` it, then run `./aura-ai/aura-ai` |

`SHA256SUMS.txt` lists a checksum for every file.

### The first time you open it

- **Windows** may say it protected your PC, because the app isn't code-signed yet. Choose **More info → Run anyway**.
- **macOS** can't check the app because it isn't notarised yet. Right-click **AURA AI → Open**, or use **System Settings → Privacy & Security → Open Anyway**. To let AURA see the screen, allow **Screen Recording** and **Accessibility** when asked.

AURA then opens in your browser, already signed in. A small window lets you reopen or stop it.

### Connect a model

Without one, AURA runs a limited offline demo, and Auto mode is unavailable. In **Settings → AI model**, paste a Claude key (console.anthropic.com → API Keys) or an OpenAI key, or point it at a free model running on your machine with Ollama or LM Studio. The key is saved on your computer only, in `~/.aura/aura.env`.

### Good to know

- Commands AURA runs are **not sandboxed**: they run as you. What stands in front of them is the permission gate, your rules, the blocks on AURA's own files and protected locations, and the change log. Read `DEVICE.md` before turning on Skip approvals.
- Auto is a second model's judgement and can be wrong.
- Remote access from a phone is plain HTTP; use a network you trust or Tailscale. A phone can answer an approval "once" or "No" but cannot give longer allowances or change permissions.
- Your data stays in `~/.aura`: chats, memory, tasks, settings, backups and logs.

### Release history

- **2.0.0**: this release.
- **1.0.0**: the first release of AURA AI for your computer.

Source code: https://github.com/udaysk-lab/AURA-AI-
