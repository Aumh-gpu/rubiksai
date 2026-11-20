"""
rubiks: the RUBIKS terminal CLI.

Same brain, same memory and the same approvals as the phone and desktop apps. The CLI
is a client of the local RUBIKS server and starts it for you when it isn't running.

  rubiks                      interactive chat (default)
  rubiks ask "question"       one answer, then exit (pipe-friendly: cat log.txt | rubiks ask "summarize")
  rubiks task "goal"          start an autonomous multi-step task and watch it
  rubiks status               server, brain, PC vitals, running task
  rubiks audit [-n 20]        what Rubiks did on this PC, from any device
  rubiks serve [--lan]        run the server in this window (--lan: reachable from your phone)
  rubiks pair                 the link to open on your phone
  rubiks invite               create a teammate invite code (owner)
  rubiks key                  add a Gemini API key (owner, hidden prompt)
  rubiks login | logout       manage the CLI's session
  rubiks voice                the original voice assistant (main.py)
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        os.system("")  # enable ANSI escape handling on older consoles
    except Exception:
        pass

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

TTY = sys.stdout.isatty()
console = Console(highlight=False, soft_wrap=False)
err_console = Console(stderr=True, highlight=False)

ACCENT = "#ff6a2b"
DIM = "grey50"
CFG_PATH = os.environ.get("RUBIKS_CLI_CONFIG") or os.path.join(os.path.expanduser("~"), ".rubiks", "cli.json")
CFG_DIR = os.path.dirname(CFG_PATH)
DEFAULT_SERVER = os.environ.get("RUBIKS_SERVER", "http://127.0.0.1:7575")


# ─── Config ───────────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg: dict):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    try:
        os.chmod(CFG_PATH, 0o600)
    except Exception:
        pass


CFG = load_cfg()
SERVER = CFG.get("server", DEFAULT_SERVER)


# ─── HTTP ─────────────────────────────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def _req(method: str, path: str, body=None, stream=False, timeout=30):
    headers = {"X-Rubiks-Device": "terminal"}
    if CFG.get("token"):
        headers["Authorization"] = f"Bearer {CFG['token']}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(SERVER + path, data=data, method=method, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=None if stream else timeout)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error") or str(e)
        except Exception:
            msg = str(e)
        raise ApiError(msg, e.code)
    except urllib.error.URLError as e:
        raise ApiError(f"Can't reach the Rubiks server at {SERVER} ({e.reason}).", 0)
    if stream:
        return resp
    raw = resp.read().decode() or "{}"
    return json.loads(raw)


def api(method, path, body=None):
    return _req(method, path, body)


# ─── Server lifecycle ─────────────────────────────────────────────────────────

def server_up() -> bool:
    try:
        _req("GET", "/api/health", timeout=2)
        return True
    except ApiError:
        return False


def ensure_server(quiet=False) -> bool:
    if server_up():
        return True
    local = SERVER.startswith("http://127.0.0.1") or SERVER.startswith("http://localhost")
    if not local:
        err_console.print(f"[red]Can't reach {SERVER}.[/] Is the PC on and is the server running?")
        return False
    if not quiet:
        err_console.print(f"[{DIM}]Starting the Rubiks server in the background…[/]")
    from rubiks_core.config import LOG_PATH  # noqa: WPS433 (creates the data dir)

    log = open(LOG_PATH, "a", encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_PROCESS_GROUP | NO_WINDOW
    subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--quiet"], cwd=ROOT, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    for _ in range(60):
        time.sleep(0.25)
        if server_up():
            return True
    err_console.print(f"[red]The server didn't start.[/] Check the log: {LOG_PATH}")
    return False


# ─── Auth ─────────────────────────────────────────────────────────────────────

def ensure_login() -> bool:
    if CFG.get("token"):
        try:
            api("GET", "/api/me")
            return True
        except ApiError as e:
            if e.status != 401:
                raise
            CFG.pop("token", None)
    return cmd_login(None)


def cmd_login(_args) -> bool:
    if not ensure_server():
        return False
    boot = api("GET", "/api/bootstrap")
    console.print()
    if not boot["has_users"]:
        console.print(Text.assemble(("  Set up Rubiks", "bold"), ("  this first account owns the Rubiks on this PC", DIM)))
        name = console.input("  Name      ").strip()
        email = console.input("  Email     ").strip()
        pw = getpass.getpass("  Password  ")
        try:
            r = api("POST", "/api/auth/signup", {"name": name, "email": email, "password": pw, "client": "rubiks-cli"})
        except ApiError as e:
            console.print(f"  [red]{e}[/]")
            return False
    else:
        console.print(Text.assemble(("  Sign in", "bold"), ("  to the Rubiks on this PC", DIM)))
        email = console.input("  Email     ").strip()
        pw = getpass.getpass("  Password  ")
        try:
            r = api("POST", "/api/auth/login", {"email": email, "password": pw, "client": "rubiks-cli"})
        except ApiError as e:
            console.print(f"  [red]{e}[/]")
            return False
    CFG["token"] = r["token"]
    CFG["server"] = SERVER
    save_cfg(CFG)
    console.print(f"  [green]✓[/] Signed in as {r['user']['name']}\n")
    return True


def cmd_logout(_args):
    if CFG.get("token"):
        try:
            api("POST", "/api/auth/logout")
        except ApiError:
            pass
    CFG.pop("token", None)
    save_cfg(CFG)
    console.print("  Signed out.")


# ─── Rendering ────────────────────────────────────────────────────────────────

def hard_breaks(text: str) -> str:
    """Keeps single line breaks (tool output) instead of letting Markdown join them, except in code fences."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced or not line.strip() else line.rstrip() + "  ")
    return "\n".join(out)


