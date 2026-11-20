"""Engines that turn a message into an answer.

GeminiEngine  - the real brain: Gemini with every guarded tool, rotating across API keys.
OfflineEngine - no key / no internet fallback: fast intent matching that still uses the
                guarded tools, so approvals, the audit log and live activity all work.
"""

import re

from . import config, devices, guard


class EngineError(Exception):
    pass


def _friendly(err) -> str:
    s = str(err or "")
    low = s.lower()
    if "429" in s or "resource_exhausted" in low or "quota" in low:
        return "Gemini's rate limit is hit on every key. Add another key in Settings or try again in a minute."
    if "api_key_invalid" in low or "api key not valid" in low or "permission_denied" in low or " 401" in s or " 403" in s:
        return "Gemini rejected the API key. Check it in Settings."
    if "getaddrinfo" in low or "connection" in low or "timed out" in low:
        return "Couldn't reach Gemini. Check the PC's internet connection."
    return f"The model call failed: {s[:220]}"


def _history_contents(history: list):
    from google.genai import types

    merged = []
    for m in history:
        role = "user" if m["role"] == "user" else "model"
        text = (m.get("content") or "").strip()
        if not text:
            continue
        if merged and merged[-1][0] == role:
            merged[-1][1].append(text)
        else:
            merged.append((role, [text]))
    if merged and merged[0][0] == "model":
        merged.pop(0)
    return [types.Content(role=r, parts=[types.Part.from_text(text="\n\n".join(t))]) for r, t in merged]


class GeminiEngine:
    name = "gemini"

    def __init__(self):
        self._next = 0

    def available(self) -> bool:
        return bool(config.gemini_keys())

    def run(self, ctx, history: list, text: str) -> str:
        from . import llm

        keys = config.gemini_keys()
        if not keys:
            raise EngineError("No Gemini API key yet. Add one in Settings.")
        tools = list(guard.load_tools().values())
        system = devices.build_system_prompt(ctx.user, ctx.device)
        last_err = None
        start = self._next % len(keys)
        for i in range(len(keys)):
            idx = (start + i) % len(keys)
            calls_before = ctx.tool_calls
            try:
                chat = llm.chat(system, tools, _history_contents(history), key=keys[idx], max_calls=12)
                resp = chat.send_message(text)
                self._next = idx
                out = (resp.text or "").strip()
                if not out:
                    out = "Done." if ctx.tool_calls > calls_before else "The model sent back an empty reply. Try rephrasing."
                return out
            except Exception as e:
                last_err = e
                self._next = idx + 1
                # Never replay side effects on another key.
                if ctx.tool_calls > calls_before:
                    break
        raise EngineError(_friendly(last_err))


