"""Banner offering to download a link that was copied or dropped on the window."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.ui.common import SECONDARY
from ytdl.ui.format_dialog import FormatDialog

if TYPE_CHECKING:
    from ytdl.ui.app import App

YOUTUBE_URL = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/|playlist\?|live/|@|channel/|c/)|youtu\.be/)\S+$",
    re.IGNORECASE,
)
ANY_URL = re.compile(r"^https?://\S+$", re.IGNORECASE)


def is_youtube_url(text: str) -> bool:
    return bool(YOUTUBE_URL.match(text.strip()))


def is_playlist_url(url: str) -> bool:
    return "/playlist" in url or ("list=" in url and "v=" not in url)


def url_from_drop(app: App, data: str) -> str | None:
    """Pull a URL out of dropped text, or out of a dropped .url shortcut file."""
    for item in app.tk.splitlist(data):
        item = item.strip()
        if ANY_URL.match(item) or is_youtube_url(item):
            return item if item.startswith("http") else f"https://{item}"
        path = Path(item)
        if path.suffix.lower() == ".url" and path.is_file():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.upper().startswith("URL="):
                    return line[4:].strip()
    return None


class LinkBanner(ctk.CTkFrame):
    def __init__(self, app: App) -> None:
        super().__init__(app, fg_color=("#dbe9f7", "#1d3a57"))
        self.app = app
        self.url = ""
        self.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=360)
        self.label.grid(row=0, column=0, sticky="ew", padx=12, pady=8)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=0, column=1, padx=8)
        ctk.CTkButton(buttons, text="Video", width=70, command=lambda: self._download(False)).pack(side="left", padx=3)
        ctk.CTkButton(buttons, text="Audio", width=70, command=lambda: self._download(True)).pack(side="left", padx=3)
        self.formats_btn = ctk.CTkButton(buttons, text="Formats…", width=80, command=self._formats, **SECONDARY)
        self.formats_btn.pack(side="left", padx=3)
        ctk.CTkButton(buttons, text="✕", width=32, command=self.hide, **SECONDARY).pack(side="left", padx=(3, 0))

    def offer(self, url: str, how: str) -> None:
        self.url = url
        shown = url if len(url) <= 60 else url[:57] + "…"
        self.label.configure(text=f"{how}: {shown}")
        if is_playlist_url(url):
            self.formats_btn.pack_forget()
        elif not self.formats_btn.winfo_manager():  # re-add it before the ✕ button
            self.formats_btn.pack(side="left", padx=3, before=self.formats_btn.master.winfo_children()[-1])
        self.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 0))

    def hide(self) -> None:
        self.grid_forget()

    def _download(self, audio_only: bool) -> None:
        playlist = True if is_playlist_url(self.url) else None
        self.app.enqueue(self.app.make_options(self.url, audio_only=audio_only, playlist=playlist), self.url)
        self.hide()

    def _formats(self) -> None:
        FormatDialog(self.app, self.url)
        self.hide()