def cube_glyph(state="idle") -> Text:
    color = {"idle": ACCENT, "ok": "green", "error": "red", "wait": "yellow"}.get(state, ACCENT)
    return Text("■", style=color)


def first_arg(args: dict) -> str:
    for v in (args or {}).values():
        if v not in ("", None):
            s = " ".join(str(v).split())
            return s[:70] + ("…" if len(s) > 70 else "")
    return ""


def move_line(ev, status) -> Text:
    mark = {"running": ("›", ACCENT), "waiting": ("?", "yellow"), "ok": ("✓", "green"), "fail": ("✗", "red")}[status]
    t = Text("  ")
    t.append(mark[0], style=mark[1])
    t.append(f" {ev.get('label') or ev.get('name')}", style="default")
    arg = first_arg(ev.get("args"))
    if arg:
        t.append(f"  {arg}", style=DIM)
    if ev.get("ms") is not None and status in ("ok", "fail"):
        ms = ev["ms"]
        t.append(f"  {ms / 1000:.1f}s" if ms >= 1000 else f"  {ms}ms", style=DIM)
    return t


def ask_approval(ev, resolved: threading.Event) -> str:
    """Prompts y/n, but gives up if the approval is answered on another device."""
    console.print()
    console.print(Text.assemble(("  ⚠ ", "yellow"), (f"{ev['label']}?", "bold")))
    if ev.get("risk"):
        console.print(f"    [{DIM}]{ev['risk']}[/]")
    for k, v in (ev.get("args") or {}).items():
        val = str(v)
        lines = val.splitlines() or [""]
        console.print(Text.assemble((f"    {k}: ", DIM), (lines[0][:200], "default")))
        for extra in lines[1:8]:
            console.print(Text("      " + extra[:200]))
    console.print(f"    [{DIM}]Allow once? [y/N]  (you can also answer from your phone)[/] ", end="")
    sys.stdout.flush()
    if sys.platform == "win32":
        import msvcrt

        while not resolved.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                if ch in ("y", "n", "\r", "\x1b"):
                    console.print("yes" if ch == "y" else "no")
                    return "approve" if ch == "y" else "deny"
            time.sleep(0.05)
        console.print("[dim]answered on another device[/]")
        return ""
    ans = input().strip().lower()
    return "approve" if ans in ("y", "yes") else "deny"


