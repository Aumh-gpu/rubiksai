"""
RUBIKS Server v6
================
One brain, every screen. Serves the installable web app (phone / tablet / desktop) and the
API that the web app and the `rubiks` terminal CLI both talk to.

    python server.py            # this PC only  -> http://127.0.0.1:7575
    python server.py --lan      # also reachable from your phone on the same Wi-Fi
"""

import argparse
import json
import os
import queue
import re
import socket
import sys
import threading
import time
import uuid
from functools import wraps

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

from flask import Flask, Response, g, jsonify, request, send_from_directory

from rubiks_core import auth, config, db, devices, engine, events, guard, llm, voice

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

COOKIE = "rubiks_session"
STATE = {"lan": False, "port": config.DEFAULT_PORT, "started": time.time()}

# ─── Helpers ──────────────────────────────────────────────────────────────────


def err(message: str, status: int = 400):
    return jsonify(ok=False, error=message), status


def _token_from_request():
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        return h[7:].strip(), "bearer"
    return request.cookies.get(COOKIE, ""), "cookie"


def login_required(fn):
    @wraps(fn)
    def inner(*a, **kw):
        token, kind = _token_from_request()
        user = auth.user_from_token(token)
        if not user:
            return err("Please sign in.", 401)
        # Cookie-authenticated writes must carry our header (blocks cross-site form posts).
        if kind == "cookie" and request.method not in ("GET", "HEAD") and request.headers.get("X-Rubiks") != "1":
            return err("Missing request header.", 403)
        g.user = user
        g.token = token
        return fn(*a, **kw)

    return inner


def owner_required(fn):
    @wraps(fn)
    @login_required
    def inner(*a, **kw):
        if g.user["role"] != "owner":
            return err("Only the owner of this Rubiks can do that.", 403)
        return fn(*a, **kw)

    return inner


_fail_lock = threading.Lock()
_failures = {}


def _rate_limited(key: str) -> bool:
    now = time.time()
    with _fail_lock:
        hits = [t for t in _failures.get(key, []) if now - t < 300]
        _failures[key] = hits
        return len(hits) >= 6


def _record_failure(key: str):
    with _fail_lock:
        _failures.setdefault(key, []).append(time.time())


def lan_addresses() -> list:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def _via_tunnel() -> bool:
    """True when the request came in through a local tunnel (cloudflared), which connects from this PC."""
    return request.remote_addr in ("127.0.0.1", "::1") and bool(
        request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"))


def client_ip() -> str:
    """The real visitor's IP. Proxy headers are only trusted when the proxy is on this PC."""
    if _via_tunnel():
        ip = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0]
        return ip.strip() or "?"
    return request.remote_addr or "?"


def is_https() -> bool:
    return request.is_secure or (_via_tunnel() and request.headers.get("X-Forwarded-Proto", "").lower() == "https")


TUNNEL_FILE = os.path.join(config.DATA_DIR, "tunnel_url.txt")


def public_url() -> str:
    """The https address written by `rubiks tunnel` while it runs."""
    try:
        if os.path.exists(TUNNEL_FILE) and time.time() - os.path.getmtime(TUNNEL_FILE) < 7 * 86400:
            with open(TUNNEL_FILE, "r", encoding="utf-8") as f:
                url = f.read().strip()
            return url if url.startswith("https://") else ""
    except Exception:
        pass
    return ""


def _set_session_cookie(resp, token: str):
    resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Strict",
                    secure=is_https(), path="/")
    return resp


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
        "connect-src 'self'; media-src 'self' blob: data:; manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'"
    )
    resp.headers["Permissions-Policy"] = "microphone=(self), camera=()"
    return resp


# ─── Static web app ───────────────────────────────────────────────────────────


@app.get("/")
def index():
    return send_from_directory(config.WEB_DIR, "index.html", max_age=0)


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(config.WEB_DIR, "sw.js", max_age=0)
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    resp = send_from_directory(config.WEB_DIR, "manifest.webmanifest", max_age=0)
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


@app.get("/<path:path>")
def static_files(path):
    if path.startswith("api/"):
        return err("Not found.", 404)
    full = os.path.join(config.WEB_DIR, path)
    if os.path.isfile(full):
        return send_from_directory(config.WEB_DIR, path, max_age=0)
    return send_from_directory(config.WEB_DIR, "index.html", max_age=0)


# ─── Auth ─────────────────────────────────────────────────────────────────────


