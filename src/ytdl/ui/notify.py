"""Windows notifications via a short-lived tray icon (Shell_NotifyIcon).

Windows 10/11 show tray balloons as regular toast notifications, so this needs
no extra packages or app registration.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

NIM_ADD, NIM_DELETE = 0, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x02, 0x04, 0x10
NIIF_USER, NIIF_LARGE_ICON = 0x04, 0x20
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
ICON_ID = 7001
REMOVE_AFTER_MS = 15000


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class Notifier:
    def __init__(self, root, icon_path: Path) -> None:
        self.root = root
        self.icon_path = icon_path
        self._icon = None
        self._shown = False
        self._remove_job = None

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def _data(self) -> NOTIFYICONDATAW:
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = int(self.root.wm_frame(), 16)
        data.uID = ICON_ID
        return data

    def _load_icon(self):
        if self._icon is None and self.icon_path.exists():
            self._icon = ctypes.windll.user32.LoadImageW(
                None, str(self.icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
        return self._icon

    def add_icon(self, title: str = "YTDL", message: str = "", balloon: bool = True) -> bool:
        """Show the tray icon, with a toast if balloon is set. Returns success."""
        if not self.supported:
            return False
        self.remove()
        data = self._data()
        data.uFlags = NIF_ICON | NIF_TIP
        data.hIcon = self._load_icon()
        data.szTip = "YTDL"
        if balloon:
            data.uFlags |= NIF_INFO
            data.szInfoTitle = title[:63]
            data.szInfo = message[:255]
            data.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
            data.hBalloonIcon = data.hIcon
        self._shown = bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)))
        if self._shown:
            self._remove_job = self.root.after(REMOVE_AFTER_MS, self.remove)
        return self._shown

    def notify(self, title: str, message: str) -> bool:
        return self.add_icon(title, message, balloon=True)

    def remove(self) -> None:
        if self._remove_job:
            self.root.after_cancel(self._remove_job)
            self._remove_job = None
        if self._shown:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._data()))
            self._shown = False
