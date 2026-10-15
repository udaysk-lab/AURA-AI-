The first release of **AURA AI for your computer** — Windows, macOS and Linux.

AURA is an AI assistant that reads your inbox, holds your calendar, tracks what you owe people and learns how you like things done. On your own computer it can also work the computer: run commands, find and edit files, open apps, check the system and, for programs with no command line, see the screen and use the mouse and keyboard. Below the trust level you choose, it asks first.

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

- **Windows** may say it protected your PC — the app isn't code-signed yet. Choose **More info → Run anyway**.
- **macOS** can't check the app because it isn't notarised yet. Right-click **AURA AI → Open**, or use **System Settings → Privacy & Security → Open Anyway**. To let AURA see the screen, allow **Screen Recording** and **Accessibility** when asked.

AURA then opens in your browser, already signed in. A small window lets you reopen or stop it.

### Connect a model

Without one, AURA runs a limited offline demo. In **Settings → AI model**, paste a Claude key (console.anthropic.com → API Keys) or an OpenAI key — or point it at a free model running on your machine with Ollama or LM Studio. The key is saved on your computer only, in `~/.aura/aura.env`.

### What's in it

- Every skill of AURA in the browser, plus seven for your computer: Command Line, File Access, Applications, System Monitor, Screen Control, Clipboard & Alerts, Web Fetch.
- What keeps it in check: your trust level and approval cards (with the real command on them), a folder fence that keeps the file tools away from keys, credentials and startup items, an optional command blocklist, and an app that only answers to your own computer.
- **Use it from your phone:** Settings → Use AURA from your phone. Pair once with a code; approve what AURA wants to do from wherever you are on your network (or anywhere, over Tailscale).
- Your data stays in `~/.aura` on your computer: chats, memory, tasks, settings and logs.

Source code: https://github.com/udaysk-lab/AURA-AI-
