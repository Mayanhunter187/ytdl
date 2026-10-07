"""Pick an exact video/audio format before downloading."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.formats import FormatChoice, FormatList, list_formats
from ytdl.ui.common import CARD, MUTED, fmt_size, resource_path, section_label
from ytdl.downloader import friendly_error

if TYPE_CHECKING:
    from ytdl.ui.app import App


class FormatDialog(ctk.CTkToplevel):
    def __init__(self, app: App, url: str, label: str = "") -> None:
        super().__init__(app)
        self.app = app
        self.url = url
        self.title("Choose a format")
        self.geometry(f"640x620+{app.winfo_rootx() + 140}+{app.winfo_rooty() + 60}")
        self.minsize(520, 400)
        self.transient(app)
        icon = resource_path("icon.ico")
        if icon.exists():
            # CTkToplevel sets its own icon shortly after creation.
            self.after(250, lambda: self.iconbitmap(str(icon)))

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.heading = ctk.CTkLabel(
            self, text=label or url, anchor="w", justify="left", wraplength=600, font=ctk.CTkFont(size=15, weight="bold")
        )
        self.heading.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        self.note = ctk.CTkLabel(self, text="Loading formats…", anchor="w", justify="left", text_color=MUTED,
                                 wraplength=600)
        self.note.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 6))
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 12))
        self.body.grid_columnconfigure(0, weight=1)

        # Read Tk-backed settings here; the worker thread must not touch Tk.
        cookies = (app.cookies_browser(), app.cookies_file())
        threading.Thread(target=self._load, args=cookies, daemon=True).start()
        self.after(100, self.focus_force)

    def _load(self, cookies_browser: str, cookies_file: str) -> None:
        try:
            result = list_formats(self.url, cookies_browser, cookies_file)
            self.app.call_soon(self._show, result)
        except Exception as exc:
            self.app.call_soon(self._fail, exc)

    def _fail(self, exc: Exception) -> None:
        if self.winfo_exists():
            self.note.configure(text=f"Couldn't load formats: {friendly_error(str(exc)).splitlines()[0]}")

    def _show(self, formats: FormatList) -> None:
        if not self.winfo_exists():
            return
        self.heading.configure(text=formats.title)
        self.note.configure(
            text=f"Sizes are estimates. Video is saved as MP4. Audio is converted to "
                 f"{self.app.audio_var.get().upper()} (change in Settings)."
        )
        row = 0
        for heading, choices in (("Video", formats.video), ("Audio only", formats.audio)):
            if not choices:
                continue
            section_label(self.body, heading).grid(row=row, column=0, sticky="w", padx=6, pady=(8, 4))
            row += 1
            for choice in choices:
                self._choice_row(choice).grid(row=row, column=0, sticky="ew", pady=(0, 4))
                row += 1

    def _choice_row(self, choice: FormatChoice) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.body, fg_color=CARD)
        frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(frame, text=choice.label, width=90, anchor="w", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=(12, 6), pady=8
        )
        ctk.CTkLabel(frame, text=choice.detail, anchor="w", text_color=MUTED).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(frame, text=f"~{fmt_size(choice.size)}" if choice.size else "", width=80, anchor="e").grid(
            row=0, column=2, padx=6
        )
        ctk.CTkButton(frame, text="Download", width=90, command=lambda: self._pick(choice)).grid(
            row=0, column=3, padx=(6, 10)
        )
        return frame

    def _pick(self, choice: FormatChoice) -> None:
        opts = self.app.make_options(self.url, audio_only=choice.audio_only, playlist=False)
        opts.format_spec = choice.spec
        opts.format_label = f"{choice.label} {choice.detail.split('  ·  ')[0]}".strip()
        self.app.enqueue(opts, self.heading.cget("text"))
        self.app.show_tab("Downloads")
        self.destroy()
