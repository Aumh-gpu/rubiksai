"""Tools that only make sense in the multi-device app."""

import os
import uuid

from .config import SHOTS_DIR


def show_my_screen() -> str:
    """Takes a screenshot of the PC and shows it to the user inside the Rubiks app, so they can see their PC from their phone or tablet.
    Use this when the user asks to see their screen, what's open on the PC, or wants visual proof that something happened."""
    from .guard import get_context

    ctx = get_context()
    try:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img.thumbnail((1600, 1600))
        uid = ctx.user["id"] if ctx else 0
        name = f"u{uid}_{uuid.uuid4().hex[:16]}.jpg"
        img.save(os.path.join(SHOTS_DIR, name), "JPEG", quality=72, optimize=True)
        if ctx:
            ctx.emit({"type": "image", "url": f"/api/shots/{name}", "alt": "Screenshot of the PC"})
        return "Screenshot captured and shown to the user in the app. Describe it only if they ask (use analyze_screen to read it)."
    except Exception as e:
        return f"Failed to capture the screen: {e}"


def get_task_status() -> str:
    """Returns progress of the autonomous background task (Fable Engine), if one is running or finished recently."""
    try:
        from fable_agent import get_fable_status

        return get_fable_status()
    except Exception as e:
        return f"Task status unavailable: {e}"