@app.get("/api/bootstrap")
def bootstrap():
    token, _ = _token_from_request()
    user = auth.user_from_token(token)
    return jsonify(
        ok=True,
        version=config.VERSION,
        has_users=auth.has_users(),
        user=auth.public_user(user) if user else None,
        engine=engine.pick_engine().name,
    )


@app.post("/api/auth/signup")
def signup():
    data = request.get_json(silent=True) or {}
    ip = client_ip()
    if _rate_limited(f"signup:{ip}"):
        return err("Too many attempts. Wait a few minutes and try again.", 429)
    try:
        user = auth.create_user(data.get("name"), data.get("email"), data.get("password"), data.get("invite", ""))
    except auth.AuthError as e:
        _record_failure(f"signup:{ip}")
        return err(str(e))
    token = auth.create_session(user["id"], data.get("client") or request.headers.get("User-Agent", "")[:80])
    resp = jsonify(ok=True, user=auth.public_user(user), token=token)
    return _set_session_cookie(resp, token)


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    ip = client_ip()
    key = f"login:{ip}:{(data.get('email') or '').lower()}"
    if _rate_limited(key):
        return err("Too many wrong attempts. Wait 5 minutes and try again.", 429)
    try:
        user = auth.verify_login(data.get("email"), data.get("password"))
    except auth.AuthError as e:
        _record_failure(key)
        return err(str(e), 401)
    token = auth.create_session(user["id"], data.get("client") or request.headers.get("User-Agent", "")[:80])
    resp = jsonify(ok=True, user=auth.public_user(user), token=token)
    return _set_session_cookie(resp, token)


@app.post("/api/auth/logout")
@login_required
def logout():
    auth.revoke(g.token)
    resp = jsonify(ok=True)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/api/me")
@login_required
def me():
    return jsonify(ok=True, user=auth.public_user(g.user))


@app.patch("/api/me")
@login_required
def update_me():
    user = auth.update_prefs(g.user["id"], request.get_json(silent=True) or {})
    return jsonify(ok=True, user=auth.public_user(user))


@app.post("/api/invites")
@owner_required
def make_invite():
    return jsonify(ok=True, code=auth.create_invite(g.user["id"]))


# ─── Settings / status ────────────────────────────────────────────────────────


@app.get("/api/settings")
@login_required
def get_settings():
    keys = config.gemini_keys()
    is_owner = g.user["role"] == "owner"
    v = config.voice_settings()
    return jsonify(
        ok=True,
        engine=engine.pick_engine().name,
        model=llm.current_model(),
        keys=[config.mask_key(k) for k in keys] if is_owner else [],
        key_count=len(keys),
        key_notes=config.misplaced_keys() if is_owner else [],
        voice={"configured": voice.configured(), "voice_id": v["voice_id"] if is_owner else "",
               "model": v["model"], "key": config.mask_key(v["api_key"]) if (is_owner and v["api_key"]) else ""},
        is_owner=is_owner,
        lan=STATE["lan"],
        port=STATE["port"],
        lan_urls=[f"http://{ip}:{STATE['port']}" for ip in lan_addresses()] if (is_owner and STATE["lan"]) else [],
        public_url=public_url() if is_owner else "",
        secure=is_https(),
        tools=len(guard.load_tools()),
        version=config.VERSION,
    )


@app.post("/api/settings/key")
@owner_required
def set_key():
    key = ((request.get_json(silent=True) or {}).get("key") or "").strip().strip('"')
    svc = config.looks_foreign(key)
    if svc:
        return err(f"That's a {svc}-style key, not a Gemini key. Voice keys go in the Voice section.")
    # AI Studio keys look like AIza…; newer Google keys look like AQ.… (with a dot)
    if not re.match(r"^[A-Za-z0-9_\-.]{20,200}$", key):
        return err("That doesn't look like a Gemini API key.")
    keys = config.save_gemini_key(key)
    return jsonify(ok=True, key_count=len(keys), engine=engine.pick_engine().name)


@app.post("/api/settings/voice")
@owner_required
def set_voice():
    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip().strip('"')
    voice_id = (data.get("voice_id") or "").strip().strip('"')
    if api_key:
        if not re.match(r"^sk_[A-Za-z0-9]{20,120}$", api_key):
            return err("ElevenLabs keys start with sk_.")
        config.save_env("ELEVENLABS_API_KEY", api_key)
    if voice_id:
        if not re.match(r"^[A-Za-z0-9]{10,40}$", voice_id):
            return err("That doesn't look like an ElevenLabs voice ID.")
        config.save_env("ELEVENLABS_VOICE_ID", voice_id)
    return jsonify(ok=True, configured=voice.configured())


