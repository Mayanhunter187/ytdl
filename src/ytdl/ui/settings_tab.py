from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.downloader import (
    AUDIO_FORMATS,
    COOKIE_BROWSERS,
    NAME_PRESETS,
    VIDEO_QUALITIES,
    preview_template,
    validate_template,
)
from ytdl.ui.common import MUTED, SECONDARY, open_path, section_label

if TYPE_CHECKING:
    from ytdl.ui.app import App

COOKIES_OFF = "Off"
COOKIES_FILE_OPTION = "cookies.txt file"
COOKIE_CHOICES = [COOKIES_OFF, *[b.title() for b in COOKIE_BROWSERS], COOKIES_FILE_OPTION]
CUSTOM_PRESET = "Custom"
ERROR_COLOR = ("#b3261e", "#f2867c")


class SettingsTab:
    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        body = self.body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(1, weight=1)
        self.r = 0

        # --- Downloads
        self._section("Downloads", top=4)
        self._label("Video quality")
        ctk.CTkOptionMenu(body, values=VIDEO_QUALITIES, variable=app.quality_var, width=140).grid(
            row=self.r, column=1, sticky="w"
        )
        self._next()
        self._label("Audio format")
        ctk.CTkOptionMenu(body, values=AUDIO_FORMATS, variable=app.audio_var, width=140).grid(
            row=self.r, column=1, sticky="w"
        )
        self._next()
        self._label("Save to")
        ctk.CTkEntry(body, textvariable=app.dir_var).grid(row=self.r, column=1, sticky="ew", pady=6)
        ctk.CTkButton(body, text="Browse…", width=80, command=self._browse_dir).grid(row=self.r, column=2, padx=(8, 0))
        ctk.CTkButton(body, text="Open", width=64, command=app.open_output_folder, **SECONDARY).grid(
            row=self.r, column=3, padx=(6, 4)
        )
        self._next()
        self._label("Simultaneous downloads")
        ctk.CTkSegmentedButton(body, values=["1", "2", "3", "4"], variable=app.concurrent_var).grid(
            row=self.r, column=1, sticky="w", pady=6
        )
        self._hint("How many items download at the same time. More than 2 rarely helps and YouTube may slow you down.")
        self._label("Skip duplicates")
        ctk.CTkSwitch(body, text="Skip videos already in History", variable=app.skip_var).grid(
            row=self.r, column=1, sticky="w", pady=6
        )
        self._hint("Videos whose file from an earlier download is still on disk are skipped, per type (video or "
                   "audio). Handy for re-downloading a playlist to pick up only its new videos.")

        # --- Files
        self._section("File names")
        self._label("Name files")
        self.preset_var = ctk.StringVar(value=self._preset_for(app.name_template_var.get()))
        ctk.CTkOptionMenu(
            body, values=[*NAME_PRESETS, CUSTOM_PRESET], variable=self.preset_var, width=200,
            command=self._apply_preset,
        ).grid(row=self.r, column=1, sticky="w", pady=6)
        self._next()
        self._label("Template")
        ctk.CTkEntry(body, textvariable=app.name_template_var, font=ctk.CTkFont(family="Consolas", size=12)).grid(
            row=self.r, column=1, columnspan=3, sticky="ew", padx=(0, 4), pady=6
        )
        self._next()
        self.preview = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=500)
        self.preview.grid(row=self.r, column=1, columnspan=3, sticky="w")
        self._hint("Uses yt-dlp's template fields, e.g. %(title)s, %(channel)s, %(id)s, %(upload_date>%Y-%m-%d)s. "
                   "A “/” creates a folder. Playlists always go in a folder named after the playlist.")
        app.name_template_var.trace_add("write", lambda *_: self._template_changed())
        self._template_changed()

        self._label("Chapters")
        ctk.CTkSwitch(body, text="Split videos with chapters into separate files", variable=app.split_var,
                      command=self._split_changed).grid(row=self.r, column=1, columnspan=3, sticky="w", pady=6)
        self._next()
        self.keep_full = ctk.CTkSwitch(body, text="Also keep the full, unsplit file", variable=app.keep_full_var)
        self.keep_full.grid(row=self.r, column=1, columnspan=3, sticky="w", pady=(0, 6))
        self._hint("Great for albums and long mixes: each chapter becomes its own track in a folder named after "
                   "the video. Videos without chapters download normally.")
        self._split_changed()

        # --- Sign-in
        self._section("Sign-in (age-restricted and members-only videos)")
        self._label("Use cookies from")
        ctk.CTkOptionMenu(body, values=COOKIE_CHOICES, variable=app.cookies_var, width=180,
                          command=lambda _v: self._cookies_changed()).grid(row=self.r, column=1, sticky="w", pady=6)
        self._next()
        self.cookie_file_row = ctk.CTkFrame(body, fg_color="transparent")
        self.cookie_file_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(self.cookie_file_row, textvariable=app.cookies_file_var,
                     placeholder_text="Path to a cookies.txt file").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(self.cookie_file_row, text="Browse…", width=80, command=self._browse_cookies).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.cookie_file_row_index = self.r
        self._hint("Lets yt-dlp use your YouTube login so it can download videos that need you to be signed in. "
                   "Firefox works best. Chrome and Edge must be fully closed, and recent versions encrypt cookies "
                   "in a way that may stop this from working; if so, export a cookies.txt file with a browser "
                   "extension instead. Cookies are only sent to YouTube.")
        self._cookies_changed()

        # --- Convenience
        self._section("Convenience")
        self._label("Clipboard")
        ctk.CTkSwitch(body, text="Offer to download YouTube links I copy", variable=app.clipboard_var).grid(
            row=self.r, column=1, columnspan=3, sticky="w", pady=6
        )
        self._hint("You can also drag a link (or a browser tab's address) onto the window.")
        self._label("Notifications")
        ctk.CTkSwitch(body, text="Show a Windows notification when downloads finish", variable=app.notify_var).grid(
            row=self.r, column=1, columnspan=3, sticky="w", pady=6
        )
        self._hint("Only shown while YTDL isn't the active window.")
        self._label("Theme")
        ctk.CTkSegmentedButton(
            body, values=["System", "Light", "Dark"], variable=app.theme_var,
            command=lambda v: ctk.set_appearance_mode(v.lower()),
        ).grid(row=self.r, column=1, sticky="w", pady=(6, 16))

    # -------------------------------------------------------------- layout helpers

    def _next(self) -> None:
        self.r += 1

    def _section(self, text: str, top: int = 18) -> None:
        section_label(self.body, text).grid(row=self.r, column=0, columnspan=4, sticky="w", pady=(top, 6))
        self._next()

    def _label(self, text: str) -> None:
        ctk.CTkLabel(self.body, text=text, anchor="w").grid(row=self.r, column=0, sticky="nw", padx=(4, 16), pady=10)

    def _hint(self, text: str) -> None:
        self._next()
        ctk.CTkLabel(self.body, text=text, anchor="w", justify="left", text_color=MUTED, wraplength=500).grid(
            row=self.r, column=1, columnspan=3, sticky="w", pady=(0, 6)
        )
        self._next()

    # -------------------------------------------------------------- behaviour

    @staticmethod
    def _preset_for(template: str) -> str:
        return next((name for name, t in NAME_PRESETS.items() if t == template), CUSTOM_PRESET)

    def _apply_preset(self, name: str) -> None:
        if name in NAME_PRESETS:
            self.app.name_template_var.set(NAME_PRESETS[name])

    def _template_changed(self) -> None:
        template = self.app.name_template_var.get()
        self.preset_var.set(self._preset_for(template))
        if error := validate_template(template):
            self.preview.configure(text=f"Invalid template: {error}", text_color=ERROR_COLOR)
        else:
            self.preview.configure(text=f"Example: {preview_template(template)}", text_color=("gray10", "gray90"))

    def _split_changed(self) -> None:
        self.keep_full.configure(state="normal" if self.app.split_var.get() else "disabled")

    def _cookies_changed(self) -> None:
        if self.app.cookies_var.get() == COOKIES_FILE_OPTION:
            self.cookie_file_row.grid(row=self.cookie_file_row_index, column=1, columnspan=3, sticky="ew", pady=(0, 6))
        else:
            self.cookie_file_row.grid_forget()

    def _browse_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.app.dir_var.get() or str(Path.home()), parent=self.app)
        if chosen:
            self.app.dir_var.set(str(Path(chosen)))

    def _browse_cookies(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self.app, title="Choose a cookies.txt file", filetypes=[("Cookies file", "*.txt"), ("All files", "*.*")]
        )
        if chosen:
            self.app.cookies_file_var.set(str(Path(chosen)))
