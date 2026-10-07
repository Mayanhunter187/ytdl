from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.downloader import AUDIO_FORMATS, VIDEO_QUALITIES
from ytdl.ui.common import MUTED, SECONDARY
from ytdl.ui.format_dialog import FormatDialog

if TYPE_CHECKING:
    from ytdl.ui.app import App


class LinkTab:
    """Download from a pasted URL."""

    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        tab.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(tab)
        form.grid(row=0, column=0, sticky="ew")
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="URL").grid(row=0, column=0, sticky="w", padx=12, pady=(14, 6))
        self.url_var = ctk.StringVar()
        self.url_entry = ctk.CTkEntry(form, textvariable=self.url_var, placeholder_text="https://www.youtube.com/watch?v=…")
        self.url_entry.grid(row=0, column=1, sticky="ew", pady=(14, 6))
        self.url_entry.bind("<Return>", lambda _e: self.download())
        ctk.CTkButton(form, text="Paste", width=80, command=self._paste).grid(row=0, column=2, padx=12, pady=(14, 6))

        ctk.CTkLabel(form, text="Type").grid(row=1, column=0, sticky="w", padx=12, pady=6)
        options = ctk.CTkFrame(form, fg_color="transparent")
        options.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=6)
        ctk.CTkSegmentedButton(
            options, values=["Video", "Audio"], variable=app.mode_var, command=lambda _v: self._update_mode()
        ).pack(side="left")
        self.quality_label = ctk.CTkLabel(options, text="Quality")
        self.quality_label.pack(side="left", padx=(20, 8))
        # Same variables as the Settings tab, so the two stay in sync.
        self.quality_menu = ctk.CTkOptionMenu(options, values=VIDEO_QUALITIES, variable=app.quality_var, width=110)
        self.audio_menu = ctk.CTkOptionMenu(options, values=AUDIO_FORMATS, variable=app.audio_var, width=110)
        ctk.CTkSwitch(options, text="Whole playlist", variable=app.playlist_var).pack(side="right")

        self.dir_hint = ctk.CTkLabel(form, text="", anchor="w", justify="left", text_color=MUTED, wraplength=520)
        self.dir_hint.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(2, 0))
        app.dir_var.trace_add("write", lambda *_: self._update_hint())

        actions = ctk.CTkFrame(form, fg_color="transparent")
        actions.grid(row=3, column=1, sticky="w", pady=(10, 14))
        ctk.CTkButton(actions, text="Download", width=140, command=self.download).pack(side="left")
        ctk.CTkButton(actions, text="Choose format…", width=130, command=self.choose_format, **SECONDARY).pack(
            side="left", padx=8
        )

        ctk.CTkLabel(
            tab, anchor="w", justify="left", text_color=MUTED, wraplength=640,
            text="Tip: drag a link or a browser tab's address onto this window, or just copy a YouTube link. "
                 "YTDL offers to download it.",
        ).grid(row=1, column=0, sticky="ew", padx=4, pady=(10, 0))

        self._update_mode()
        self._update_hint()

    def _update_mode(self) -> None:
        audio = self.app.mode_var.get() == "Audio"
        self.quality_menu.pack_forget()
        self.audio_menu.pack_forget()
        self.quality_label.configure(text="Format" if audio else "Quality")
        (self.audio_menu if audio else self.quality_menu).pack(side="left", after=self.quality_label)

    def _update_hint(self) -> None:
        self.dir_hint.configure(text=f"Saving to {self.app.dir_var.get()}  ·  change it in Settings")

    def _paste(self) -> None:
        try:
            self.url_var.set(self.app.clipboard_get().strip())
        except Exception:
            pass

    def choose_format(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.url_entry.focus_set()
            return
        FormatDialog(self.app, url)
        self.url_var.set("")

    def download(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            self.url_entry.focus_set()
            return
        self.app.enqueue(self.app.make_options(url), url)
        self.url_var.set("")
        self.app.show_tab("Downloads")
