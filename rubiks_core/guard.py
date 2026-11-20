"""The guard: every tool the AI can call goes through here.

Each tool has a tier:
  read   - looks at things (stats, search, files, screen). Always runs.
  open   - opens or plays something (apps, sites, media). Always runs.
  act    - changes something on screen or on disk. Asks on phone/tablet, runs at the desk.
  risky  - runs code, sends messages, closes programs, acts as you online. Always asks,
           unless the user turned on Full autopilot and is at the desktop or terminal.

Every call is written to the audit log and streamed live to the user's devices.
"""

import functools
import importlib
import inspect
import json
import threading
import time
import uuid

from . import db, events

# (module, function, tier, human label)
TOOL_SPECS = [
    ("pc_controller", "get_system_stats", "read", "Check CPU and RAM"),
    ("sys_admin_tool", "get_system_info", "read", "Read system info"),
    ("sys_admin_tool", "get_running_processes", "read", "List running apps"),
    ("sys_admin_tool", "read_clipboard", "read", "Read the clipboard"),
    ("web_tools", "search_web", "read", "Search the web"),
    ("get_weather", "get_weather", "read", "Check the weather"),
    ("briefing_tool", "get_morning_briefing", "read", "Morning briefing"),
    ("memory_tool", "remember_fact", "read", "Remember"),
    ("memory_tool", "recall_memories", "read", "Recall memory"),
    ("file_tools", "list_files", "read", "List files"),
    ("file_tools", "read_file", "read", "Read a file"),
    ("file_tools", "find_recent_files", "read", "Find recent files"),
    ("file_tools", "search_files", "read", "Search files"),
    ("vision_tool", "get_active_windows", "read", "List open windows"),
    ("vision_tool", "analyze_screen", "read", "Look at the screen"),
    ("network_diag_tool", "run_speed_test", "read", "Internet speed test"),
    ("lead_gen_tool", "find_leads", "read", "Find leads"),
    ("pc_controller", "open_application", "open", "Open an app"),
    ("pc_controller", "open_website", "open", "Open a website"),
    ("file_tools", "open_file", "open", "Open a file"),
    ("youtube_tool", "play_on_youtube", "open", "Play on YouTube"),
    ("media_tool", "play_on_spotify", "open", "Play on Spotify"),
    ("media_tool", "control_media", "open", "Media controls"),
    ("sys_admin_tool", "set_system_volume", "open", "Set the volume"),
    ("gmail_tool", "open_gmail", "open", "Open Gmail"),
    ("gmail_tool", "open_specific_email", "open", "Open an email"),
    ("gmail_tool", "compose_email", "open", "Draft an email"),
    ("email_tool", "check_emails", "open", "Open the inbox"),
    ("maps_tool", "get_directions", "open", "Get directions"),
    ("maps_tool", "show_on_map", "open", "Show on a map"),
    ("pc_controller", "type_text", "act", "Type on the PC"),
    ("vision_tool", "click_on_screen", "act", "Click on the screen"),
    ("sys_admin_tool", "write_clipboard", "act", "Write to the clipboard"),
    ("file_tools", "write_file", "act", "Write a file"),
    ("generate_image", "generate_image", "act", "Generate an image"),
    ("chat_agent", "stop_chat_agent", "act", "Stop the auto-reply agent"),
    ("pc_controller", "run_terminal_command", "risky", "Run a terminal command"),
    ("pc_controller", "execute_python_script", "risky", "Run Python code"),
    ("pc_controller", "type_and_enter", "risky", "Type and press Enter"),
    ("sys_admin_tool", "kill_process", "risky", "Force-close a program"),
    ("sys_admin_tool", "initiate_lockdown", "risky", "Lock the PC"),
    ("whatsapp_tool", "send_whatsapp_by_name", "risky", "Send a WhatsApp message"),
    ("whatsapp_tool", "send_whatsapp_message", "risky", "Send a WhatsApp message"),
    ("account_tool", "create_account", "risky", "Create an online account"),
    ("shadow_engine", "execute_shadow_task", "risky", "Run a hidden browser script"),
    ("swarm_tool", "execute_swarm", "risky", "Start a swarm of AI agents"),
    ("swarm_tool", "ask_entity", "risky", "Ask another AI in the browser"),
    ("self_training", "teach_new_skill", "risky", "Install a new skill (code)"),
    ("self_training", "upgrade_rubiks_core", "risky", "Rewrite Rubiks' own code"),
    ("chat_agent", "start_chat_agent", "risky", "Auto-reply to chats as you"),
    ("fable_agent", "launch_fable_agent", "risky", "Start an autonomous task"),
    ("website_builder_tool", "build_business_website", "risky", "Build a website"),
    ("whatsapp_tool", "whatsapp_status", "read", "Check WhatsApp"),
    ("rubiks_core.winctl", "focus_window", "open", "Bring a window to the front"),
    ("rubiks_core.extra_tools", "show_my_screen", "read", "Show the PC screen"),
    ("rubiks_core.extra_tools", "get_task_status", "read", "Check task progress"),
]

