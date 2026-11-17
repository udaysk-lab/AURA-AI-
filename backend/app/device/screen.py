"""Seeing and driving the screen.

Screenshots, mouse, and keyboard, through `pyautogui` when it's installed and a
desktop session exists. This is how AURA operates apps that have no command
line — the GUI equivalent of the shell. It degrades to a clear "unavailable"
when the package or the display is missing, so a headless box just reports that
screen control is off rather than erroring deep in a tool.

Coordinates are screen pixels. A screenshot is saved under AURA's own data folder and its path returned; the agent views it
with the normal image path, and the bytes never balloon the model's context.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata

from app.config import settings
from app.device.runtime import DeviceUnavailable, capabilities, require_enabled


def _gui():
    if not settings.device_screen_control:
        raise DeviceUnavailable("Screen control is switched off (DEVICE_SCREEN_CONTROL).")
    if not capabilities().get("screen_control"):
        raise DeviceUnavailable(
            "Screen control isn't available here — it needs a desktop session and the "
            "'pyautogui' package (pip install -r requirements-device.txt)."
        )
    import pyautogui  # type: ignore

    # Don't let a failsafe corner throw mid-task; the permission gate is the real brake.
    pyautogui.FAILSAFE = False
    return pyautogui


def _screens_dir() -> str:
    # Screenshots live in AURA's own data folder, never in a folder the user connected.
    from app.device.runtime import data_dir

    path = data_dir("screens")
    os.makedirs(path, exist_ok=True)
    return path


# A full-resolution screenshot is millions of pixels and costs a fortune in
# tokens for no accuracy gain. Downscale the copy the model sees to fit this
# box, and report the scale so coordinates can be mapped back to the real
# screen — clicking uses screen pixels, not image pixels.
_MODEL_IMAGE_MAX = 1280


def screenshot() -> dict:
    """Capture the screen, and hand the model something it can actually see.

    Returns the saved path plus the image itself (base64, downscaled). The
    coordinator attaches that to the next model call, which is what makes
    "look at the screen, then click the button" work rather than guess.
    """
    require_enabled()
    gui = _gui()
    path = os.path.join(_screens_dir(), f"screen-{int(time.time())}.png")
    img = gui.screenshot()
    img.save(path)
    width, height = img.size

    result = {
        "path": path,
        "width": width,
        "height": height,
        "message": (
            f"Captured the screen ({width}×{height}). The image is attached — read the "
            "coordinates you need from it. Click coordinates are screen pixels in this "
            f"{width}×{height} space."
        ),
    }

    encoded = _encode_for_model(img)
    if encoded:
        result["image"] = encoded
        result["scale"] = encoded.pop("_scale", 1.0)
        if result["scale"] != 1.0:
            result["message"] += (
                f" The attached copy is scaled to {result['scale']:.3f}× to save space, so "
                f"multiply any position you read off it by {1 / result['scale']:.3f} to get "
                "screen coordinates."
            )
    else:
        result["message"] += (
            " The image couldn't be attached, so you cannot see it — say so rather than "
            "guessing where to click."
        )
    return result


def _encode_for_model(img) -> dict | None:
    """Downscale and base64-encode a screenshot. None if that isn't possible."""
    import base64
    import io

    try:
        scale = 1.0
        width, height = img.size
        largest = max(width, height)
        if largest > _MODEL_IMAGE_MAX:
            scale = _MODEL_IMAGE_MAX / largest
            try:
                from PIL import Image  # Pillow arrives with pyautogui

                resample = getattr(Image, "Resampling", Image).LANCZOS
            except Exception:
                resample = None
            resized = (
                img.resize((int(width * scale), int(height * scale)), resample)
                if resample is not None
                else img.resize((int(width * scale), int(height * scale)))
            )
        else:
            resized = img

        buffer = io.BytesIO()
        resized.convert("RGB").save(buffer, format="PNG", optimize=True)
        return {
            "media_type": "image/png",
            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
            "_scale": scale,
        }
    except Exception:
        # No sight is recoverable; a crashed turn isn't.
        return None


# --- what the keyboard and mouse calls may carry ------------------------------------------------------------------

MAX_TEXT = 2000
MAX_CHORD = 4

_LETTERS = "abcdefghijklmnopqrstuvwxyz"
# The key names the input back end knows. Names that start a program by themselves (mail, calculator and other
# launch keys) or put the computer to sleep are left out on purpose.
KEY_NAMES = frozenset(
    list(_LETTERS) + list("0123456789") + list("!\"#$%&'()*,-./:;<=>?@[\\]^_`{|}~")
    + [f"f{i}" for i in range(1, 25)] + [f"num{i}" for i in range(10)]
    + [
        "alt", "altleft", "altright", "apps", "backspace", "browserback", "browserforward", "browserhome",
        "browserrefresh", "browsersearch", "browserstop", "capslock", "clear", "command", "option", "optionleft",
        "optionright", "ctrl", "ctrlleft", "ctrlright", "decimal", "del", "delete", "divide", "down", "end", "enter",
        "esc", "escape", "fn", "help", "home", "insert", "left", "multiply", "nexttrack", "numlock", "pagedown",
        "pageup", "pause", "pgdn", "pgup", "playpause", "prevtrack", "print", "printscreen", "prntscrn", "prtsc",
        "prtscr", "return", "right", "scrolllock", "shift", "shiftleft", "shiftright", "space", "stop", "subtract",
        "tab", "up", "volumedown", "volumemute", "volumeup", "win", "winleft", "winright", "add", "separator",
    ]
)
_KEY_ALIASES = {"cmd": "command", "control": "ctrl"}