@app.post("/api/transcribe")
@login_required
def transcribe():
    """Speech to text for browsers without built-in speech recognition (the app records, Gemini transcribes)."""
    audio = request.get_data(cache=False)
    mime = (request.mimetype or "audio/webm").split(";")[0]
    if not audio or len(audio) < 800:
        return err("Didn't catch anything. Hold the mic a little longer.")
    if not mime.startswith("audio/"):
        return err("Send audio.")
    if not config.gemini_keys():
        return err("Voice typing needs a Gemini key. Add one in Settings.")
    from google.genai import types

    try:
        r = llm.generate([
            types.Part.from_bytes(data=audio, mime_type=mime),
            "Transcribe this voice message to an AI assistant named Rubiks exactly as spoken, in the language spoken "
            "(Hinglish stays Hinglish). "
            "Return only the words, no quotes or notes. If nothing intelligible was said, return an empty string.",
        ])
        text = (r.text or "").strip().strip('"')
    except Exception as e:
        return err(f"Couldn't transcribe that: {str(e)[:160]}", 502)
    return jsonify(ok=True, text=text)


@app.post("/api/tts")
@login_required
def tts():
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return err("Nothing to say.")
    try:
        audio = voice.synthesize(text)
    except voice.VoiceError as e:
        return err(str(e), 502 if "ElevenLabs said" in str(e) or "reach" in str(e) else 400)
    resp = Response(audio, mimetype="audio/mpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.get("/api/vitals")
@login_required
def vitals():
    import psutil

    batt = None
    try:
        b = psutil.sensors_battery()
        if b:
            batt = {"percent": round(b.percent), "plugged": bool(b.power_plugged)}
    except Exception:
        pass
    try:
        disk = psutil.disk_usage(os.path.splitdrive(config.ROOT)[0] + "\\" if sys.platform == "win32" else "/")
        disk_pct = round(disk.percent)
    except Exception:
        disk_pct = None
    mem = psutil.virtual_memory()
    return jsonify(ok=True, cpu=round(psutil.cpu_percent(interval=None)), ram=round(mem.percent),
                   ram_gb=round(mem.total / 1024 ** 3, 1), battery=batt, disk=disk_pct,
                   host=socket.gethostname(), uptime=int(time.time() - STATE["started"]))


@app.get("/api/activity")
@login_required
def activity():
    limit = max(1, min(int(request.args.get("limit", 30)), 200))
    rows = db.query(
        "SELECT id, conversation_id, device, tool, tier, args, decision, ok, summary, ms, created_at "
        "FROM audit WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (g.user["id"], limit),
    )
    labels = {t["name"]: t["label"] for t in guard.tool_catalog()}
    for r in rows:
        r["label"] = labels.get(r["tool"], r["tool"])
        try:
            r["args"] = json.loads(r["args"] or "{}")
        except Exception:
            r["args"] = {}
    return jsonify(ok=True, items=rows, pending=guard.broker.pending_for(g.user["id"]))


@app.get("/api/tools")
@login_required
def tools():
    return jsonify(ok=True, tools=guard.tool_catalog())


# ─── Conversations ────────────────────────────────────────────────────────────


def _own_conversation(cid: str):
    return db.one("SELECT * FROM conversations WHERE id = ? AND user_id = ?", (cid, g.user["id"]))


@app.get("/api/conversations")
@login_required
def list_conversations():
    rows = db.query(
        "SELECT c.id, c.title, c.device, c.updated_at, "
        "(SELECT content FROM messages m WHERE m.conversation_id = c.id ORDER BY m.id DESC LIMIT 1) AS last "
        "FROM conversations c WHERE c.user_id = ? ORDER BY c.updated_at DESC LIMIT 100",
        (g.user["id"],),
    )
    for r in rows:
        r["last"] = re.sub(r"\s+", " ", re.sub(r"```|[`*_#>|]", "", r["last"] or "")).strip()[:120]
    return jsonify(ok=True, items=rows)


@app.get("/api/conversations/<cid>")
@login_required
def get_conversation(cid):
    conv = _own_conversation(cid)
    if not conv:
        return err("Conversation not found.", 404)
    msgs = db.query("SELECT id, role, content, device, meta, created_at FROM messages WHERE conversation_id = ? ORDER BY id", (cid,))
    for m in msgs:
        try:
            m["meta"] = json.loads(m["meta"]) if m["meta"] else {}
        except Exception:
            m["meta"] = {}
    return jsonify(ok=True, conversation=conv, messages=msgs)


@app.patch("/api/conversations/<cid>")
@login_required
def rename_conversation(cid):
    if not _own_conversation(cid):
        return err("Conversation not found.", 404)
    title = ((request.get_json(silent=True) or {}).get("title") or "").strip()[:80]
    if title:
        db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, cid))
    return jsonify(ok=True)


