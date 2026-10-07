"""Ask where a song goes when its artist or album couldn't be worked out."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from ytdl.downloader import DownloadOptions
from ytdl.music import SINGLES, AlbumMatch, Song, find_albums
from ytdl.tracks import safe_filename
from ytdl.ui.common import MUTED, SECONDARY, resource_path

if TYPE_CHECKING:
    from ytdl.ui.app import App

WARNING_BG = ("#fdf3d7", "#3a3220")
WARNING_TEXT = ("#7a5200", "#e3b341")
ERROR_COLOR = ("#b3261e", "#f2867c")


def _label(match: AlbumMatch) -> str:
    details = ", ".join(x for x in (match.year, match.kind if match.kind != "Album" else "") if x)
    return f"{match.title} ({details})" if details else match.title


class SongDialog(ctk.CTkToplevel):
    def __init__(self, app: App, opts: DownloadOptions, label: str, song: Song,
                 on_done: Callable[[bool], None] | None = None) -> None:
        super().__init__(app)
        self.app, self.opts, self.label, self.song, self.on_done = app, opts, label, song, on_done
        self.options: dict[str, str] = {}  # combobox text -> album title
        self.title("Where should this song go?")
        self.geometry(f"640x600+{app.winfo_rootx() + 140}+{app.winfo_rooty() + 60}")
        self.minsize(640, 560)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        icon = resource_path("icon.ico")
        if icon.exists():
            self.after(250, lambda: self.iconbitmap(str(icon)))
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Where should this song go?", anchor="w",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        ctk.CTkLabel(self, text=label, anchor="w", justify="left", wraplength=590, text_color=MUTED).grid(
            row=1, column=0, sticky="ew", padx=18)

        # --- what's missing, and how to fix it
        box = ctk.CTkFrame(self, fg_color=WARNING_BG)
        box.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 4))
        ctk.CTkLabel(box, text=self._problem_text(), anchor="w", justify="left", wraplength=570,
                     text_color=WARNING_TEXT).pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(
            box, anchor="w", justify="left", wraplength=570, text_color=WARNING_TEXT,
            text="You can:\n"
                 "•  correct the artist or song name below and click Find album\n"
                 "•  pick an album from the list, or type one yourself\n"
                 "•  put it in the artist's Singles folder, or skip sorting for this one",
        ).pack(fill="x", padx=12, pady=(0, 10))

        # --- form
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.grid(row=3, column=0, sticky="ew", padx=18, pady=(8, 0))
        form.grid_columnconfigure(1, weight=1)
        self.artist = ctk.StringVar(value=song.artist if "artist_guess" not in song.problems else "")
        self.song_title = ctk.StringVar(value=song.title)
        self.album = ctk.StringVar(value=song.album)
        for row, (text, var) in enumerate((("Artist", self.artist), ("Song", self.song_title))):
            ctk.CTkLabel(form, text=text, width=60, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            entry = ctk.CTkEntry(form, textvariable=var)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        if "artist_guess" in song.problems:
            entry_hint = f"Maybe “{song.artist}”? That's the uploader's channel name."
            ctk.CTkLabel(form, text=entry_hint, anchor="w", text_color=MUTED).grid(
                row=2, column=1, columnspan=2, sticky="w")
        ctk.CTkLabel(form, text="Album", width=60, anchor="w").grid(row=3, column=0, sticky="w", pady=4)
        self.album_box = ctk.CTkComboBox(form, variable=self.album, values=[""], command=self._album_picked)
        self.album_box.grid(row=3, column=1, sticky="ew", pady=4)
        self.find_btn = ctk.CTkButton(form, text="Find album", width=100, command=self._find, **SECONDARY)
        self.find_btn.grid(row=3, column=2, padx=(8, 0))
        self.status = ctk.CTkLabel(form, text="", anchor="w", justify="left", wraplength=480, text_color=MUTED)
        self.status.grid(row=4, column=1, columnspan=2, sticky="w")
        self._set_options(song.album_options)

        self.preview = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=590)
        self.preview.grid(row=4, column=0, sticky="ew", padx=18, pady=(8, 0))
        for var in (self.artist, self.song_title, self.album):
            var.trace_add("write", lambda *_: self._update_preview())

        self.dont_ask = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(self, text="Don't ask next time: put songs with an unknown album in Singles",
                        variable=self.dont_ask).grid(row=5, column=0, sticky="w", padx=18, pady=(12, 0))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=6, column=0, sticky="e", padx=16, pady=16)
        ctk.CTkButton(buttons, text="Don't sort", width=100, command=self._unsorted, **SECONDARY).pack(side="left")
        ctk.CTkButton(buttons, text="Put in Singles", width=120, command=self._singles, **SECONDARY).pack(
            side="left", padx=8)
        ctk.CTkButton(buttons, text="Cancel", width=80, command=self._cancel, **SECONDARY).pack(side="left")
        ctk.CTkButton(buttons, text="Download", width=110, command=self._download).pack(side="left", padx=(8, 0))

        self._update_preview()
        self.after(100, self.focus_force)

    # -------------------------------------------------------------- text

    def _problem_text(self) -> str:
        song, lines = self.song, []
        if "artist" in song.problems:
            lines.append("Couldn't tell who the artist is. The video title doesn't say.")
        elif "artist_guess" in song.problems:
            lines.append(f"Couldn't tell who the artist is. “{song.artist}” is the uploader's channel, which may "
                         "not be the artist.")
        if "album" in song.problems:
            name = f"“{song.title}”" if song.title else "this song"
            if not self.opts.lookup_albums:
                lines.append(f"Don't know which album {name} is on (online album lookup is off in Settings).")
            elif song.album_options:
                lines.append(f"Couldn't find a studio album with {name}, only the releases in the Album list.")
            else:
                lines.append(f"Couldn't find which album {name} is on.")
        return "\n".join(lines) or "Please check the details below."

    def _set_options(self, matches: list[AlbumMatch]) -> None:
        self.options = {_label(m): m.title for m in matches}
        self.album_box.configure(values=list(self.options) or [""])
        if matches:
            self.status.configure(text=f"{len(matches)} release{'s' if len(matches) != 1 else ''} found. "
                                       "Open the list to pick one.", text_color=MUTED)

    def _album_picked(self, choice: str) -> None:
        self.album.set(self.options.get(choice, choice))

    def _target(self, album: str) -> str:
        artist, title = self.artist.get().strip(), self.song_title.get().strip()
        if not artist:
            return ""
        return "\\".join((self.opts.output_dir.name, safe_filename(artist), safe_filename(album or SINGLES),
                          f"{safe_filename(title or 'song')}.{self.opts.audio_format}"))

    def _update_preview(self) -> None:
        target = self._target(self.album.get().strip())
        self.preview.configure(text=f"Saves to: {target}" if target else "Enter the artist to see where it goes.")

    # -------------------------------------------------------------- actions

    def _find(self) -> None:
        artist, title = self.artist.get().strip(), self.song_title.get().strip()
        if not (artist and title):
            self.status.configure(text="Enter both the artist and the song name first.", text_color=ERROR_COLOR)
            return
        self.find_btn.configure(state="disabled", text="Searching…")

        def work() -> None:
            try:
                matches, error = find_albums(artist, title), ""
            except Exception as exc:
                matches, error = [], str(exc)
            self.app.call_soon(self._found, matches, error)

        threading.Thread(target=work, daemon=True).start()

    def _found(self, matches: list[AlbumMatch], error: str) -> None:
        if not self.winfo_exists():
            return
        self.find_btn.configure(state="normal", text="Find album")
        if error:
            self.status.configure(text=f"Couldn't reach MusicBrainz: {error}", text_color=ERROR_COLOR)
            return
        if not matches:
            self.status.configure(text="Nothing found for that artist and song. Check the spelling, type the "
                                       "album yourself, or use Singles.", text_color=ERROR_COLOR)
            return
        self._set_options(matches)
        self.album.set(matches[0].title)

    def _finish(self, added: bool) -> None:
        if self.dont_ask.get():
            self.app.ask_unsorted_var.set(False)
        if self.on_done:
            self.on_done(added)
        self.destroy()

    def _cancel(self) -> None:
        self._finish(False)

    def _queue(self, album: str) -> None:
        artist, title = self.artist.get().strip(), self.song_title.get().strip()
        if not artist:
            self.status.configure(text="Enter the artist first, or choose Don't sort.", text_color=ERROR_COLOR)
            return
        self.opts.song = {"artist": artist, "album": album, "title": title or self.song.title, "year": ""}
        if album == self.song.album:
            self.opts.song["year"] = self.song.year
        self.app.queue_download(self.opts, f"{artist} - {self.opts.song['title']}", self.on_done)
        self.on_done = None  # already reported
        self._finish(True)

    def _download(self) -> None:
        album = self.album.get().strip()
        album = self.options.get(album, album)
        if not album:
            self.status.configure(text="Pick or type an album, or choose Put in Singles.", text_color=ERROR_COLOR)
            return
        self._queue(album)

    def _singles(self) -> None:
        self._queue(SINGLES)

    def _unsorted(self) -> None:
        self.opts.organize_music = False
        self.opts.song = {}
        self.app.queue_download(self.opts, self.label, self.on_done)
        self.on_done = None
        self._finish(True)