def parse_chord(keys: object) -> list[str]:
    """One chord of at most four known key names from text such as 'ctrl+s'. Raises ValueError with a plain
    sentence when the text is not that."""
    if not isinstance(keys, str):
        raise ValueError("'keys' must be text.")
    combo = [_KEY_ALIASES.get(k, k) for k in (p.strip().lower() for p in re.split(r"[+\s]+", keys)) if k]
    if not combo:
        raise ValueError("'keys' names no key.")
    if len(combo) > MAX_CHORD:
        raise ValueError(f"'keys' is more than one chord: at most {MAX_CHORD} keys can be pressed together in one call.")
    unknown = [k for k in combo if k not in KEY_NAMES]
    if unknown:
        raise ValueError(f"'keys' has a key name that is not known: {unknown[0]!r}.")
    return combo


def _control_character(text: str) -> str | None:
    for ch in text:
        if ch not in "\n\t" and unicodedata.category(ch) == "Cc":
            return ch
    return None


def input_problem(tool: str, args: dict) -> str | None:
    """The plain sentence naming what is wrong with the mouse or keyboard arguments of one call, else None.
    The gate and the handlers both ask this, so they read the call the same way."""
    if tool == "type_text":
        text = args.get("text")
        if not isinstance(text, str):
            return "'text' must be text."
        if len(text) > MAX_TEXT:
            return f"'text' has more than {MAX_TEXT} characters."
        bad = _control_character(text)
        if bad is not None:
            return f"'text' has a control character (code {ord(bad)}); only new lines and tabs are allowed."
    elif tool == "press_keys":
        try:
            parse_chord(args.get("keys"))
        except ValueError as exc:
            return str(exc)
    elif tool in ("mouse_click", "scroll_screen"):
        x, y = args.get("x"), args.get("y")
        if (x is None) != (y is None):
            return "'x' and 'y' must be given together, or both left out."
        for name, v in (("x", x), ("y", y)):
            if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
                return f"'{name}' must be a whole number."
    return None


def click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
    """Click at exactly this point (the one the permission gate judged)."""
    require_enabled()
    gui = _gui()
    if not _whole(x) or not _whole(y):
        raise DeviceUnavailable("A click needs whole-number coordinates.")
    if button not in ("left", "right", "middle") or not _whole(clicks) or not 1 <= clicks <= 3:
        raise DeviceUnavailable("That button or click count is not one AURA uses.")
    gui.click(x=x, y=y, button=button, clicks=clicks)
    return {"message": f"{button.capitalize()}-clicked ({x}, {y})×{clicks}."}


def _whole(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def first_line(text: str) -> tuple[str, bool]:
    """The part of `text` up to and including its first new line, and whether a new line ended it."""
    cut = text.find("\n")
    return (text, False) if cut < 0 else (text[:cut + 1], True)


def pieces(text: str) -> list[str]:
    """`text` split after each tab, so the window can be checked again between the parts."""
    out: list[str] = []
    for part in text.split("\t"):
        out.append(part + "\t")
    out[-1] = out[-1][:-1]
    return [p for p in out if p]


def type_text(text: str, interval: float = 0.01, *, check=None) -> dict:
    """Type `text` up to and including its first new line, in pieces split after each tab. `check()` runs before each
    piece and returns None to go on or a sentence to stop with; typing then ends there. Whatever follows the first new
    line is not typed: what a submit does has to be judged by the next call."""
    require_enabled()
    gui = _gui()
    if not isinstance(text, str):
        raise DeviceUnavailable("Text to type must be text.")
    problem = input_problem("type_text", {"text": text})
    if problem:
        raise DeviceUnavailable(problem)
    head, ended = first_line(text)
    done = 0
    for piece in pieces(head):
        why = check() if check is not None else None
        if why:
            return {"error": f"Stopped after typing {done} of {len(text)} character(s): {why}", "typed": done,
                    "length": len(text)}
        gui.typewrite(piece, interval=interval)
        done += len(piece)
    message = f"Typed {done} of {len(text)} character(s)."
    if ended and done < len(text):
        message += (" Enter ends the call: the rest was not typed. Look at the screen to see what the Enter did, "
                    "then type the rest in another call if it is still wanted.")
    return {"message": message, "length": len(text), "typed": done}


def press_keys(keys: str) -> dict:
    """Press one chord of known keys. 'enter', 'ctrl+s', 'cmd+space', 'alt+tab'."""
    require_enabled()
    gui = _gui()
    try:
        combo = parse_chord(keys)
    except ValueError as exc:
        return {"error": str(exc)}
    if len(combo) == 1:
        gui.press(combo[0])
    else:
        gui.hotkey(*combo)
    return {"message": f"Pressed {'+'.join(combo)}."}


def scroll(amount: int, x: int, y: int) -> dict:
    """Scroll by `amount` clicks at exactly this point. Positive scrolls up, negative down."""
    require_enabled()
    gui = _gui()
    if not _whole(amount) or not _whole(x) or not _whole(y) or not -100 <= amount <= 100:
        raise DeviceUnavailable("A scroll needs whole numbers, and an amount from -100 to 100.")
    gui.scroll(amount, x=x, y=y)
    return {"message": f"Scrolled {amount} at ({x}, {y})."}
