from __future__ import annotations

import time
from pathlib import Path
from tkinter import messagebox
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.history import HistoryEntry
from ytdl.ui.common import CARD, MUTED, SECONDARY, fmt_size, open_path, reveal

if TYPE_CHECKING:
    from ytdl.ui.app import App

MAX_ROWS = 200


class HistoryRow(ctk.CTkFrame):
    def __init__(self, master, entry: HistoryEntry, tab: HistoryTab) -> None:
        super().__init__(master, fg_color=CARD)
        self.grid_columnconfigure(0, weight=1)
        exists = entry.exists

        ctk.CTkLabel(
            self, text=entry.title or Path(entry.path).name, anchor="w", justify="left", wraplength=520,
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.timestamp))
        path = Path(entry.path)
        kind = "chapter tracks" if path.is_dir() else path.suffix.lstrip(".").upper()
        meta = f"{entry.mode.title()}  ·  {kind}  ·  {fmt_size(entry.size)}  ·  {when}"
        if not exists:
            meta += "  ·  file missing"
        ctk.CTkLabel(self, text=meta, anchor="w", text_color=MUTED if exists else ("#b3261e", "#f2867c")).grid(
            row=1, column=0, sticky="ew", padx=12, pady=(0, 10)
        )

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=0, column=1, rowspan=2, padx=10)
        if exists:
            ctk.CTkButton(actions, text="Open", width=64, command=lambda: open_path(Path(entry.path))).pack(
                side="left", padx=2
            )
            ctk.CTkButton(actions, text="Folder", width=64, command=lambda: reveal(Path(entry.path)), **SECONDARY).pack(
                side="left", padx=2
            )
        elif entry.url:
            ctk.CTkButton(actions, text="Download again", width=120, command=lambda: tab.redownload(entry)).pack(
                side="left", padx=2
            )
        ctk.CTkButton(actions, text="✕", width=32, command=lambda: tab.remove(entry), **SECONDARY).pack(
            side="left", padx=2
        )


class HistoryTab:
    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        self._pending_refresh: str | None = None
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        self.filter_var = ctk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._schedule_refresh())
        ctk.CTkEntry(bar, textvariable=self.filter_var, placeholder_text="Filter by title").grid(
            row=0, column=0, sticky="ew"
        )
        ctk.CTkButton(bar, text="Clear history", width=110, command=self.clear, **SECONDARY).grid(
            row=0, column=1, padx=(8, 0)
        )

        self.info = ctk.CTkLabel(tab, text="", anchor="w", text_color=MUTED)
        self.info.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.list.grid(row=2, column=0, sticky="nsew")
        self.list.grid_columnconfigure(0, weight=1)

        self.refresh()

    def _schedule_refresh(self) -> None:
        if self._pending_refresh:
            self.app.after_cancel(self._pending_refresh)
        self._pending_refresh = self.app.after(250, self.refresh)

    def refresh(self) -> None:
        self._pending_refresh = None
        for child in self.list.winfo_children():
            child.destroy()
        needle = self.filter_var.get().strip().lower()
        entries = [e for e in self.app.history.newest_first() if needle in e.title.lower()]
        for index, entry in enumerate(entries[:MAX_ROWS]):
            HistoryRow(self.list, entry, self).grid(row=index, column=0, sticky="ew", pady=(0, 6))

        total = len(self.app.history.entries)
        if not total:
            text = "No downloads yet. Finished downloads show up here."
        elif needle:
            text = f"{len(entries)} of {total} downloads match"
        else:
            text = f"{total} download{'s' if total != 1 else ''}"
        if len(entries) > MAX_ROWS:
            text += f"  ·  showing the newest {MAX_ROWS}"
        if self.app.skip_var.get():
            text += "  ·  videos listed here are skipped if downloaded again (change in Settings)"
        self.info.configure(text=text)

    def remove(self, entry: HistoryEntry) -> None:
        self.app.history.remove(entry)
        self.refresh()

    def clear(self) -> None:
        if self.app.history.entries and messagebox.askyesno(
            "Clear history", "Remove every entry from the download history?\nYour downloaded files are not deleted.",
            parent=self.app,
        ):
            self.app.history.clear()
            self.refresh()

    def redownload(self, entry: HistoryEntry) -> None:
        opts = self.app.make_options(entry.url, audio_only=entry.mode == "audio", playlist=False)
        self.app.enqueue(opts, entry.title)
        self.app.show_tab("Downloads")
