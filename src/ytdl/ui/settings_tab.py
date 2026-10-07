from __future__ import annotations

import threading
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
    test_cookies,
    validate_template,
)
from ytdl.ui.common import MUTED, SECONDARY, open_path, section_label

if TYPE_CHECKING:
    from ytdl.ui.app import App

COOKIES_OFF = "Off"
COOKIES_FILE_OPTION = "cookies.txt file"
COOKIE_CHOICES = [COOKIES_OFF, *[b.title() for b in COOKIE_BROWSERS], COOKIES_FILE_OPTION]
CUSTOM_PRESET = "Custom"
MULTI_SONG_ASK, MULTI_SONG_AUTO, MULTI_SONG_OFF = "Ask me", "Split automatically", "Keep as one file"
MULTI_SONG_CHOICES = [MULTI_SONG_ASK, MULTI_SONG_AUTO, MULTI_SONG_OFF]
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

        # --- Music
        self._section("Music")
        self._label("Sort songs")
        ctk.CTkSwitch(body, text="Put audio downloads in Artist / Album folders", variable=app.organize_var,
                      command=self._organize_changed).grid(row=self.r, column=1, columnspan=3, sticky="w", pady=6)
        self._next()
        self.lookup = ctk.CTkSwitch(body, text="Look up missing album names online (MusicBrainz)",
                                    variable=app.lookup_var)
        self.lookup.grid(row=self.r, column=1, columnspan=3, sticky="w", pady=(0, 4))
        self._next()
        self.ask_unsorted = ctk.CTkSwitch(body, text="Ask me when the artist or album can't be found",
                                          variable=app.ask_unsorted_var)
        self.ask_unsorted.grid(row=self.r, column=1, columnspan=3, sticky="w", pady=(0, 6))
        self._hint("Songs land in Downloads\\YTDL\\Pantera\\Vulgar Display of Power\\Walk.mp3, with matching "
                   "tags. The artist and album come from YouTube when it knows them, otherwise from the video "
                   "title and MusicBrainz, a free music database (only the artist and song name are sent). "
                   "If something can't be found you're asked; with asking off, the song goes to the artist's "
                   "Singles folder. Playlists are sorted per song without asking. Videos and non-music audio "
                   "use the file name template above.")
        self._organize_changed()
        self._label("Several songs")
        ctk.CTkSegmentedButton(body, values=MULTI_SONG_CHOICES, variable=app.multi_song_var,
                               command=lambda _v: self._multi_song_changed()).grid(
            row=self.r, column=1, columnspan=3, sticky="w", pady=6)
        self._next()
        self.keep_full = ctk.CTkSwitch(body, text="Also keep the full, unsplit file", variable=app.keep_full_var)
        self.keep_full.grid(row=self.r, column=1, columnspan=3, sticky="w", pady=(0, 6))
        self._hint("When you download the audio of a long video that holds several songs (an album, a mix), YTDL "
                   "finds them in the chapters, the description or a tracklist comment. “Ask me” opens a list "
                   "where you pick and name the songs; “Split automatically” uses the detected names. Each song "
                   "is saved as its own tagged file in a folder named after the album. Playlists and video "
                   "downloads are never split.")
        self._multi_song_changed()

        # --- Sign-in
        self._section("Sign-in (age-restricted and members-only videos)")
        self._label("Use cookies from")
        ctk.CTkOptionMenu(body, values=COOKIE_CHOICES, variable=app.cookies_var, width=180,
                          command=lambda _v: self._cookies_changed()).grid(row=self.r, column=1, sticky="w", pady=6)
        self.test_btn = ctk.CTkButton(body, text="Test", width=80, command=self._test_cookies, **SECONDARY)
        self.test_btn.grid(row=self.r, column=2, padx=(8, 0))
        self._next()
        self.cookie_result = ctk.CTkLabel(body, text="", anchor="w", justify="left", wraplength=500)
        self.cookie_result.grid(row=self.r, column=1, columnspan=3, sticky="w")
        self._next()
        self.cookie_file_row = ctk.CTkFrame(body, fg_color="transparent")
        self.cookie_file_row.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(self.cookie_file_row, textvariable=app.cookies_file_var,
                     placeholder_text="Path to a cookies.txt file").grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(self.cookie_file_row, text="Browse…", width=80, command=self._browse_cookies).grid(
            row=0, column=1, padx=(8, 0)
        )
        self.cookie_file_row_index = self.r
        self._hint("Only used when YouTube says a video needs a signed-in account; everything else downloads "
                   "without them. Firefox works best. Chrome, Edge and other Chromium browsers lock and encrypt "
                   "their cookies, so they usually only work while the browser is fully closed, and sometimes not "
                   "at all. A cookies.txt file (exported with a browser extension) always works. Cookies are only "
                   "sent to YouTube. Press Test to check.")
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

    def _organize_changed(self) -> None:
        state = "normal" if self.app.organize_var.get() else "disabled"
        self.lookup.configure(state=state)
        self.ask_unsorted.configure(state=state)

    def _multi_song_changed(self) -> None:
        splitting = self.app.multi_song_var.get() != MULTI_SONG_OFF
        self.keep_full.configure(state="normal" if splitting else "disabled")

    def _test_cookies(self) -> None:
        browser, file = self.app.cookies_browser(), self.app.cookies_file()
        self.test_btn.configure(state="disabled")
        self.cookie_result.configure(text="Checking…", text_color=MUTED)

        def work() -> None:
            message = test_cookies(browser, file)
            self.app.call_soon(self._show_cookie_result, message)

        threading.Thread(target=work, daemon=True).start()

    def _show_cookie_result(self, message: str) -> None:
        self.test_btn.configure(state="normal")
        color = ERROR_COLOR if message.startswith("✗") else ("#2f7d32", "#6cc070") if message.startswith("✓") \
            else ("gray10", "gray90")
        self.cookie_result.configure(text=message, text_color=color)

    def _cookies_changed(self) -> None:
        self.cookie_result.configure(text="")
        self.test_btn.configure(state="disabled" if self.app.cookies_var.get() == COOKIES_OFF else "normal")
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