def stream_turn(message: str, conv_id=None, plain=False):
    """Sends a message and renders the streamed turn. Returns (conversation_id, reply)."""
    try:
        resp = _req("POST", "/api/chat", {"message": message, "conversation_id": conv_id, "device": "terminal"}, stream=True)
    except ApiError as e:
        err_console.print(f"  [red]{e}[/]")
        return conv_id, ""

    resolved = {}
    labels = {}
    reply = ""
    engine = ""
    status = None
    if TTY and not plain:
        status = console.status(Text("  working", style=DIM), spinner="dots", spinner_style=ACCENT)
        status.start()

    def stop_status():
        nonlocal status
        if status:
            status.stop()
            status = None

    last_running = None  # id of the tool whose "running" line is the last thing printed
    try:
        while True:
            chunk = resp.readline()
            if not chunk:
                break
            line = chunk.decode("utf-8", "replace").rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            t = ev.get("type")
            if t == "start":
                conv_id = ev.get("conversation_id")
                engine = ev.get("engine", "")
            elif t == "tool" and not plain:
                labels[ev["id"]] = ev
                if ev.get("status") == "running":
                    stop_status()
                    console.print(move_line(ev, "running"))
                    last_running = ev["id"]
            elif t == "tool_done" and not plain:
                stop_status()
                done = {**labels.get(ev["id"], {}), **ev}
                if console.is_terminal and last_running == ev["id"]:
                    console.file.write("\x1b[1A\x1b[2K")  # replace the "running" line in place
                console.print(move_line(done, "ok" if ev.get("ok") else "fail"))
                last_running = None
            elif t == "approval":
                stop_status()
                last_running = None
                ev_done = threading.Event()
                resolved[ev["id"]] = ev_done
                if plain or not sys.stdin.isatty():
                    err_console.print(f"  [yellow]Needs approval:[/] {ev['label']}. Approve it in the app.")
                    continue
                decision = ask_approval(ev, ev_done)
                if decision:
                    try:
                        api("POST", f"/api/approvals/{ev['id']}", {"decision": decision})
                    except ApiError as e:
                        console.print(f"    [{DIM}]{e}[/]")
            elif t == "approval_resolved":
                if ev["id"] in resolved:
                    resolved[ev["id"]].set()
            elif t == "image" and not plain:
                stop_status()
                console.print(f"  [{DIM}]› screenshot sent to the app ({SERVER}{ev['url']})[/]")
            elif t == "done":
                reply = ev.get("text", "")
                engine = ev.get("engine", engine)
            elif t == "error":
                reply = ""
                stop_status()
                err_console.print(f"  [red]{ev.get('message')}[/]")
    finally:
        stop_status()
        resp.close()

    if reply:
        if plain:
            print(reply)
        else:
            console.print()
            head = Text("  ")
            head.append_text(cube_glyph())
            head.append(" rubiks", style="bold")
            if engine == "offline":
                head.append("  offline mode", style=DIM)
            console.print(head)
            console.print(Markdown(hard_breaks(reply), code_theme="ansi_dark"), width=min(console.width, 100))
            console.print()
    return conv_id, reply


# ─── Commands ─────────────────────────────────────────────────────────────────

def print_banner(user, engine):
    rows = [Text(), Text(), Text()]
    for r in range(3):
        rows[r].append("  ")
        for c in range(3):
            rows[r].append("■", style=ACCENT if (r, c) == (1, 1) else "grey30")
            rows[r].append(" ")
    rows[0].append("  rubiks", style="bold")
    rows[1].append(f"  {user['name']}", style="default")
    rows[1].append(f"  ·  {'Gemini' if engine == 'gemini' else 'offline mode'}", style=DIM)
    rows[2].append("  /help for commands, Ctrl+C to quit", style=DIM)
    console.print()
    for r in rows:
        console.print(r)
    console.print()


HELP = [
    ("/new", "start a fresh conversation"),
    ("/history", "recent conversations"),
    ("/open <n>", "continue conversation n from /history"),
    ("/task <goal>", "start an autonomous multi-step task"),
    ("/status", "PC vitals and running task"),
    ("/audit", "last actions Rubiks took"),
    ("/pair", "link for your phone"),
    ("/exit", "quit"),
]


def cmd_chat(args):
    if not ensure_server() or not ensure_login():
        return 1
    boot = api("GET", "/api/bootstrap")
    print_banner(boot["user"], boot["engine"])
    conv_id = None
    history = []
    while True:
        try:
            line = console.input(Text.assemble(("› ", ACCENT))).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return 0
        if not line:
            continue
        if line.startswith("/"):
            cmd, _, rest = line.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/exit", "/quit", "/q"):
                return 0
            if cmd == "/help":
                t = Table.grid(padding=(0, 3))
                for a, b in HELP:
                    t.add_row(Text("  " + a, style=ACCENT), Text(b, style=DIM))
                console.print(t)
                continue
            if cmd == "/new":
                conv_id = None
                console.print(f"  [{DIM}]new conversation[/]")
                continue
            if cmd == "/history":
                history = api("GET", "/api/conversations")["items"][:12]
                for i, c in enumerate(history, 1):
                    when = time.strftime("%d %b %H:%M", time.localtime(c["updated_at"]))
                    console.print(Text.assemble((f"  {i:>2} ", ACCENT), (c["title"][:60], "default"), (f"  {c.get('device') or ''} · {when}", DIM)))
                continue
            if cmd == "/open":
                try:
                    conv_id = history[int(rest) - 1]["id"]
                    console.print(f"  [{DIM}]continuing “{history[int(rest) - 1]['title']}”[/]")
                except Exception:
                    console.print(f"  [{DIM}]run /history first, then /open <number>[/]")
                continue
            if cmd == "/task":
                cmd_task(argparse.Namespace(goal=[rest], watch=True))
                continue
            if cmd == "/status":
                cmd_status(None)
                continue
            if cmd == "/audit":
                cmd_audit(argparse.Namespace(n=12))
                continue
            if cmd == "/pair":
                cmd_pair(None)
                continue
            console.print(f"  [{DIM}]unknown command, try /help[/]")
            continue
        try:
            conv_id, _ = stream_turn(line, conv_id)
        except KeyboardInterrupt:
            console.print(f"\n  [{DIM}]stopped watching. Rubiks will finish in the background.[/]")


