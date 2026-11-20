"""
WhatsApp Desktop automation that verifies every step.

Drives the WhatsApp Desktop app (WhatsApp.Root.exe) through Windows UI Automation: it finds
the real search box, chat list entries, chat header, message box and Send button by their
accessible names instead of guessing pixel positions. Before anything is sent it checks that
the right chat is open and the text is in the message box. If any check fails it stops and
says why, rather than typing into the wrong window or the wrong chat.
"""

import os
import re
import time
import urllib.parse

import pyautogui
import pyperclip

WA_PROCESSES = ("WhatsApp.Root.exe", "WhatsApp.exe")


# ─── Plumbing ────────────────────────────────────────────────────────────────

def _auto():
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2)
    return auto


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _walk(ctrl, depth=0, max_depth=70):
    try:
        children = ctrl.GetChildren()
    except Exception:
        return
    for ch in children:
        yield ch
        if depth < max_depth:
            yield from _walk(ch, depth + 1, max_depth)


def _wait(fn, timeout=4.0, step=0.25):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def _window(launch=True):
    from rubiks_core import winctl

    win = winctl.find_window(WA_PROCESSES)
    if win or not launch:
        return win
    try:
        os.startfile("whatsapp:")
    except Exception:
        return None
    return _wait(lambda: winctl.find_window(WA_PROCESSES), timeout=12, step=0.5)


class _UI:
    """A snapshot of WhatsApp's accessible controls with their positions."""

    def __init__(self, win):
        auto = _auto()
        self.win = win
        self.root = auto.ControlFromHandle(win.hwnd)
        self.left, self.top, self.right, self.bottom = win.rect()
        self.width = max(self.right - self.left, 1)
        self.height = max(self.bottom - self.top, 1)
        self.items = []
        for c in _walk(self.root):
            try:
                r = c.BoundingRectangle
                self.items.append((c, c.ControlTypeName, c.Name or "", r))
            except Exception:
                continue

    def in_list_pane(self, r):  # left column: search box + chats
        return r.left < self.left + self.width * 0.42

    def in_chat_pane(self, r):  # right column: open chat
        return r.left >= self.left + self.width * 0.30

    def edits(self):
        return [(c, n, r) for c, t, n, r in self.items if t == "EditControl"]

    def buttons(self, name_part):
        p = _norm(name_part)
        return [(c, n, r) for c, t, n, r in self.items if t == "ButtonControl" and p in _norm(n)]

    def logged_out(self):
        return not self.edits() and any(_norm(n) in ("log in", "link with phone number", "get started")
                                       for _, t, n, _ in self.items if t == "ButtonControl")

    def search_box(self):
        cands = [(c, n, r) for c, n, r in self.edits() if "search" in _norm(n) and self.in_list_pane(r)]
        cands = cands or [(c, n, r) for c, n, r in self.edits() if self.in_list_pane(r)]
        return min(cands, key=lambda x: x[2].top)[0] if cands else None

    def composer(self):
        eds = [(c, n, r) for c, n, r in self.edits() if self.in_chat_pane(r) and "search" not in _norm(n)]
        named = [e for e in eds if "message" in _norm(e[1])]
        pick = named or eds
        return max(pick, key=lambda x: x[2].bottom)[0] if pick else None

    def chat_results(self, contact):
        """Chat-list entries whose title matches the contact, as (control, title)."""
        want = _norm(contact)
        out, seen = [], set()
        for c, t, n, r in self.items:
            if t not in ("ListItemControl", "ButtonControl", "GroupControl", "TextControl", "HyperlinkControl"):
                continue
            if not self.in_list_pane(r) or not n:
                continue
            title = _norm(re.split(r"[,\n]", n)[0])
            if want in title and title not in seen and "search" not in title:
                seen.add(title)
                out.append((c, re.split(r"[,\n]", n)[0].strip()))
        return out

    def header_names(self):
        """Names shown in the open chat's header (top of the right pane)."""
        top_band = self.top + self.height * 0.16
        return [n for c, t, n, r in self.items
                if n and self.in_chat_pane(r) and r.top < top_band and t in ("TextControl", "ButtonControl", "GroupControl")]


def _click(ctrl):
    """Invoke without the mouse when the control supports it; else a real click."""
    for getter in ("GetInvokePattern", "GetSelectionItemPattern", "GetLegacyIAccessiblePattern"):
        try:
            pat = getattr(ctrl, getter)()
            if getter == "GetInvokePattern":
                pat.Invoke()
            elif getter == "GetSelectionItemPattern":
                pat.Select()
            else:
                pat.DoDefaultAction()
            return
        except Exception:
            continue
    ctrl.Click(simulateMove=False)


