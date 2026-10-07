"""Pick and name the songs to cut an album / mix video into."""

from __future__ import annotations

import webbrowser
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from ytdl.downloader import DownloadOptions
from ytdl.tracks import Track, TrackScan, fix_names, fmt_time, safe_filename
from ytdl.ui.common import CARD, MUTED, SECONDARY, resource_path

if TYPE_CHECKING:
    from ytdl.ui.app import App

SOURCES = {
    "chapters": "the video's chapters",
    "description": "the video's description",
    "a comment": "a tracklist in the comments",
}


class TrackRow:
    def __init__(self, master, index: int, track: Track, url: str, on_toggle: Callable[[], None]) -> None:
        self.track = track
        self.frame = ctk.CTkFrame(master, fg_color=CARD)
        self.frame.grid_columnconfigure(3, weight=1)
        self.selected = ctk.BooleanVar(value=track.selected)
        ctk.CTkCheckBox(self.frame, text="", width=24, variable=self.selected, command=on_toggle).grid(
            row=0, column=0, padx=(10, 0), pady=6
        )
        ctk.CTkLabel(self.frame, text=f"{index:02d}", width=26, text_color=MUTED).grid(row=0, column=1)
        times = ctk.CTkLabel(
            self.frame, text=f"{fmt_time(track.start)} – {fmt_time(track.end)}  ({fmt_time(track.length)})",
            width=150, anchor="w", text_color=("#1f6aa5", "#5aa9e6"), cursor="hand2",
        )
        times.grid(row=0, column=2, padx=(6, 8))
        joiner = "&" if "?" in url else "?"
        times.bind("<Button-1>", lambda _e: webbrowser.open(f"{url}{joiner}t={int(track.start)}s"))
        self.name = ctk.StringVar(value=track.name)
        ctk.CTkEntry(self.frame, textvariable=self.name).grid(row=0, column=3, sticky="ew", padx=(0, 10), pady=6)

    def reset_name(self) -> None:
        self.name.set(self.track.name)