@app.delete("/api/conversations/<cid>")
@login_required
def delete_conversation(cid):
    if not _own_conversation(cid):
        return err("Conversation not found.", 404)
    db.execute("DELETE FROM conversations WHERE id = ?", (cid,))
    return jsonify(ok=True)


# ─── Chat (streams Server-Sent Events) ────────────────────────────────────────


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _title_from(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    return (t[:48] + "…") if len(t) > 48 else (t or "New chat")


@app.post("/api/chat")
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    text = (data.get("message") or "").strip()
    if not text:
        return err("Type a message first.")
    if len(text) > 20000:
        return err("That message is too long (20,000 characters max).")
    device = data.get("device") if data.get("device") in devices.DEVICES else (g.user.get("device_mode") or "desktop")
    user = dict(g.user)
    now = time.time()

    cid = data.get("conversation_id")
    if cid and not _own_conversation(cid):
        return err("Conversation not found.", 404)
    if not cid:
        cid = uuid.uuid4().hex[:16]
        db.execute("INSERT INTO conversations (id, user_id, title, device, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (cid, user["id"], _title_from(text), device, now, now))

    history = db.query("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 30", (cid,))[::-1]
    db.execute("INSERT INTO messages (conversation_id, role, content, device, created_at) VALUES (?, 'user', ?, ?, ?)",
               (cid, text, device, now))
    db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, cid))

    # The client may name the turn so it recognises its own events on the live bus,
    # which can arrive before this response starts streaming.
    proposed = str(data.get("turn_id") or "")
    turn_id = proposed if re.match(r"^[a-z0-9]{8,32}$", proposed) else uuid.uuid4().hex[:12]
    q = queue.Queue()
    trace = {"tools": [], "images": []}

    def emit(event: dict, broadcast: bool = False):
        event = dict(event)
        event.setdefault("turn_id", turn_id)
        event.setdefault("conversation_id", cid)
        event.setdefault("device", device)
        if event.get("type") == "tool_done":
            trace["tools"].append({k: event.get(k) for k in ("name", "ok", "summary", "ms")})
        if event.get("type") == "image":
            trace["images"].append(event.get("url"))
        q.put(event)
        if broadcast:
            events.publish(user["id"], dict(event))

    def worker():
        ctx = guard.TurnContext(user, cid, device, emit, turn_id)
        guard.set_context(ctx)
        eng = engine.pick_engine()
        events.publish(user["id"], {"type": "turn", "state": "start", "turn_id": turn_id, "conversation_id": cid, "device": device})
        try:
            try:
                reply = eng.run(ctx, history, text)
                used = eng.name
            except engine.EngineError as e:
                if eng.name == "gemini" and ctx.tool_calls == 0:
                    reply = f"_{e}_\n\n" + engine.offline.run(ctx, history, text)
                    used = "offline"
                else:
                    raise
            # Stream the reply in small pieces so it lands progressively (total under ~1s).
            words = re.split(r"(\s+)", reply)
            chunks = ["".join(words[i:i + 6]) for i in range(0, len(words), 6)]
            delay = min(0.022, 0.9 / max(len(chunks), 1))
            for c in chunks:
                q.put({"type": "delta", "text": c})
                time.sleep(delay)
            meta = {"engine": used, "tools": trace["tools"], "images": trace["images"]}
            mid = db.execute(
                "INSERT INTO messages (conversation_id, role, content, device, meta, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
                (cid, reply, device, json.dumps(meta), time.time()),
            )
            db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), cid))
            q.put({"type": "done", "text": reply, "engine": used, "message_id": mid, "conversation_id": cid, "turn_id": turn_id})
        except Exception as e:
            msg = str(e) if isinstance(e, engine.EngineError) else f"Something broke while answering: {e}"
            db.execute(
                "INSERT INTO messages (conversation_id, role, content, device, meta, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
                (cid, msg, device, json.dumps({"error": True}), time.time()),
            )
            q.put({"type": "error", "message": msg, "conversation_id": cid, "turn_id": turn_id})
        finally:
            events.publish(user["id"], {"type": "turn", "state": "end", "turn_id": turn_id, "conversation_id": cid, "device": device})
            q.put(None)

    threading.Thread(target=worker, daemon=True, name=f"turn-{turn_id}").start()

    def stream():
        yield _sse({"type": "start", "conversation_id": cid, "turn_id": turn_id, "engine": engine.pick_engine().name})
        while True:
            try:
                ev = q.get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if ev is None:
                break
            yield _sse(ev)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/approvals/<aid>")
