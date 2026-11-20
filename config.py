import os
from dotenv import load_dotenv

load_dotenv()

# AI Identity
AI_NAME = "RUBIKS"
WAKE_WORD = "rubiks"

# API Keys
gemini_keys_str = os.getenv("GEMINI_API_KEYS", "")
# Skip placeholders and keys that belong to other services (e.g. an ElevenLabs sk_ key pasted here by mistake)
GEMINI_API_KEYS = [
    k.strip().strip('"') for k in gemini_keys_str.split(",")
    if k.strip() and not k.strip().lower().startswith("your") and not k.strip().startswith(("sk_", "sk-", "gsk_"))
]

# ElevenLabs voice (optional). When set, Rubiks speaks with this voice everywhere.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# TTS Settings
TTS_VOICE = "en-GB-RyanNeural"