class OfflineEngine:
    name = "offline"

    HELP = (
        "I'm in offline mode, so I understand quick commands rather than open questions. Try:\n"
        "- **system status**, **what's running**, **recent files**\n"
        "- **show my screen**\n"
        "- **search** best laptops under 60k\n"
        "- **weather in** Pune\n"
        "- **open** notepad, **play** lo-fi beats\n"
        "- **remember** my GST number is on the invoice folder\n"
        "- **run** Get-Date (asks you first)\n\n"
        "Add a Gemini API key in Settings to unlock the full brain."
    )

    SITES = {
        "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "google": "https://www.google.com",
        "whatsapp": "https://web.whatsapp.com", "whatsapp web": "https://web.whatsapp.com", "instagram": "https://www.instagram.com",
        "linkedin": "https://www.linkedin.com", "twitter": "https://x.com", "x": "https://x.com", "github": "https://github.com",
        "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai", "maps": "https://maps.google.com",
        "google maps": "https://maps.google.com", "drive": "https://drive.google.com", "google drive": "https://drive.google.com",
        "netflix": "https://www.netflix.com", "amazon": "https://www.amazon.in", "flipkart": "https://www.flipkart.com",
    }

    def available(self) -> bool:
        return True

    def run(self, ctx, history: list, text: str) -> str:
        tools = guard.load_tools()
        # Quick commands live on the first line (piped CLI input is appended below it).
        text = (text.strip().splitlines() or [""])[0].strip()
        low = text.lower().rstrip("?.! ")
        first = (ctx.user.get("name") or "there").split()[0]
        phone = ctx.device == "phone"

        def call(name, *a, **k):
            fn = tools.get(name)
            if not fn:
                return f"That tool isn't available on this PC ({name})."
            return str(fn(*a, **k))

        def show(lead: str, result: str, limit: int = None) -> str:
            if result.startswith("DENIED"):
                return "Okay, I won't do that."
            limit = limit or (280 if phone else 1800)
            body = result.strip()
            if len(body) > limit:
                body = body[:limit].rstrip() + "…"
            return f"{lead}\n\n{body}" if lead else body

        if re.match(r"^(hi|hey|hello|yo|hola|namaste|good (morning|afternoon|evening))\b", low):
            if phone:
                return f"Hey {first}. Offline mode: quick PC commands work. Add a Gemini key in Settings for the full brain."
            return f"Hey {first}. I'm running in offline mode, so quick PC commands work but open-ended thinking doesn't.\n\n" + self.HELP

        if "screen" in low and any(w in low for w in ("show", "see", "what's on", "whats on", "screenshot")):
            r = call("show_my_screen")
            return "Here's your PC right now." if not r.lower().startswith("failed") else r

        if any(w in low for w in ("system status", "cpu", "ram", "battery", "how is my pc", "vitals", "pc status")):
            r = call("get_system_stats")
            if not phone:
                r += "\n" + call("get_system_info")
            return show("Here's how the PC is doing:", r)

        m = re.search(r"weather(?:\s+(?:in|for|at))?\s+(.+)$", low)
        if m:
            return show("", call("get_weather", m.group(1).strip()))
        if low in ("weather", "what's the weather", "whats the weather"):
            return "Which city? Try \"weather in Pune\"."

        if any(w in low for w in ("what's running", "whats running", "running apps", "processes")):
            return show("Top programs by memory:", call("get_running_processes", 8 if phone else 15))

        if "recent files" in low or "latest files" in low:
            return show("Most recent files:", call("find_recent_files", "", 5))

        if any(w in low for w in ("what do you remember", "my memories", "what do you know about me")):
            return show("", call("recall_memories"))

        m = re.match(r"^remember(?:\s+that)?\s+(.+)$", text.strip(), re.I)
        if m:
            call("remember_fact", "notes", m.group(1).strip())
            return "Saved to memory."

        m = re.match(r"^(?:search|google|look up)(?:\s+for)?\s+(.+)$", text.strip(), re.I)
        if m:
            return show(f"Top results for \"{m.group(1).strip()}\":", call("search_web", m.group(1).strip()), 400 if phone else 2400)

        m = re.match(r"^(?:open|launch|start)\s+(.+)$", text.strip(), re.I)
        if m:
            raw = m.group(1).strip()
            in_browser = bool(re.search(r"\s+(?:on|in)\s+(?:my\s+|the\s+)?(?:browser|chrome|edge)$", raw, re.I))
            target = re.sub(r"\s+(?:on|in)\s+(?:my\s+|the\s+)?(?:browser|chrome|edge|pc|computer|laptop)$", "", raw, flags=re.I)
            target = re.sub(r"^(?:the|my)\s+", "", target, flags=re.I).strip()
            if in_browser or re.search(r"\.(com|in|org|net|io|ai|dev|co)\b|^https?://", target, re.I):
                if target.lower() in self.SITES:
                    target = self.SITES[target.lower()]
                return show(f"Opening {m.group(1).strip()}.", call("open_website", target))
            if target.lower() in self.SITES:
                return show(f"Opening {target} in your browser.", call("open_website", self.SITES[target.lower()]))
            return show("", call("open_application", target))

        m = re.match(r"^play\s+(.+)$", text.strip(), re.I)
        if m:
            return show("", call("play_on_youtube", m.group(1).strip()))

        m = re.match(r"^(?:run|exec|terminal)[:\s]+(.+)$", text.strip(), re.I)
        if m:
            r = call("run_terminal_command", m.group(1).strip())
            if r.startswith("DENIED"):
                return "Okay, I didn't run it."
            fence = "" if ctx.device in ("phone", "terminal") else "```\n"
            end = "" if not fence else "\n```"
            return f"Ran `{m.group(1).strip()}`:\n\n{fence}{r[:280 if phone else 3000]}{end}"

        if re.search(r"\block (my )?(pc|computer|laptop)\b", low):
            r = call("initiate_lockdown")
            return "Okay, I didn't lock it." if r.startswith("DENIED") else r

        if any(w in low for w in ("briefing", "brief me", "morning update")):
            return show("", call("get_morning_briefing"))

        if any(w in low for w in ("task status", "task progress", "fable status")):
            return show("", call("get_task_status"))

        if low in ("help", "/help", "what can you do", "commands"):
            return self.HELP

        if phone:
            return "I'm offline, so I only understand quick commands like \"system status\", \"show my screen\" or \"search …\". Add a Gemini key in Settings for everything else."
        return "I couldn't match that to a quick command.\n\n" + self.HELP


gemini = GeminiEngine()
offline = OfflineEngine()


def pick_engine():
    return gemini if gemini.available() else offline