RISK_NOTES = {
    "run_terminal_command": "Runs a PowerShell command on your PC.",
    "execute_python_script": "Runs Python code on your PC.",
    "type_and_enter": "Types into the active window and presses Enter. This can send messages.",
    "kill_process": "Force-closes a program. Unsaved work in it is lost.",
    "initiate_lockdown": "Mutes, minimizes every window and locks Windows.",
    "send_whatsapp_by_name": "Sends a WhatsApp message as you.",
    "send_whatsapp_message": "Sends a WhatsApp message as you.",
    "create_account": "Signs up for a service in your name.",
    "execute_shadow_task": "Runs an invisible browser automation script.",
    "execute_swarm": "Opens other AI tools in your browser and types into them.",
    "ask_entity": "Opens another AI in your browser and types into it.",
    "teach_new_skill": "Writes new code into Rubiks itself.",
    "upgrade_rubiks_core": "Lets a coding agent change Rubiks' own code.",
    "start_chat_agent": "Watches your screen and replies to chats as you.",
    "launch_fable_agent": "Plans and runs many steps on its own: terminal, files and web.",
    "build_business_website": "Drives an AI in your browser to build a site.",
    "type_text": "Types into whichever window is active.",
    "click_on_screen": "Clicks on your screen.",
    "write_file": "Creates or overwrites a file.",
    "write_clipboard": "Replaces what's on your clipboard.",
    "generate_image": "Generates an image and saves it to disk.",
    "stop_chat_agent": "Stops the auto-reply agent.",
}

APPROVAL_TIMEOUT = 180  # seconds


# ─── Per-turn context (tools run in the same thread as the model call) ─────────

_local = threading.local()


class TurnContext:
    def __init__(self, user: dict, conversation_id: str, device: str, emit, turn_id: str):
        self.user = user
        self.conversation_id = conversation_id
        self.device = device
        self.emit = emit
        self.turn_id = turn_id
        self.tool_calls = 0


def set_context(ctx):
    _local.turn = ctx


def get_context():
    return getattr(_local, "turn", None)


# ─── Approvals ───────────────────────────────────────────────────────────────

class ApprovalBroker:
    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()

    def request(self, ctx: TurnContext, payload: dict) -> str:
        aid = uuid.uuid4().hex[:12]
        ev = threading.Event()
        item = {"user_id": ctx.user["id"], "event": ev, "decision": None, "payload": None}
        event = {
            "type": "approval",
            "id": aid,
            "device": ctx.device,
            "expires_at": time.time() + APPROVAL_TIMEOUT,
            **payload,
        }
        item["payload"] = event
        with self._lock:
            self._pending[aid] = item
        ctx.emit(event, broadcast=True)

        ev.wait(APPROVAL_TIMEOUT)
        with self._lock:
            done = self._pending.pop(aid, None)
        decision = (done or {}).get("decision") or "timeout"
        ctx.emit({"type": "approval_resolved", "id": aid, "decision": decision}, broadcast=True)
        return decision

    def resolve(self, aid: str, user_id: int, decision: str) -> bool:
        if decision not in ("approve", "deny"):
            return False
        with self._lock:
            item = self._pending.get(aid)
            if not item or item["user_id"] != user_id or item["decision"]:
                return False
            item["decision"] = decision
            item["event"].set()
        return True

    def pending_for(self, user_id: int) -> list:
        with self._lock:
            return [i["payload"] for i in self._pending.values() if i["user_id"] == user_id and not i["decision"]]


broker = ApprovalBroker()


def needs_approval(tier: str, device: str, approval_mode: str) -> bool:
    if tier in ("read", "open"):
        return False
    at_desk = device in ("desktop", "terminal")
    if tier == "act":
        return not at_desk
    # risky
    return not (approval_mode == "autopilot" and at_desk)


# ─── Wrapping ────────────────────────────────────────────────────────────────

