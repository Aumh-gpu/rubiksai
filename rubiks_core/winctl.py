"""Reliable window management for Windows.

Background processes (like the Rubiks server) are normally not allowed to bring a window
to the front, so naive `window.activate()` silently fails and keystrokes land in whatever
window the user had open. This module:
  - finds windows by their owning process, not just by a title that other windows may share
  - uses the documented ways to get foreground rights (Alt-key trick, AttachThreadInput)
  - verifies the target really is in front, so callers can refuse to type otherwise
  - explains elevation (UIPI) problems instead of failing silently
"""

import ctypes
import ctypes.wintypes as wt
import time

import psutil

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
advapi32 = ctypes.windll.advapi32

SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


class Window:
    def __init__(self, hwnd: int, title: str, pid: int, process: str):
        self.hwnd, self.title, self.pid, self.process = hwnd, title, pid, process

    def rect(self):
        r = wt.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        return r.left, r.top, r.right, r.bottom

    def __repr__(self):
        return f"<{self.process} '{self.title}'>"


def list_windows() -> list:
    """Visible, titled top-level windows with their process names."""
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            proc = psutil.Process(pid.value).name()
        except Exception:
            proc = "?"
        out.append(Window(hwnd, buf.value, pid.value, proc))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(processes=(), title_contains: str = "", exclude_processes=("msedgewebview2.exe",)):
    """First window whose process is in `processes` (case-insensitive) and/or whose title contains text."""
    procs = {p.lower() for p in processes}
    excl = {p.lower() for p in exclude_processes}
    needle = title_contains.lower()
    for w in list_windows():
        p = w.process.lower()
        if p in excl:
            continue
        if procs and p not in procs:
            continue
        if needle and needle not in w.title.lower():
            continue
        return w
    return None


def _is_elevated(pid: int):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    tok = wt.HANDLE()
    try:
        if not advapi32.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            return None
        val, size = wt.DWORD(), wt.DWORD()
        advapi32.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(size))  # TokenElevation
        return bool(val.value)
    finally:
        kernel32.CloseHandle(h)


def elevation_problem(win: Window) -> str:
    """Windows blocks input from normal apps into apps running as administrator (UIPI)."""
    me_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    if _is_elevated(win.pid) and not me_admin:
        return (f"{win.process} is running as administrator, and Windows blocks normal apps from typing into it. "
                f"Restart {win.process} normally (not 'Run as administrator'), or run Rubiks as administrator.")
    return ""


def foreground_hwnd() -> int:
    return user32.GetForegroundWindow()


def focus(win: Window, timeout: float = 2.0) -> bool:
    """Brings `win` to the front and returns True only if it really is the foreground window."""
    hwnd = win.hwnd
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        attempt += 1
        if attempt % 2:
            # A synthetic Alt press gives this process the right to set the foreground window.
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            user32.SetForegroundWindow(hwnd)
        else:
            fg = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg, None)
            me = kernel32.GetCurrentThreadId()
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)
            user32.AttachThreadInput(me, fg_thread, True)
            user32.AttachThreadInput(me, target_thread, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(me, target_thread, False)
            user32.AttachThreadInput(me, fg_thread, False)
        time.sleep(0.15)
    return user32.GetForegroundWindow() == hwnd


def still_in_front(win: Window) -> bool:
    return user32.GetForegroundWindow() == win.hwnd


def describe_foreground() -> str:
    fg = user32.GetForegroundWindow()
    for w in list_windows():
        if w.hwnd == fg:
            return f"{w.process} ('{w.title[:50]}')"
    return "an unknown window"


def close_window(win: Window):
    user32.PostMessageW(win.hwnd, 0x0010, 0, 0)  # WM_CLOSE


# ─── Tools the AI can call ───────────────────────────────────────────────────

def focus_window(app_or_title: str) -> str:
    """Brings a window to the front by app name (e.g. 'WhatsApp', 'chrome', 'notepad') or by part of its title,
    and confirms which window is actually in front afterwards. Use before typing into any app."""
    q = app_or_title.strip().lower()
    wins = [w for w in list_windows() if w.process.lower() != "msedgewebview2.exe"]
    target = (next((w for w in wins if w.process.lower().startswith(q)), None)
              or next((w for w in wins if q in w.process.lower()), None)
              or next((w for w in wins if q in w.title.lower()), None))
    if not target:
        open_apps = ", ".join(sorted({w.process.replace('.exe', '') for w in wins}))[:300]
        return f"No open window matches '{app_or_title}'. Open apps: {open_apps}"
    problem = elevation_problem(target)
    if problem:
        return problem
    if focus(target):
        return f"{target.process} ('{target.title[:60]}') is now in front."
    return (f"Windows refused to bring {target.process} to the front; {describe_foreground()} is still in front. "
            f"Nothing was typed. Ask the user to click {target.process} once, then retry.")
