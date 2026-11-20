"""One place that knows how to talk to Gemini (google-genai SDK).

- picks keys from .env (and skips keys that clearly belong to another service)
- falls back across models when Google retires one ("no longer available to new users")
- offers a tiny adapter so the older modules (fable_agent, brain) keep their shape
"""

import random
import threading

from . import config

# Best first; later entries are lighter models that usually have spare capacity.
FALLBACK_MODELS = [
    "gemini-3.8-flash", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-3.7-flash",
    "gemini-3.5-flash", "gemini-flash-lite-latest", "gemini-3.1-flash-lite",
]
REMEMBER_SECONDS = 600  # stick with a model that worked, then retry the best one
_working = {"model": None, "until": 0.0}
_lock = threading.Lock()


def candidate_models() -> list:
    import time

    out = []
    sticky = _working["model"] if time.time() < _working["until"] else None
    for m in [sticky, config.MODEL, *FALLBACK_MODELS]:
        if m and m not in out:
            out.append(m)
    return out


def remember_model(m: str):
    import time

    with _lock:
        _working["model"] = m
        _working["until"] = time.time() + REMEMBER_SECONDS


def current_model() -> str:
    import time

    return _working["model"] if (_working["model"] and time.time() < _working["until"]) else config.MODEL


def model_gone(err) -> bool:
    """True when another model may succeed: this one is retired, overloaded, timing out or out of quota."""
    s = str(err).lower()
    retired = ("404" in s or "not_found" in s) and ("model" in s or "no longer available" in s)
    overloaded = any(w in s for w in ("503", "504", "unavailable", "high demand", "overloaded", "deadline", "500 internal"))
    slow = "timed out" in s or "timeout" in s
    quota = "429" in s or "resource_exhausted" in s
    return retired or overloaded or slow or quota


def client(key: str = None):
    from google import genai
    from google.genai import types

    keys = config.gemini_keys()
    if not key:
        if not keys:
            raise RuntimeError("No Gemini API key yet. Add one in Settings or with `rubiks key`.")
        key = random.choice(keys)
    # Fail fast (20s per model step, no SDK retry loop) so a stuck model falls through to the next one.
    return genai.Client(api_key=key, http_options=types.HttpOptions(
        timeout=20_000, retry_options=types.HttpRetryOptions(attempts=1)))


def generate(contents, system: str = None, key: str = None):
    """Single request with model fallback. `contents` may mix text and PIL images."""
    from google.genai import types

    cfg = types.GenerateContentConfig(system_instruction=system) if system else None
    last = None
    c = client(key)
    for m in candidate_models():
        try:
            r = c.models.generate_content(model=m, contents=contents, config=cfg)
            remember_model(m)
            return r
        except Exception as e:
            last = e
            if model_gone(e):
                continue
            raise
    raise last


def _tell_user(text: str):
    """Shows a short status line in the live turn, if there is one."""
    try:
        from .guard import get_context

        ctx = get_context()
        if ctx:
            ctx.emit({"type": "status", "text": text})
    except Exception:
        pass


class _Chat:
    def __init__(self, system, tools, history, key, max_calls):
        self.system, self.tools, self.key, self.max_calls = system, tools, key, max_calls
        self.history = history or []
        self._chat = None
        self._client = None  # must outlive the chat: the SDK closes its HTTP client when the Client is collected

    def _make(self, model):
        from google.genai import types

        if self._client is None:
            self._client = client(self.key)
        return self._client.chats.create(
            model=model,
            config=types.GenerateContentConfig(
                system_instruction=self.system,
                tools=self.tools or None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=not self.tools, maximum_remote_calls=self.max_calls),
            ),
            history=self.history,
        )

    def send_message(self, text):
        if self._chat is not None:
            return self._chat.send_message(text)
        from .guard import get_context

        ctx = get_context()
        last = None
        for m in candidate_models():
            chat = self._make(m)
            calls_before = ctx.tool_calls if ctx else 0
            try:
                r = chat.send_message(text)
                remember_model(m)
                self._chat = chat
                return r
            except Exception as e:
                last = e
                acted = ctx is not None and ctx.tool_calls > calls_before
                if model_gone(e) and not acted:  # never replay PC actions on another model
                    _tell_user(f"{m} is busy right now, switching to another model…")
                    continue
                raise
        raise last

    def get_history(self):
        return self._chat.get_history() if self._chat is not None else self.history


def chat(system: str = None, tools: list = None, history: list = None, key: str = None, max_calls: int = 10) -> _Chat:
    return _Chat(system, tools, history, key, max_calls)


class LegacyModel:
    """Drop-in for google.generativeai.GenerativeModel(...).start_chat(...)."""

    def __init__(self, model_name=None, tools=None, system_instruction=None, key=None):
        self.tools, self.system, self.key = tools, system_instruction, key

    def start_chat(self, enable_automatic_function_calling=True, history=None):
        return chat(self.system, self.tools if enable_automatic_function_calling else None, history, self.key)

    def generate_content(self, contents):
        return generate(contents, self.system, self.key)