def cmd_ask(args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    question = " ".join(args.question).strip()
    if not sys.stdin.isatty():
        piped = sys.stdin.read()
        if piped.strip():
            question = f"{question}\n\n---\n{piped[:15000]}" if question else piped[:15000]
    if not question:
        err_console.print('  usage: rubiks ask "your question"')
        return 2
    _, reply = stream_turn(question, None, plain=not TTY)
    return 0 if reply else 1


def cmd_status(_args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    v = api("GET", "/api/vitals")
    s = api("GET", "/api/settings")
    t = api("GET", "/api/tasks").get("task")
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=DIM, justify="right")
    grid.add_column()
    grid.add_row("pc", v.get("host", "?"))
    grid.add_row("brain", f"Gemini · {s['model']} · {s['key_count']} key(s)" if s["engine"] == "gemini" else "offline mode (rubiks key to add one)")
    bar = lambda p: f"{'█' * round((p or 0) / 10)}{'░' * (10 - round((p or 0) / 10))} {p}%"
    grid.add_row("cpu", bar(v["cpu"]))
    grid.add_row("memory", bar(v["ram"]) + f" of {v['ram_gb']} GB")
    if v.get("disk") is not None:
        grid.add_row("disk", bar(v["disk"]))
    if v.get("battery"):
        b = v["battery"]
        grid.add_row("battery", f"{b['percent']}%{' charging' if b['plugged'] else ''}")
    grid.add_row("phone", ", ".join(s["lan_urls"]) if s["lan_urls"] else "off (rubiks serve --lan)")
    if t:
        grid.add_row("task", f"{t['goal'][:60]} · {t['status']} {t['progress']}")
    console.print()
    console.print(grid)
    console.print()
    return 0


def cmd_audit(args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    rows = api("GET", f"/api/activity?limit={getattr(args, 'n', 20) or 20}")["items"]
    if not rows:
        console.print(f"  [{DIM}]No actions yet.[/]")
        return 0
    t = Table(box=None, padding=(0, 2), header_style=DIM, show_edge=False)
    for col in ("when", "from", "action", "detail", "result"):
        t.add_column(col)
    for r in rows:
        ok = r["decision"] in ("deny", "timeout") and "blocked" or ("ok" if r["ok"] else "failed")
        style = {"ok": "green", "failed": "red", "blocked": "yellow"}[ok]
        t.add_row(time.strftime("%d %b %H:%M", time.localtime(r["created_at"])), r.get("device") or "-", r["label"],
                  Text(first_arg(r["args"])[:48], style=DIM), Text(ok, style=style))
    console.print()
    console.print(t)
    console.print()
    return 0


def cmd_task(args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    goal = " ".join(args.goal).strip()
    if goal:
        try:
            r = api("POST", "/api/tasks", {"goal": goal})
            console.print(f"  [{ACCENT}]■[/] {r['message']}")
        except ApiError as e:
            console.print(f"  [red]{e}[/]")
            return 1
    t = api("GET", "/api/tasks").get("task")
    if not t:
        console.print(f"  [{DIM}]No task yet. Try: rubiks task \"research 5 CRMs for a 10-person agency\"[/]")
        return 0

    def render(task):
        tbl = Table.grid(padding=(0, 1))
        tbl.add_row(Text(f"  {task['goal'][:80]}", style="bold"))
        tbl.add_row(Text(f"  {task['status'].lower()} · {task['progress']}", style=DIM))
        for s in task["steps"]:
            mark, style = {"DONE": ("✓", "green"), "RUNNING": ("›", ACCENT), "RETRYING": ("↻", "yellow"),
                           "FAILED": ("✗", "red")}.get(s["status"], ("·", DIM))
            tbl.add_row(Text.assemble(("  " + mark + " ", style), (s["description"][:90], "default" if s["status"] != "PENDING" else DIM)))
        return tbl

    if not getattr(args, "watch", True) or not TTY:
        console.print(render(t))
        return 0
    try:
        with Live(render(t), console=console, refresh_per_second=4, transient=False) as live:
            while t and t.get("running"):
                time.sleep(2)
                t = api("GET", "/api/tasks").get("task") or t
                live.update(render(t))
        console.print(f"  [green]✓[/] {t.get('summary') or 'Task finished.'}\n")
    except KeyboardInterrupt:
        console.print(f"\n  [{DIM}]stopped watching; the task keeps running. rubiks task to check again.[/]")
    return 0


def cmd_pair(_args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    s = api("GET", "/api/settings")
    if not s["is_owner"]:
        console.print(f"  [{DIM}]Only the owner can see pairing links.[/]")
        return 1
    if not s["lan_urls"]:
        console.print("\n  Phone access is off. Restart the server so your phone can reach it:")
        console.print(f"    [{ACCENT}]rubiks serve --lan[/]\n")
        return 0
    console.print("\n  On your phone (same Wi-Fi), open:\n")
    for u in s["lan_urls"]:
        console.print(f"    [bold]{u}[/]")
    try:
        import qrcode  # optional

        qr = qrcode.QRCode(border=1)
        qr.add_data(s["lan_urls"][0])
        console.print()
        qr.print_ascii(invert=True)
    except ImportError:
        console.print(f"\n  [{DIM}]pip install qrcode to show a scannable code here[/]")
    console.print(f"\n  [{DIM}]Then Share → Add to Home Screen to install it.[/]\n")
    return 0


def cmd_invite(_args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    try:
        code = api("POST", "/api/invites")["code"]
    except ApiError as e:
        console.print(f"  [red]{e}[/]")
        return 1
    console.print(f"\n  Invite code  [bold {ACCENT}]{code}[/]\n  [{DIM}]Single use. They choose “Join with invite” on the sign-in screen.[/]\n")
    return 0


def cmd_key(_args):
    if not ensure_server(quiet=True) or not ensure_login():
        return 1
    key = getpass.getpass("  Gemini API key (input hidden): ").strip()
    if not key:
        return 1
    try:
        r = api("POST", "/api/settings/key", {"key": key})
        console.print(f"  [green]✓[/] Saved. {r['key_count']} key(s), brain: {r['engine']}")
    except ApiError as e:
        console.print(f"  [red]{e}[/]")
        return 1
    return 0


def cmd_serve(args):
    os.chdir(ROOT)
    import server  # noqa: WPS433

    server.run(lan=args.lan, port=args.port)


def cmd_voice(_args):
    os.chdir(ROOT)
    return subprocess.call([sys.executable, os.path.join(ROOT, "main.py")])


def main(argv=None):
    p = argparse.ArgumentParser(prog="rubiks", description="RUBIKS: your AI operator, in the terminal.")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="interactive chat (default)")
    a = sub.add_parser("ask", help="ask once and print the answer")
    a.add_argument("question", nargs="*")
    t = sub.add_parser("task", help="start or watch an autonomous task")
    t.add_argument("goal", nargs="*")
    sub.add_parser("status", help="PC vitals, brain and task")
    au = sub.add_parser("audit", help="recent actions")
    au.add_argument("-n", type=int, default=20)
    s = sub.add_parser("serve", help="run the server in this window")
    s.add_argument("--lan", action="store_true", help="reachable from phones on the same Wi-Fi")
    s.add_argument("--port", type=int, default=int(os.environ.get("RUBIKS_PORT", "7575")))
    sub.add_parser("pair", help="link for your phone")
    sub.add_parser("invite", help="create a teammate invite (owner)")
    sub.add_parser("key", help="add a Gemini API key (owner)")
    sub.add_parser("login", help="sign the CLI in")
    sub.add_parser("logout", help="sign the CLI out")
    sub.add_parser("voice", help="the original voice assistant")
    args = p.parse_args(argv)

    handlers = {
        None: cmd_chat, "chat": cmd_chat, "ask": cmd_ask, "task": cmd_task, "status": cmd_status, "audit": cmd_audit,
        "serve": cmd_serve, "pair": cmd_pair, "invite": cmd_invite, "key": cmd_key, "login": cmd_login,
        "logout": cmd_logout, "voice": cmd_voice,
    }
    try:
        code = handlers[args.cmd](args)
    except ApiError as e:
        err_console.print(f"  [red]{e}[/]")
        code = 1
    except KeyboardInterrupt:
        code = 130
    if code is True or code is None:
        code = 0
    elif code is False:
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
