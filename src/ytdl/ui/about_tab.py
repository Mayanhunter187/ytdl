from __future__ import annotations

import platform
import subprocess
import threading
import webbrowser
from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl import __version__, updater
from ytdl.downloader import find_deno, find_ffmpeg
from ytdl.updater import DATA_DIR, RELEASES_PAGE
from ytdl.ui.common import MUTED, NO_WINDOW, SECONDARY, open_path, section_label

if TYPE_CHECKING:
    from ytdl.ui.app import App

REPO_URL = "https://github.com/Mayanhunter187/ytdl"


def _tool_version(exe: str | None, flag: str, word: int) -> str:
    if not exe:
        return "not found"
    try:
        out = subprocess.run(
            [exe, flag], capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW
        ).stdout.splitlines()
        return out[0].split()[word] if out else "unknown"
    except Exception:
        return "unknown"


class AboutTab:
    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        body = self.body = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew")
        body.grid_columnconfigure(1, weight=1)
        self.r = 0

        ctk.CTkLabel(body, text=f"YTDL {__version__}", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=self._row(), column=0, columnspan=3, sticky="w", padx=4
        )
        link = ctk.CTkLabel(body, text=REPO_URL, text_color=("#1f6aa5", "#5aa9e6"), cursor="hand2")
        link.grid(row=self._row(), column=0, columnspan=3, sticky="w", padx=4)
        link.bind("<Button-1>", lambda _e: webbrowser.open(REPO_URL))

        # --- updates
        section_label(body, "Updates").grid(row=self._row(), column=0, columnspan=3, sticky="w", pady=(18, 4), padx=4)
        self.app_status = self._status_line("YTDL")
        self.app_buttons = self._button_row()
        self.app_install_btn = ctk.CTkButton(self.app_buttons, text="Update now", width=120,
                                             command=app.install_app_update)
        self.app_notes_btn = ctk.CTkButton(self.app_buttons, text="What's new", width=110, **SECONDARY,
                                           command=lambda: webbrowser.open(
                                               app.app_release.page_url if app.app_release else RELEASES_PAGE))

        self.ytdlp_status = self._status_line("yt-dlp")
        self.ytdlp_buttons = self._button_row()
        self.install_btn = ctk.CTkButton(self.ytdlp_buttons, text="Install update", width=130,
                                         command=app.install_update)
        self.restart_btn = ctk.CTkButton(self.ytdlp_buttons, text="Restart now", width=120, command=app.restart)

        controls = ctk.CTkFrame(body, fg_color="transparent")
        controls.grid(row=self._row(), column=0, columnspan=3, sticky="w", padx=4, pady=(10, 0))
        self.check_btn = ctk.CTkButton(controls, text="Check for updates", width=150,
                                       command=lambda: app.check_for_updates(manual=True))
        self.check_btn.pack(side="left")
        ctk.CTkSwitch(body, text="Check for updates on startup and install yt-dlp updates automatically",
                      variable=app.auto_update_var).grid(row=self._row(), column=0, columnspan=3, sticky="w",
                                                         padx=4, pady=(10, 0))
        ctk.CTkLabel(
            body, anchor="w", justify="left", text_color=MUTED, wraplength=620,
            text="YouTube changes often and yt-dlp ships fixes within days. yt-dlp updates are small, come straight "
                 "from PyPI and apply on the next start. New YTDL versions come from this project's GitHub "
                 "Releases; Update now downloads it, checks its checksum and restarts into it.",
        ).grid(row=self._row(), column=0, columnspan=3, sticky="w", padx=4, pady=(8, 0))

        # --- components
        section_label(body, "Components").grid(row=self._row(), column=0, columnspan=3, sticky="w", pady=(18, 4),
                                               padx=4)
        self.values: dict[str, ctk.CTkLabel] = {}
        for name in ["yt-dlp", "yt-dlp-ejs", "ffmpeg", "deno", "Python"]:
            r = self._row()
            ctk.CTkLabel(body, text=name, anchor="w", width=110).grid(row=r, column=0, sticky="w", padx=4, pady=2)
            value = ctk.CTkLabel(body, text="…", anchor="w")
            value.grid(row=r, column=1, sticky="w", pady=2)
            self.values[name] = value
        self.values["Python"].configure(text=platform.python_version())
        threading.Thread(target=self._probe_tools, daemon=True).start()

        # --- data
        section_label(body, "Data").grid(row=self._row(), column=0, columnspan=3, sticky="w", pady=(18, 4), padx=4)
        r = self._row()
        ctk.CTkLabel(body, text=str(DATA_DIR), anchor="w", text_color=MUTED).grid(
            row=r, column=0, columnspan=2, sticky="w", padx=4
        )
        ctk.CTkButton(
            body, text="Open", width=64, **SECONDARY,
            command=lambda: (DATA_DIR.mkdir(parents=True, exist_ok=True), open_path(DATA_DIR)),
        ).grid(row=r, column=2, sticky="e", padx=4)
        ctk.CTkLabel(
            body, anchor="w", justify="left", text_color=MUTED, wraplength=620,
            text="Settings, download history, the download queue and yt-dlp updates are stored here.\n\n"
                 "Only download content you have the right to download.",
        ).grid(row=self._row(), column=0, columnspan=3, sticky="w", padx=4, pady=(4, 8))

        self.refresh()

    def _row(self) -> int:
        self.r += 1
        return self.r

    def _status_line(self, name: str) -> ctk.CTkLabel:
        r = self._row()
        ctk.CTkLabel(self.body, text=name, anchor="w", width=110, font=ctk.CTkFont(weight="bold")).grid(
            row=r, column=0, sticky="nw", padx=4, pady=(6, 0)
        )
        label = ctk.CTkLabel(self.body, text="", anchor="w", justify="left", wraplength=500)
        label.grid(row=r, column=1, columnspan=2, sticky="w", pady=(6, 0))
        return label

    def _button_row(self) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        frame.grid(row=self._row(), column=1, columnspan=2, sticky="w")
        return frame

    def _probe_tools(self) -> None:
        try:
            import yt_dlp_ejs

            ejs = yt_dlp_ejs.version
        except Exception:
            ejs = "not found"
        ffmpeg = _tool_version(find_ffmpeg(), "-version", 2)
        deno = _tool_version(find_deno(), "--version", 1)
        self.app.call_soon(self._show_tools, ejs, ffmpeg, deno)

    def _show_tools(self, ejs: str, ffmpeg: str, deno: str) -> None:
        self.values["yt-dlp-ejs"].configure(text=ejs)
        self.values["ffmpeg"].configure(text=ffmpeg)
        self.values["deno"].configure(text=deno)

    def refresh(self) -> None:
        """Re-read update state from the app."""
        app = self.app
        active = updater.ACTIVE
        source = "bundled with the app" if active.source == "bundled" else f"updated (app ships {active.bundled})"
        self.values["yt-dlp"].configure(text=f"{active.version}  ·  {source}")

        self.app_status.configure(text=app.app_update_message or f"Version {__version__}")
        for btn in (self.app_install_btn, self.app_notes_btn):
            btn.pack_forget()
        if app.app_release:
            self.app_install_btn.configure(
                state="disabled" if app.app_update_busy else "normal",
                text="Update now" if updater.can_self_update() else "Download",
            )
            self.app_install_btn.pack(side="left", pady=(4, 0))
            self.app_notes_btn.pack(side="left", padx=(8, 0), pady=(4, 0))

        self.ytdlp_status.configure(text=app.update_message or active.note or f"Version {active.version}")
        self.install_btn.pack_forget()
        self.restart_btn.pack_forget()
        if app.update_pending:
            self.restart_btn.pack(side="left", pady=(4, 0))
        elif app.update_available and not app.update_busy:
            self.install_btn.pack(side="left", pady=(4, 0))

        busy = app.update_busy or app.app_update_busy
        self.check_btn.configure(state="disabled" if busy else "normal")
