# AURA AI for your computer

The desktop app is AURA in one download: the API, the background worker and the
web app run as a single program on your machine, and it opens in your browser
already signed in. Below the trust level you choose, everything it wants to do
on the computer waits for your approval.

## Get it

The home page's **Download** button picks the right file for your system. All
builds are on the [latest release](https://github.com/udaysk-lab/AURA-AI-/releases/latest):

| System | File |
| --- | --- |
| Windows 10/11 | `AURA-AI-Setup-Windows-x64.exe` (installs for you alone, no administrator password) |
| Windows, no installer | `AURA-AI-Windows-x64-portable.zip` — unzip, run `AURA AI.exe` |
| Mac, Apple silicon | `AURA-AI-macOS-AppleSilicon.zip` |
| Mac, Intel | `AURA-AI-macOS-Intel.zip` |
| Linux x86-64 | `AURA-AI-Linux-x64.tar.gz` — `tar xzf`, run `./aura-ai/aura-ai` |

`SHA256SUMS.txt` on the release lists every file's checksum.

The builds aren't code-signed yet, so the first launch needs one extra click:

- **Windows**: "Windows protected your PC" → **More info → Run anyway**.
- **macOS**: right-click **AURA AI → Open**, or **System Settings → Privacy &
  Security → Open Anyway**. For screen control, allow **Screen Recording** and
  **Accessibility** when macOS asks.

## What happens when you open it

1. AURA makes its data folder (below) and a settings file with fresh secrets.
2. It starts on `http://127.0.0.1:8765` (or a free port, if something else has
   8765) and opens your browser at a sign-in link carrying a key only your user
   account can read, so it signs you straight in.
3. A small window stays open: **Open AURA** brings the browser back, **Stop
   AURA** shuts it down, and it says when a newer release is out.

Opening it a second time just opens the browser on the copy already running.

Without a model key AURA runs a limited offline demo. Add one in **Settings →
AI model**: a Claude key from console.anthropic.com, an OpenAI key, or a free
local model through Ollama or LM Studio. It is saved on your computer only.

## Where your data lives

Everything is in one folder, `~/.aura` (`C:\Users\<you>\.aura` on Windows):

| | |
| --- | --- |
| `aura.env` | settings: model key, device limits, remote access, generated secrets |
| `aura.db` | chats, memory, tasks, approvals — everything AURA knows |
| `logs/aura.log` | what happened, when something didn't work |
| `commands/` | output of commands AURA ran in the background |

AURA's file tools can never touch this folder or the folder the app is
installed in, so the assistant can't rewrite its own settings or code.
Uninstalling keeps the folder; delete it yourself to remove everything.

`aura.env` takes the same settings as the `.env.example` at the repo root (the
device section is the interesting part: allowed folders, exec mode, denylist).
Restart AURA after editing it.

## Use it from your phone

**Settings → Use AURA from your phone** turns on remote access; restart AURA
once (Stop AURA, then open it again) and it listens on your network. **Pair a
device** then shows this computer's addresses and a one-time 8-character code
to type on the phone. A paired phone gets the same chat, screens and approval cards, but can't
change the model or remote settings, run console commands or pair more
devices; unpairing signs it out at once. Traffic on your network isn't
encrypted, so use it on a network you trust or over Tailscale.

## Build it yourself

You need Python 3.12, Node 22 and, for the Windows installer, Inno Setup 6.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt -r backend/requirements-device.txt "pyinstaller==6.22.3"
python desktop/build.py
```

`build.py` builds the web app as static files (`npm run build:desktop`), bundles
it with the backend through PyInstaller (`desktop/aura.spec`), and packages the
result in `build/desktop/`:

| Built on | You get |
| --- | --- |
| Windows | `AURA AI/` and `AURA-AI-Windows-x64-portable.zip` |
| macOS | `AURA AI.app` and `AURA-AI-macOS-AppleSilicon.zip` (or `-Intel`) |
| Linux | `aura-ai/` and `AURA-AI-Linux-x64.tar.gz` |

The Windows installer wraps the app folder:

```bat
iscc /DAppVersion=1.0.0 "/DSourceDir=%CD%\build\desktop\AURA AI" "/DOutputDir=%CD%\build\desktop" desktop\windows\aura.iss
```

Check a build answers before you ship it:

```bash
./build/desktop/aura-ai/aura-ai --smoke-test --smoke-report smoke.json
```

The smoke test starts the app on a throwaway data folder with the offline
model, checks the web app, the Host guard, the owner sign-in (and that no
other account can be made), device control, a real shell command, the settings
screens and a chat reply, then exits
non-zero if anything failed. The app has no console on Windows, so read the
report file there.

## Run it from source

```bash
cd frontend && npm ci && npm run build:desktop && cd ..
python backend/aura_desktop.py               # the app, with its window
python backend/aura_desktop.py --headless    # no window or browser; prints the sign-in link
```

Other options: `--port 9000`, `--data-dir <folder>`, `--no-browser`,
`--version`, `--smoke-test`. `AURA_HOME` moves the data folder too.

## Publishing a release

See [DEPLOY.md](../DEPLOY.md#the-desktop-app): bump `backend/app/version.py`,
update `RELEASE_NOTES.md`, push a matching `v` tag. The **Desktop release**
workflow builds and smoke-tests every platform, installs and re-tests the
Windows installer, and publishes the files the home page links to.

## Files here

```
desktop/
  aura.spec            PyInstaller recipe (one folder, no console window)
  build.py             build + package for the current platform
  windows/aura.iss     Inno Setup installer (per-user, Start menu, optional desktop icon)
  assets/              icon as SVG, PNG, .ico and .icns
  RELEASE_NOTES.md     the text of the GitHub release
backend/aura_desktop.py   the launcher: data folder, settings, server, window, smoke test
```
