"""Paths, environment and API-key handling for the RUBIKS server."""

import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(ENV_PATH)

# Make the legacy tool modules (pc_controller, web_tools, ...) importable.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _default_data_dir() -> str:
    # User data lives outside the repo (and outside OneDrive sync) so it is never
    # committed and SQLite is not fighting a sync client.
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "Rubiks")


DATA_DIR = os.environ.get("RUBIKS_DATA_DIR") or _default_data_dir()
DB_PATH = os.path.join(DATA_DIR, "rubiks.db")
SHOTS_DIR = os.path.join(DATA_DIR, "shots")
LOG_PATH = os.path.join(DATA_DIR, "server.log")
WEB_DIR = os.path.join(ROOT, "web")

DEFAULT_PORT = int(os.environ.get("RUBIKS_PORT", "7575"))
MODEL = os.environ.get("RUBIKS_MODEL", "gemini-3.8-flash")
VERSION = "6.1.0"

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SHOTS_DIR, exist_ok=True)


def looks_foreign(key: str) -> str:
    """Returns which service a key belongs to if it's clearly not a Gemini key."""
    if key.startswith("sk_"):
        return "ElevenLabs"
    if key.startswith("sk-"):
        return "OpenAI/Anthropic"
    if key.startswith("gsk_"):
        return "Groq"
    return ""


def _raw_gemini_keys() -> list:
    raw = os.environ.get("GEMINI_API_KEYS", "")
    keys = []
    for k in raw.split(","):
        k = k.strip().strip('"').strip("'")
        if k and not k.lower().startswith("your"):
            keys.append(k)
    return keys


def gemini_keys() -> list:
    return [k for k in _raw_gemini_keys() if not looks_foreign(k)]


def misplaced_keys() -> list:
    """Human notes about keys in GEMINI_API_KEYS that belong to other services."""
    return [f"A key in GEMINI_API_KEYS is a {looks_foreign(k)}-style key ({mask_key(k)}), so it was skipped. "
            f"Put your Gemini key (AIza… or AQ.…) on that line."
            for k in _raw_gemini_keys() if looks_foreign(k)]


def save_env(name: str, value: str):
    """Sets NAME="value" in .env (adding or replacing the line) and in this process."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    out, written = [], False
    for line in lines:
        if line.strip().startswith(f"{name}="):
            out.append(f'{name}="{value}"\n')
            written = True
        else:
            out.append(line)
    if not written:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(f'{name}="{value}"\n')
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(out)
    os.environ[name] = value


def voice_settings() -> dict:
    return {
        "api_key": os.environ.get("ELEVENLABS_API_KEY", "").strip().strip('"'),
        "voice_id": os.environ.get("ELEVENLABS_VOICE_ID", "").strip().strip('"'),
        "model": os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5").strip().strip('"'),
    }


def mask_key(key: str) -> str:
    if len(key) <= 10:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def save_gemini_key(new_key: str) -> list:
    """Adds a Gemini key to .env and hot-reloads it for the server and legacy modules."""
    new_key = new_key.strip()
    keys = gemini_keys()  # also drops keys that belong to other services
    if new_key and new_key not in keys:
        keys.append(new_key)
    save_env("GEMINI_API_KEYS", ",".join(keys))
    # Legacy modules did `from config import GEMINI_API_KEYS`; mutate that list in place
    # so vision/fable tools see the new key without a restart.
    try:
        import config as legacy_config  # noqa: WPS433
        legacy_config.GEMINI_API_KEYS[:] = keys
    except Exception:
        pass
    return keys