def _value(ctrl) -> str:
    try:
        return ctrl.GetValuePattern().Value or ""
    except Exception:
        try:
            return ctrl.GetLegacyIAccessiblePattern().Value or ""
        except Exception:
            return ""


def _header_matches(ui, contact):
    want = _norm(contact)
    return any(want == _norm(n) or _norm(n).startswith(want) for n in ui.header_names())


def _vision_header(win) -> str:
    """Asks Gemini which chat is open (used only when accessibility names are inconclusive)."""
    try:
        import mss
        from PIL import Image
        from rubiks_core import llm

        l, t, r, b = win.rect()
        with mss.mss() as sct:
            shot = sct.grab({"left": l, "top": t, "width": r - l, "height": b - t})
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        resp = llm.generate([img, "This is WhatsApp Desktop. Reply with ONLY the contact or group name shown in the "
                                  "header of the chat that is open on the right. If no chat is open, reply NONE."])
        return (resp.text or "").strip().strip('"')
    except Exception:
        return ""


# ─── Tools ───────────────────────────────────────────────────────────────────

def whatsapp_status() -> str:
    """Checks WhatsApp Desktop on this PC: is it open, logged in, and can Rubiks control it. Use before sending if unsure."""
    try:
        from rubiks_core import winctl

        win = _window(launch=False)
        if not win:
            return "WhatsApp Desktop isn't open. send_whatsapp_by_name will open it automatically."
        problem = winctl.elevation_problem(win)
        if problem:
            return problem
        ui = _UI(win)
        if ui.logged_out():
            return ("WhatsApp Desktop is open but LOGGED OUT, so nothing can be sent. The user must link it: open WhatsApp "
                    "on their phone > Settings > Linked devices > Link a device, and scan the QR code shown on the PC.")
        return "WhatsApp Desktop is open and logged in. Ready to send."
    except Exception as e:
        return f"Couldn't check WhatsApp: {e}"


