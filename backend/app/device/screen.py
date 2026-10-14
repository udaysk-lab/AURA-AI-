"""Seeing and driving the screen.

Screenshots, mouse, and keyboard, through `pyautogui` when it's installed and a
desktop session exists. This is how AURA operates apps that have no command
line — the GUI equivalent of the shell. It degrades to a clear "unavailable"
when the package or the display is missing, so a headless box just reports that
screen control is off rather than erroring deep in a tool.

Coordinates are screen pixels. A screenshot is saved under the first allowed
root (in an `.aura-screens` folder) and its path returned; the agent views it
with the normal image path, and the bytes never balloon the model's context.
"""

from __future__ import annotations

import os
import time

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

    # Don't let a failsafe corner throw mid-task; the autonomy gate is the real brake.
    pyautogui.FAILSAFE = False
    return pyautogui


def _screens_dir() -> str:
    base = settings.device_allowed_root_list[0]
    path = os.path.join(base, ".aura-screens")
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


def screen_size() -> dict:
    require_enabled()
    gui = _gui()
    w, h = gui.size()
    return {"width": w, "height": h}


def cursor_position() -> dict:
    require_enabled()
    gui = _gui()
    x, y = gui.position()
    return {"x": x, "y": y}


def move_mouse(x: int, y: int, duration: float = 0.2) -> dict:
    require_enabled()
    gui = _gui()
    gui.moveTo(int(x), int(y), duration=duration)
    return {"x": int(x), "y": int(y)}


def click(x: int | None = None, y: int | None = None, button: str = "left", clicks: int = 1) -> dict:
    require_enabled()
    gui = _gui()
    button = button if button in ("left", "right", "middle") else "left"
    if x is not None and y is not None:
        gui.click(x=int(x), y=int(y), button=button, clicks=clicks)
        where = f"({int(x)}, {int(y)})"
    else:
        gui.click(button=button, clicks=clicks)
        where = "the current position"
    return {"message": f"{button.capitalize()}-clicked {where}×{clicks}."}


def double_click(x: int | None = None, y: int | None = None) -> dict:
    return click(x, y, button="left", clicks=2)


def type_text(text: str, interval: float = 0.01) -> dict:
    require_enabled()
    gui = _gui()
    gui.typewrite(text, interval=interval)
    return {"message": f"Typed {len(text)} character(s).", "length": len(text)}


def press_keys(keys: str) -> dict:
    """Press a key or a chord. 'enter', 'ctrl+s', 'cmd+space', 'alt+tab'."""
    require_enabled()
    gui = _gui()
    combo = [k.strip().lower() for k in str(keys).replace("+", " ").split() if k.strip()]
    if not combo:
        return {"error": "No keys given."}
    if len(combo) == 1:
        gui.press(combo[0])
    else:
        gui.hotkey(*combo)
    return {"message": f"Pressed {'+'.join(combo)}."}


def scroll(amount: int, x: int | None = None, y: int | None = None) -> dict:
    """Scroll by `amount` clicks. Positive scrolls up, negative down."""
    require_enabled()
    gui = _gui()
    if x is not None and y is not None:
        gui.scroll(int(amount), x=int(x), y=int(y))
    else:
        gui.scroll(int(amount))
    return {"message": f"Scrolled {amount}."}
