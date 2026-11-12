"""Canonical application ids, so one app has one name on every platform.

"Google Chrome", "chrome.exe", "com.google.Chrome" and "google-chrome" are the
same program. Rules, grants and the blocklist compare canonical ids by equality
(permissions spec 10, section 5.5), so every place that names an app goes
through `canonical` first.

Pure functions, no I/O: safe to import anywhere and to test on any platform.
"""

from __future__ import annotations

import re

# Stripped from the end of a name, repeatedly ("Foo.app.desktop" -> "foo").
_SUFFIXES = (".appref-ms", ".desktop", ".lnk", ".exe", ".app")

# Reverse-DNS bundle ids start with one of these ("com.google.chrome").
_BUNDLE_PREFIXES = frozenset({"com", "org", "net", "io", "app", "dev", "co", "edu", "gov"})

# canonical-before-alias -> canonical id. Keys are already collapsed ("a-b").
ALIASES: dict[str, str] = {
    # browsers
    "google-chrome": "chrome", "chrome-exe": "chrome", "google-chrome-stable": "chrome",
    "google-chrome-beta": "chrome", "google-chrome-canary": "chrome",
    "chromium-browser": "chromium",
    "microsoft-edge": "edge", "msedge": "edge", "microsoft-edge-stable": "edge",
    "mozilla-firefox": "firefox", "firefox-esr": "firefox",
    "brave-browser": "brave", "brave-browser-stable": "brave",
    "opera-stable": "opera",
    # editors and IDEs
    "code": "vscode", "visual-studio-code": "vscode", "vs-code": "vscode", "code-oss": "vscode",
    "code-insiders": "vscode", "visual-studio-code-insiders": "vscode",
    "intellij-idea-ce": "intellij-idea", "intellij-idea-community": "intellij-idea",
    "intellij-idea-ultimate": "intellij-idea", "idea": "intellij-idea", "idea64": "intellij-idea",
    "pycharm-community": "pycharm", "pycharm64": "pycharm", "pycharm-professional": "pycharm",
    "webstorm64": "webstorm",
    "studio64": "android-studio", "studio": "android-studio",
    "sublime-text-3": "sublime-text", "sublime-text-4": "sublime-text", "sublime": "sublime-text",
    "subl": "sublime-text",
    "devenv": "visual-studio",
    # terminals
    "wt": "windows-terminal", "windowsterminal": "windows-terminal",
    "iterm": "iterm2", "iterm-2": "iterm2",
    "command-prompt": "cmd", "windows-command-prompt": "cmd",
    "windows-powershell": "powershell", "powershell-ise": "powershell",
    "terminal-app": "terminal", "gnome-terminal-server": "gnome-terminal",
}

TERMINALS = frozenset({
    "terminal", "iterm2", "windows-terminal", "cmd", "powershell", "pwsh", "gnome-terminal",
    "konsole", "alacritty", "kitty", "wezterm", "xterm", "hyper", "warp",
})
IDES = frozenset({
    "vscode", "cursor", "pycharm", "intellij-idea", "webstorm", "android-studio", "xcode",
    "sublime-text", "zed", "visual-studio",
})
BROWSERS = frozenset({
    "chrome", "edge", "firefox", "safari", "brave", "opera", "arc", "vivaldi", "chromium",
})


def canonical(name: str) -> str:
    """The canonical id for an application name, a path or a bundle id.

    Returns "" when nothing alphanumeric is left; callers treat that as a bad
    argument, never as a match.
    """
    text = (name or "").strip()
    text = re.split(r"[\\/]", text.rstrip("\\/"))[-1]  # strip the directory, either separator
    text = text.lower().strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
    parts = text.split(".")
    if len(parts) >= 3 and parts[0] in _BUNDLE_PREFIXES and all(parts):
        text = parts[-1]  # com.google.chrome -> chrome; com.apple.dt.xcode -> xcode
    text = re.sub(r"[\W_]+", "-", text).strip("-")
    return ALIASES.get(text, text)


def is_terminal(app_id: str) -> bool:
    return canonical(app_id) in TERMINALS


def is_ide(app_id: str) -> bool:
    return canonical(app_id) in IDES


def is_browser(app_id: str) -> bool:
    return canonical(app_id) in BROWSERS
