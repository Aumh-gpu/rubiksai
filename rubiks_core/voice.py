"""Rubiks' voice: ElevenLabs text-to-speech with an on-disk cache.

Used by the web app (/api/tts, so phones and tablets hear the same voice as the PC)
and by the legacy voice assistant (audio_engine.py).
"""

import hashlib
import os
import re

import requests

from . import config

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
MAX_CHARS = 2500


class VoiceError(Exception):
    pass


def configured() -> bool:
    v = config.voice_settings()
    return bool(v["api_key"] and v["voice_id"])


def speakable(text: str) -> str:
    """Strips markdown, code and links so the voice reads like a person, not a parser."""
    t = re.sub(r"```.*?```", " (code is on screen) ", text or "", flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_`#>|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_CHARS]


def synthesize(text: str) -> bytes:
    v = config.voice_settings()
    if not (v["api_key"] and v["voice_id"]):
        raise VoiceError("No ElevenLabs voice configured.")
    clean = speakable(text)
    if not clean:
        raise VoiceError("Nothing to say.")

    tts_dir = os.path.join(config.DATA_DIR, "tts")
    os.makedirs(tts_dir, exist_ok=True)
    digest = hashlib.sha1(f"{v['voice_id']}|{v['model']}|{clean}".encode()).hexdigest()
    cached = os.path.join(tts_dir, f"{digest}.mp3")
    if os.path.exists(cached):
        with open(cached, "rb") as f:
            return f.read()

    try:
        r = requests.post(
            API.format(voice=v["voice_id"]),
            headers={"xi-api-key": v["api_key"], "Content-Type": "application/json", "Accept": "audio/mpeg"},
            json={"text": clean, "model_id": v["model"],
                  "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True}},
            timeout=30,
        )
    except requests.RequestException as e:
        raise VoiceError(f"Couldn't reach ElevenLabs: {e}")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail")
            msg = detail.get("message") if isinstance(detail, dict) else str(detail)
        except Exception:
            msg = r.text[:200]
        raise VoiceError(f"ElevenLabs said no ({r.status_code}): {msg}")
    with open(cached, "wb") as f:
        f.write(r.content)
    return r.content