class TrackDialog(ctk.CTkToplevel):
    """Shown when an audio download turns out to hold several songs."""

    def __init__(self, app: App, opts: DownloadOptions, label: str, scan: TrackScan,
                 on_done: Callable[[bool], None] | None = None) -> None:
        super().__init__(app)
        self.app, self.opts, self.label, self.scan = app, opts, label, scan
        self.on_done = on_done
        self.title("Songs in this video")
        self.geometry(f"840x700+{app.winfo_rootx() + 40}+{app.winfo_rooty() + 30}")
        self.minsize(740, 460)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        icon = resource_path("icon.ico")
        if icon.exists():
            self.after(250, lambda: self.iconbitmap(str(icon)))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        ctk.CTkLabel(self, text=scan.title, anchor="w", justify="left", wraplength=600,
                     font=ctk.CTkFont(size=16, weight="bold")).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 0))
        ctk.CTkLabel(
            self, anchor="w", justify="left", wraplength=600, text_color=MUTED,
            text=f"Found {len(scan.tracks)} songs in {SOURCES.get(scan.source, scan.source)}. Untick anything you "
                 "don't want and edit the names if you like. Click a time to hear that spot on YouTube.",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 8))

        # --- album / artist
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=2, column=0, sticky="ew", padx=16)
        meta.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(meta, text="Album / folder").grid(row=0, column=0, padx=(0, 8))
        self.album = ctk.StringVar(value=scan.album)
        ctk.CTkEntry(meta, textvariable=self.album).grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(meta, text="Artist").grid(row=0, column=2, padx=(16, 8))
        self.artist = ctk.StringVar(value=scan.artist)
        ctk.CTkEntry(meta, textvariable=self.artist).grid(row=0, column=3, sticky="ew")
        self.album.trace_add("write", lambda *_: self._update_summary())

        # --- options
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 6))
        self.number_files = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(bar, text="Number the files (01 - …)", variable=self.number_files,
                      command=self._update_summary).pack(side="left")
        self.artist_titles = ctk.BooleanVar(value=scan.artist_titles)
        ctk.CTkSwitch(bar, text="Names are “Artist - Title”", variable=self.artist_titles).pack(side="left", padx=16)
        ctk.CTkButton(bar, text="Reset names", width=100, command=self._reset_names, **SECONDARY).pack(side="right")
        ctk.CTkButton(bar, text="None", width=56, command=lambda: self._select(False), **SECONDARY).pack(
            side="right", padx=6)
        ctk.CTkButton(bar, text="All", width=48, command=lambda: self._select(True), **SECONDARY).pack(side="right")

        # --- tracks
        listing = ctk.CTkScrollableFrame(self, fg_color="transparent")
        listing.grid(row=4, column=0, sticky="nsew", padx=10)
        listing.grid_columnconfigure(0, weight=1)
        self.rows = []
        for i, track in enumerate(scan.tracks, start=1):
            row = TrackRow(listing, i, track, scan.url, self._update_summary)
            row.frame.grid(row=i, column=0, sticky="ew", pady=(0, 4))
            self.rows.append(row)

        # --- footer
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=5, column=0, sticky="ew", padx=16, pady=(8, 14))
        footer.grid_columnconfigure(0, weight=1)
        self.summary = ctk.CTkLabel(footer, text="", anchor="w", justify="left", wraplength=420, text_color=MUTED)
        self.summary.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(footer, text="Keep as one file", width=130, command=self._one_file, **SECONDARY).grid(
            row=0, column=1, padx=(8, 0))
        ctk.CTkButton(footer, text="Cancel", width=80, command=self._cancel, **SECONDARY).grid(row=0, column=2, padx=8)
        self.download_btn = ctk.CTkButton(footer, text="", width=150, command=self._download)
        self.download_btn.grid(row=0, column=3)

        self._update_summary()
        self.after(100, self.focus_force)

    # -------------------------------------------------------------- actions

    def _selected(self) -> list[TrackRow]:
        return [row for row in self.rows if row.selected.get()]

    def _select(self, value: bool) -> None:
        for row in self.rows:
            row.selected.set(value)
        self._update_summary()

    def _reset_names(self) -> None:
        for row in self.rows:
            row.reset_name()

    def _update_summary(self) -> None:
        count = len(self._selected())
        folder = safe_filename(self.album.get().strip() or self.scan.title)
        self.summary.configure(
            text=f"{count} of {len(self.rows)} songs  ·  saved as {self.opts.audio_format.upper()} files in "
                 f"{self.opts.output_dir.name}\\{folder}"
        )
        self.download_btn.configure(text=f"Download {count} song{'s' if count != 1 else ''}",
                                    state="normal" if count else "disabled")

    def _finish(self, added: bool) -> None:
        if self.on_done:
            self.on_done(added)
        self.destroy()

    def _cancel(self) -> None:
        self._finish(False)

    def _one_file(self) -> None:
        self.app.enqueue(self.opts, self.label)
        self._finish(True)

    def _download(self) -> None:
        rows = self._selected()
        if not rows:
            return
        # A cleared name falls back to the detected one; duplicates get "(2)".
        picked = [Track(r.track.start, r.track.end, r.track.raw, r.name.get().strip() or r.track.name) for r in rows]
        fix_names(picked)
        opts = self.opts
        opts.tracks = [{"start": t.start, "end": t.end, "name": t.name} for t in picked]
        opts.album = self.album.get().strip() or self.scan.album or self.scan.title
        opts.artist = self.artist.get().strip()
        opts.number_tracks = self.number_files.get()
        opts.artist_titles = self.artist_titles.get()
        opts.keep_full = self.app.keep_full_var.get()
        self.app.enqueue(opts, f"{opts.album} ({len(picked)} songs)")
        self.app.show_tab("Downloads")
        self._finish(True)
