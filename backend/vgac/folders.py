"""Open a folder in the file explorer and bring its window to the front (owner, 2026-11-12: "it does open the folder,
but doesn't bring it to the front, so you don't know it happened").

Why the window stayed behind: the backend runs hidden in the background, and Windows does not let a background
process put a new window in front of the one the person is using (the foreground lock), so Explorer opened behind
the app. Here the backend waits for the Explorer window that shows the folder (a new one, or the one already open on
it, which Explorer reuses), then:

1. restores it if it is minimised and raises it, with this thread's input briefly attached to the foreground window's
   thread (the documented way a helper may hand focus on, since that thread holds the foreground);
2. when Windows still refuses, flashes its taskbar button until it is clicked, so there is something to see.

The result says which happened (`front`), so the page can tell the person where the folder is. On macOS and Linux
the system's own opener is used as before (it raises the window itself).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

WAIT_SECONDS = 4.0
POLL_SECONDS = 0.1
EXPLORER_CLASS = "CabinetWClass"


def open_in_explorer(folder: Path) -> dict[str, Any]:
    """Open `folder` in the file explorer. -> {"front": True | False | None} (None: not Windows, or the window was not
    found in time; the folder still opens). OSError when the folder cannot be opened at all."""
    if sys.platform != "win32":
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        return {"front": None}
    before = set(_explorer_windows())
    os.startfile(str(folder))  # type: ignore[attr-defined]  # Explorer, on this folder only
    hwnd = _wait_for_window(folder, before)
    if hwnd is None:
        return {"front": None}
    return {"front": _bring_to_front(hwnd)}


# --- Windows ---------------------------------------------------------------------------------------------------------


def _user32() -> Any:
    import ctypes

    return ctypes.WinDLL("user32", use_last_error=True)


def _explorer_windows() -> dict[int, str]:
    """Visible Explorer folder windows: {hwnd: title}."""
    import ctypes
    from ctypes import wintypes

    u = _user32()
    found: dict[int, str] = {}
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd: int, _l: int) -> bool:
        if not u.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if cls.value != EXPLORER_CLASS:
            return True
        n = u.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, title, n + 1)
        found[int(hwnd)] = title.value
        return True

    u.EnumWindows(proto(each), 0)
    return found


def title_matches(title: str, folder: Path) -> bool:
    """Explorer titles a folder window with the folder's name, or its full path when "Display the full path in the
    title bar" is on (newer builds may add " - File Explorer")."""
    t = title.strip()
    for suffix in (" - File Explorer", " - Explorer"):
        if t.endswith(suffix):
            t = t[: -len(suffix)]
    names = {folder.name.lower(), str(folder).lower(), str(folder).rstrip("\\/").lower()}
    return t.lower() in names


def pick_window(windows: dict[int, str], folder: Path, before: set[int]) -> int | None:
    """The window showing `folder`: a new one first (just opened), else one that was already open on it."""
    matching = [h for h, t in windows.items() if title_matches(t, folder)]
    new = [h for h in matching if h not in before]
    if new:
        return new[0]
    return matching[0] if matching else None


def _wait_for_window(folder: Path, before: set[int]) -> int | None:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        hwnd = pick_window(_explorer_windows(), folder, before)
        if hwnd is not None:
            return hwnd
        time.sleep(POLL_SECONDS)
    return None


def _bring_to_front(hwnd: int) -> bool:
    import ctypes
    from ctypes import wintypes

    u = _user32()
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    sw_restore, sw_show = 9, 5
    u.ShowWindow(hwnd, sw_restore if u.IsIconic(hwnd) else sw_show)
    fg = u.GetForegroundWindow()
    if fg == hwnd:
        return True
    me = k.GetCurrentThreadId()
    fg_thread = u.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetActiveWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_thread, False)
    if u.GetForegroundWindow() == hwnd:
        return True

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND), ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT), ("dwTimeout", wintypes.DWORD)]

    flash_all, flash_until_front = 0x3, 0xC
    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, flash_all | flash_until_front, 0, 0)
    u.FlashWindowEx(ctypes.byref(info))
    return False