@login_required
def resolve_approval(aid):
    decision = (request.get_json(silent=True) or {}).get("decision")
    if not guard.broker.resolve(aid, g.user["id"], decision):
        return err("That approval already expired or was answered on another device.", 409)
    return jsonify(ok=True)


@app.get("/api/events")
@login_required
def event_stream():
    uid = g.user["id"]
    q = events.subscribe(uid)

    def stream():
        try:
            yield _sse({"type": "hello", "pending": guard.broker.pending_for(uid)})
            while True:
                try:
                    ev = q.get(timeout=15)
                    yield _sse(ev)
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(uid, q)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/shots/<name>")
@login_required
def shot(name):
    m = re.match(r"^u(\d+)_[a-f0-9]{16}\.jpg$", name)
    if not m or int(m.group(1)) != g.user["id"]:
        return err("Not found.", 404)
    return send_from_directory(config.SHOTS_DIR, name, max_age=3600)


# ─── Autonomous tasks (Fable Engine) ──────────────────────────────────────────


@app.get("/api/tasks")
@login_required
def task_state():
    try:
        import fable_agent as fa
    except Exception as e:
        return jsonify(ok=True, task=None, note=str(e))
    t = fa._fable_task
    if not t:
        return jsonify(ok=True, task=None)
    return jsonify(ok=True, task={
        "goal": t.goal, "status": t.status, "running": bool(fa._fable_running), "plan": t.plan_text,
        "summary": t.final_summary, "progress": t.progress(),
        "steps": [{"index": s.index, "description": s.description, "status": s.status, "attempts": s.attempts} for s in t.steps],
        "log": fa._fable_log[-12:],
    })


@app.post("/api/tasks")
@login_required
def start_task():
    goal = ((request.get_json(silent=True) or {}).get("goal") or "").strip()
    if not goal:
        return err("Describe the goal for the task.")
    if not config.gemini_keys():
        return err("Autonomous tasks need a Gemini API key. Add one in Settings.")
    import fable_agent as fa

    msg = fa.launch_fable_agent(goal)
    db.execute(
        "INSERT INTO audit (user_id, device, tool, tier, args, decision, ok, summary, ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g.user["id"], request.headers.get("X-Rubiks-Device", "terminal"), "launch_fable_agent", "risky",
         json.dumps({"goal": goal}), "user-started", 1, msg[:500], 0, time.time()),
    )
    return jsonify(ok=True, message=msg)


@app.post("/api/tasks/stop")
@login_required
def stop_task():
    import fable_agent as fa

    return jsonify(ok=True, message=fa.stop_fable_agent())


@app.get("/api/health")
def health():
    return jsonify(ok=True, version=config.VERSION, engine=engine.pick_engine().name)


# ─── Entry point ──────────────────────────────────────────────────────────────


def run(lan: bool = False, port: int = None, quiet: bool = False):
    import logging

    port = port or config.DEFAULT_PORT
    STATE.update(lan=lan, port=port)
    host = "0.0.0.0" if lan else "127.0.0.1"
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    # Warm the tool registry in the background (the legacy modules take a few seconds to import).
    threading.Thread(target=guard.load_tools, daemon=True).start()
    if not quiet:
        print(f"\n  RUBIKS {config.VERSION} is up")
        print(f"  This PC     http://127.0.0.1:{port}")
        if lan:
            for ip in lan_addresses():
                print(f"  Phone/tab   http://{ip}:{port}")
        else:
            print("  Phone/tab   run with --lan to reach it from your phone on the same Wi-Fi")
        print(f"  Brain       {'Gemini (' + config.MODEL + ')' if config.gemini_keys() else 'offline mode (add a Gemini key in Settings)'}")
        print(f"  Voice       {'ElevenLabs' if voice.configured() else 'browser voice (add ElevenLabs in Settings)'}")
        for note in config.misplaced_keys():
            print(f"  Warning     {note}")
        print(f"  Data        {config.DATA_DIR}\n")
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="RUBIKS multi-device server")
    p.add_argument("--lan", action="store_true", help="listen on the local network so phones/tablets can connect")
    p.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args()
    os.chdir(config.ROOT)
    run(lan=a.lan, port=a.port, quiet=a.quiet)