def _display_args(fn, args, kwargs) -> dict:
    try:
        bound = inspect.signature(fn).bind_partial(*args, **kwargs).arguments
    except Exception:
        bound = dict(kwargs)
    out = {}
    for k, v in bound.items():
        s = v if isinstance(v, (int, float, bool)) or v is None else str(v)
        if isinstance(s, str) and len(s) > 600:
            s = s[:600] + "…"
        out[k] = s
    return out


def _audit(ctx, tool, tier, args, decision, ok, summary, ms):
    try:
        db.execute(
            "INSERT INTO audit (user_id, conversation_id, device, tool, tier, args, decision, ok, summary, ms, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ctx.user["id"] if ctx else None,
                ctx.conversation_id if ctx else None,
                ctx.device if ctx else None,
                tool,
                tier,
                json.dumps(args)[:2000],
                decision,
                None if ok is None else int(bool(ok)),
                (summary or "")[:500],
                ms,
                time.time(),
            ),
        )
    except Exception:
        pass


def _looks_failed(result) -> bool:
    s = str(result).lower()[:200]
    return any(w in s for w in ("failed", "error", "could not", "couldn't", "unable to", "not found"))


def guard_tool(fn, tier: str, label: str):
    name = fn.__name__

    @functools.wraps(fn)
    def guarded(*args, **kwargs):
        ctx = get_context()
        shown = _display_args(fn, args, kwargs)
        call_id = uuid.uuid4().hex[:10]
        if ctx:
            ctx.tool_calls += 1
            user = ctx.user
            ask = needs_approval(tier, ctx.device, user.get("approval_mode", "ask"))
        else:
            ask = tier in ("act", "risky")

        if ctx:
            ctx.emit({"type": "tool", "id": call_id, "name": name, "label": label, "tier": tier,
                      "args": shown, "status": "waiting" if ask else "running"}, broadcast=True)

        decision = "auto"
        if ask:
            if not ctx:
                _audit(None, name, tier, shown, "deny", None, "No user context to ask for approval.", 0)
                return f"DENIED: '{label}' needs a user's approval and none was available."
            decision = broker.request(ctx, {
                "tool": name, "label": label, "tier": tier, "args": shown,
                "risk": RISK_NOTES.get(name, ""), "conversation_id": ctx.conversation_id,
            })
            if decision != "approve":
                why = "didn't respond in time" if decision == "timeout" else "said no"
                _audit(ctx, name, tier, shown, decision, None, f"User {why}.", 0)
                ctx.emit({"type": "tool_done", "id": call_id, "name": name, "ok": False,
                          "summary": "Not approved" if decision == "deny" else "Approval timed out", "ms": 0}, broadcast=True)
                return (f"DENIED: the user {why} for '{label}'. Do not retry this action or work around it. "
                        f"Acknowledge briefly and offer an alternative.")
            ctx.emit({"type": "tool", "id": call_id, "name": name, "label": label, "tier": tier,
                      "args": shown, "status": "running"}, broadcast=True)

        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            ok = not _looks_failed(result)
        except Exception as e:  # tools should never crash the turn
            result = f"Tool error in {name}: {e}"
            ok = False
        ms = int((time.time() - t0) * 1000)
        summary = str(result).strip().replace("\n", " ")[:240]
        _audit(ctx, name, tier, shown, decision, ok, summary, ms)
        if ctx:
            ctx.emit({"type": "tool_done", "id": call_id, "name": name, "ok": ok, "summary": summary, "ms": ms}, broadcast=True)
        return result

    guarded.rubiks_tier = tier
    guarded.rubiks_label = label
    return guarded


_TOOLS = None
_LOAD_ERRORS = {}


def load_tools() -> dict:
    """Imports every tool module that works on this machine and wraps its functions."""
    global _TOOLS
    if _TOOLS is not None:
        return _TOOLS
    tools = {}
    modules = {}
    for mod_name, fn_name, tier, label in TOOL_SPECS:
        try:
            if mod_name not in modules:
                modules[mod_name] = importlib.import_module(mod_name)
            fn = getattr(modules[mod_name], fn_name)
            tools[fn_name] = guard_tool(fn, tier, label)
        except Exception as e:
            _LOAD_ERRORS[f"{mod_name}.{fn_name}"] = str(e)[:200]
    _TOOLS = tools
    return tools


def tool_catalog() -> list:
    tools = load_tools()
    return [
        {"name": n, "label": f.rubiks_label, "tier": f.rubiks_tier}
        for n, f in tools.items()
    ]


def load_errors() -> dict:
    load_tools()
    return dict(_LOAD_ERRORS)