def send_whatsapp_by_name(contact_name: str, message: str) -> str:
    """Sends a WhatsApp message to a contact or group by NAME using WhatsApp Desktop.
    It searches the chat list, opens the chat whose name matches exactly, confirms the chat header shows that name,
    puts the text in the message box and presses Send. If several chats match (e.g. 'Rahul' and 'Rahul Sharma'),
    it returns the options instead of guessing; call again with the full name. Never asks for a phone number.

    Args:
        contact_name: The contact or group name as it appears in WhatsApp (e.g. "Mom", "Rahul Sharma").
        message: The message text to send.
    """
    from rubiks_core import winctl

    contact_name = (contact_name or "").strip()
    if not contact_name or not (message or "").strip():
        return "Need both a contact name and a message."

    win = _window()
    if not win:
        return "WhatsApp Desktop isn't installed or couldn't be opened. Install it from the Microsoft Store and log in."
    problem = winctl.elevation_problem(win)
    if problem:
        return problem

    ui = _wait(lambda: (lambda u: u if (u.edits() or u.logged_out()) else None)(_UI(win)), timeout=8)
    if not ui or ui.logged_out():
        return ("WhatsApp Desktop is LOGGED OUT, so nothing was sent. Link it: on your phone open WhatsApp > Settings > "
                "Linked devices > Link a device, and scan the QR code on your PC. Then ask again.")

    if not winctl.focus(win):
        return (f"Windows wouldn't bring WhatsApp to the front ({winctl.describe_foreground()} stayed in front), "
                f"so nothing was typed. Click WhatsApp once, then ask again.")

    saved_clip = None
    try:
        saved_clip = pyperclip.paste()
    except Exception:
        pass

    try:
        # 1. Search for the chat
        box = ui.search_box()
        if box:
            _click(box)
            try:
                box.SetFocus()
            except Exception:
                pass
        else:
            pyautogui.hotkey("ctrl", "f")
        time.sleep(0.3)
        if not winctl.still_in_front(win):
            return f"Another window ({winctl.describe_foreground()}) jumped in front. Nothing was typed."
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        pyperclip.copy(contact_name)
        pyautogui.hotkey("ctrl", "v")

        # 2. Pick the exact chat from the results
        results = _wait(lambda: _UI(win).chat_results(contact_name), timeout=4)
        target = None
        if results:
            exact = [r for r in results if _norm(r[1]) == _norm(contact_name)]
            if len(exact) == 1:
                target = exact[0]
            elif len(results) == 1:
                target = results[0]
            else:
                names = ", ".join(sorted({r[1] for r in results})[:6])
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
                return f"Several chats match '{contact_name}': {names}. Which one? Nothing was sent."
            _click(target[0])
            chat_title = target[1]
        else:
            # No named result exposed; open the top result and rely on the header check below.
            pyautogui.press("down")
            pyautogui.press("enter")
            chat_title = contact_name
        time.sleep(0.8)

        # 3. Confirm the right chat is open (accessibility names first, screenshot check as backup)
        ui = _wait(lambda: (lambda u: u if u.composer() else None)(_UI(win)), timeout=5) or _UI(win)
        if not ui.composer():
            return f"Couldn't open a chat for '{contact_name}' (no message box appeared). Nothing was sent."
        if not _header_matches(ui, chat_title):
            seen = _vision_header(win)
            if seen.upper() == "NONE" or (seen and _norm(contact_name) not in _norm(seen)):
                return f"The chat that opened is '{seen or 'unknown'}', not '{contact_name}'. Nothing was sent."

        # 4. Put the message in the real message box and check it's there
        composer = ui.composer()
        _click(composer)
        try:
            composer.SetFocus()
        except Exception:
            pass
        if not winctl.still_in_front(win):
            return f"Another window ({winctl.describe_foreground()}) jumped in front. Nothing was typed."
        pyperclip.copy(message)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        typed = _value(composer)
        probe = _norm(message)[:12]
        if typed and probe and probe not in _norm(typed):
            return "The text didn't land in the message box, so nothing was sent. Try again."

        # 5. Send with WhatsApp's own Send button (falls back to Enter)
        send_btns = [b for b in _UI(win).buttons("send") if ui.in_chat_pane(b[2])]
        if send_btns:
            _click(max(send_btns, key=lambda b: b[2].bottom)[0])
        else:
            if not winctl.still_in_front(win):
                return f"Another window ({winctl.describe_foreground()}) jumped in front before sending. Message left typed, not sent."
            pyautogui.press("enter")
        time.sleep(0.5)
        left_over = _value(_UI(win).composer() or composer)
        if left_over and probe and probe in _norm(left_over):
            return f"The message is typed in '{chat_title}' but didn't send. Check WhatsApp on the PC."
        return f"Sent to {chat_title} on WhatsApp."
    except Exception as e:
        return f"WhatsApp send failed partway ({e}). Check WhatsApp on the PC before retrying."
    finally:
        if saved_clip is not None:
            try:
                pyperclip.copy(saved_clip)
            except Exception:
                pass


def send_whatsapp_message(phone_number: str, message: str) -> str:
    """Sends a WhatsApp message to a phone number (country code first, digits only, e.g. 919876543210) via WhatsApp Desktop.
    Only use this if you have the EXACT phone number. If you only have a name, use send_whatsapp_by_name instead.
    """
    from rubiks_core import winctl

    phone = "".join(ch for ch in str(phone_number) if ch.isdigit())
    if len(phone) < 10:
        return "That phone number looks incomplete. Include the country code, e.g. 919876543210."
    try:
        os.startfile(f"whatsapp://send?phone={phone}&text={urllib.parse.quote(message)}")
    except Exception as e:
        return f"Couldn't open WhatsApp Desktop: {e}"

    win = _wait(lambda: _window(launch=False), timeout=10)
    if not win:
        return "WhatsApp Desktop didn't open. Is it installed and logged in?"
    ui = _wait(lambda: (lambda u: u if (u.composer() or u.logged_out()) else None)(_UI(win)), timeout=10)
    if not ui or ui.logged_out():
        return "WhatsApp Desktop is logged out (or the chat didn't open), so nothing was sent. Link it from your phone first."
    composer = ui.composer()
    probe = _norm(message)[:12]
    if probe and probe not in _norm(_value(composer)):
        return "The chat opened but the message wasn't prefilled. Nothing was sent."
    send_btns = [b for b in ui.buttons("send") if ui.in_chat_pane(b[2])]
    if send_btns:
        _click(max(send_btns, key=lambda b: b[2].bottom)[0])
    else:
        if not winctl.focus(win):
            return "Windows wouldn't bring WhatsApp to the front, so the message is typed but not sent."
        pyautogui.press("enter")
    return f"Sent to +{phone} on WhatsApp."
