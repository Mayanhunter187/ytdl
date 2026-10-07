"""Main window: tabs, status bar, settings, and the bridge from worker threads."""

from __future__ import annotations

import json
import queue
import threading
import webbrowser
from pathlib import Path
from tkinter import TclError, messagebox
from typing import Callable

import customtkinter as ctk

from ytdl import updater
from ytdl.downloader import DEFAULT_NAME_TEMPLATE, DownloadOptions, validate_template
from ytdl.history import History
from ytdl.jobs import DONE, FAILED, RUNNING, SKIPPED, Job, JobManager
from ytdl.ui.about_tab import AboutTab
from ytdl.ui.common import MUTED, SECONDARY, open_path, resource_path
from ytdl.ui.downloads_tab import DownloadsTab
from ytdl.ui.history_tab import HistoryTab
from ytdl.ui.link_banner import LinkBanner, is_youtube_url, url_from_drop
from ytdl.ui.link_tab import LinkTab
from ytdl.ui.notify import Notifier
from ytdl.ui.search_tab import SearchTab
from ytdl.ui.settings_tab import COOKIES_FILE_OPTION, COOKIES_OFF, SettingsTab
from ytdl.updater import DATA_DIR, AppRelease, UpdateInfo

try:
    from tkinterdnd2 import DND_FILES, DND_TEXT, TkinterDnD

    DnDWrapper = TkinterDnD.DnDWrapper
except Exception:  # drag and drop is a nice-to-have
    TkinterDnD = None
    DnDWrapper = object

SETTINGS_FILE = DATA_DIR / "settings.json"
QUEUE_FILE = DATA_DIR / "queue.json"
DEFAULT_OUTPUT = Path.home() / "Downloads" / "YTDL"
TABS = ["Search", "Link", "Downloads", "History", "Settings", "About"]
POLL_MS = 100
CLIPBOARD_POLL_MS = 1000
UPDATE_CHECK_DELAY_MS = 3000


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


class App(ctk.CTk, DnDWrapper):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_json(SETTINGS_FILE, {})
        self._calls: queue.Queue[tuple[Callable, tuple]] = queue.Queue()
        self._timers: dict[str, str] = {}
        self._closing = False
        self.history = History()

        # yt-dlp update state, read by the About tab and status bar.
        self.update_busy = False
        self.update_available: UpdateInfo | None = None
        self.update_pending: str | None = updater.installed_pending()
        self.update_message = (
            f"yt-dlp {self.update_pending} is downloaded. Restart the app to start using it."
            if self.update_pending else ""
        )
        # App (exe) update state.
        self.app_release: AppRelease | None = None
        self.app_update_busy = False
        self.app_update_message = ""

        self.mode_var = self._setting_var(ctk.StringVar, "mode", "Video")
        self.quality_var = self._setting_var(ctk.StringVar, "quality", "Best")
        self.audio_var = self._setting_var(ctk.StringVar, "audio_format", "mp3")
        self.playlist_var = self._setting_var(ctk.BooleanVar, "playlist", False)
        self.dir_var = self._setting_var(ctk.StringVar, "output_dir", str(DEFAULT_OUTPUT))
        self.name_template_var = self._setting_var(ctk.StringVar, "name_template", DEFAULT_NAME_TEMPLATE)
        self.split_var = self._setting_var(ctk.BooleanVar, "split_chapters", False)
        self.keep_full_var = self._setting_var(ctk.BooleanVar, "keep_full", False)
        self.concurrent_var = self._setting_var(ctk.StringVar, "concurrent", "2")
        self.skip_var = self._setting_var(ctk.BooleanVar, "skip_existing", True)
        self.cookies_var = self._setting_var(ctk.StringVar, "cookies", COOKIES_OFF)
        self.cookies_file_var = self._setting_var(ctk.StringVar, "cookies_file", "")
        self.notify_var = self._setting_var(ctk.BooleanVar, "notify", True)
        self.clipboard_var = self._setting_var(ctk.BooleanVar, "watch_clipboard", True)
        self.auto_update_var = self._setting_var(ctk.BooleanVar, "auto_update", True)
        self.theme_var = self._setting_var(ctk.StringVar, "theme", "System")
        ctk.set_appearance_mode(self.theme_var.get().lower())

        self.jobs = JobManager(
            post=lambda job_id, kind, payload: self.call_soon(self._on_job_event, job_id, kind, payload),
            on_change=self._on_job_change,
            on_list_change=self._on_job_list_change,
            on_finished=self._on_job_finished,
            skip_ids=lambda mode: self.history.downloaded_ids(mode) if self.skip_var.get() else set(),
        )
        self.jobs.max_concurrent = int(self.concurrent_var.get())
        self.concurrent_var.trace_add("write", lambda *_: self._set_concurrency())
        self._batch: list[Job] = []  # finished since the queue was last idle

        self.title("YTDL - YouTube Downloader")
        self.geometry("920x780")
        self.minsize(760, 620)
        icon = resource_path("icon.ico")
        if icon.exists():
            self.iconbitmap(str(icon))
        self.notifier = Notifier(self, icon)

        self._build_ui()
        self._setup_drag_and_drop()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._drain_calls)

        try:
            self._last_clipboard = self.clipboard_get()  # don't offer whatever was copied before launch
        except TclError:
            self._last_clipboard = ""
        self.after(CLIPBOARD_POLL_MS, self._poll_clipboard)

        if restored := self.jobs.restore(load_json(QUEUE_FILE, [])):
            self.downloads_tab.log(f"Resumed {restored} unfinished download{'s' if restored != 1 else ''}.")
            self._refresh_status(last=f"Resumed {restored} download{'s' if restored != 1 else ''} from last time")
        self.after(UPDATE_CHECK_DELAY_MS, self._startup_checks)

    # ---------------------------------------------------------------- layout

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.banner = LinkBanner(self)  # row 0, shown on demand

        self.tabs = ctk.CTkTabview(self, anchor="nw", command=self._on_tab_change)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 0))
        frames = {name: self.tabs.add(name) for name in TABS}
        self.search_tab = SearchTab(self, frames["Search"])
        self.link_tab = LinkTab(self, frames["Link"])
        self.downloads_tab = DownloadsTab(self, frames["Downloads"])
        self.history_tab = HistoryTab(self, frames["History"])
        self.settings_tab = SettingsTab(self, frames["Settings"])
        self.about_tab = AboutTab(self, frames["About"])
        self.tabs.set(self.settings.get("tab") if self.settings.get("tab") in TABS else "Search")

        bar = ctk.CTkFrame(self)
        bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="Ready", anchor="w", text_color=MUTED)
        self.status_label.grid(row=0, column=0, sticky="ew", padx=12, pady=(6, 0))
        self.progress = ctk.CTkProgressBar(bar, height=8)
        self.progress.set(0)
        self.progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(2, 10))
        self.app_update_btn = ctk.CTkButton(bar, text="", width=10, command=lambda: self.show_tab("About"))
        self.update_btn = ctk.CTkButton(bar, text="", width=10, command=self.restart)
        ctk.CTkButton(bar, text="Open folder", width=100, command=self.open_output_folder, **SECONDARY).grid(
            row=0, column=3, rowspan=2, padx=(6, 12)
        )
        self._refresh_update_ui()

    def _setup_drag_and_drop(self) -> None:
        if TkinterDnD is None:
            return
        try:
            self.TkdndVersion = TkinterDnD._require(self)
            self.drop_target_register(DND_TEXT, DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as exc:
            self.downloads_tab.log(f"Drag and drop unavailable: {exc}")

    def show_tab(self, name: str) -> None:
        self.tabs.set(name)
        self._on_tab_change()

    def _on_tab_change(self) -> None:
        self.settings["tab"] = self.tabs.get()
        self._debounce("settings", 500, self._save_settings)
        if self.tabs.get() == "History":
            self.history_tab.refresh()

    # ---------------------------------------------------------------- settings

    def _setting_var(self, kind, key: str, default):
        var = kind(value=self.settings.get(key, default))
        self.bind_setting(var, key)
        return var

    def bind_setting(self, var, key: str) -> None:
        def changed(*_):
            self.settings[key] = var.get()
            self._debounce("settings", 500, self._save_settings)

        var.trace_add("write", changed)

    def _debounce(self, key: str, delay_ms: int, fn: Callable) -> None:
        if timer := self._timers.pop(key, None):
            self.after_cancel(timer)

        def run():
            self._timers.pop(key, None)
            fn()

        self._timers[key] = self.after(delay_ms, run)

    def _save_settings(self) -> None:
        save_json(SETTINGS_FILE, self.settings)

    def _set_concurrency(self) -> None:
        self.jobs.max_concurrent = int(self.concurrent_var.get())
        self.jobs.pump()

    def cookies_browser(self) -> str:
        choice = self.cookies_var.get()
        return "" if choice in (COOKIES_OFF, COOKIES_FILE_OPTION) else choice.lower()

    def cookies_file(self) -> str:
        return self.cookies_file_var.get().strip() if self.cookies_var.get() == COOKIES_FILE_OPTION else ""

    # ---------------------------------------------------------------- threads

    def call_soon(self, fn: Callable, *args) -> None:
        """Thread-safe: run fn(*args) on the UI thread."""
        self._calls.put((fn, args))

    def _drain_calls(self) -> None:
        try:
            while True:
                fn, args = self._calls.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:  # keep the loop alive no matter what
                    self.downloads_tab.log(f"Internal error: {exc!r}")
        except queue.Empty:
            pass
        if not self._closing:
            self.after(POLL_MS, self._drain_calls)

    # ---------------------------------------------------------------- links

    def _poll_clipboard(self) -> None:
        if self._closing:
            return
        if self.clipboard_var.get():
            try:
                text = self.clipboard_get()
            except TclError:
                text = ""
            if text != self._last_clipboard:
                self._last_clipboard = text
                text = text.strip()
                if len(text) < 500 and is_youtube_url(text):
                    self.banner.offer(text if text.startswith("http") else f"https://{text}", "YouTube link copied")
        self._timers["clipboard"] = self.after(CLIPBOARD_POLL_MS, self._poll_clipboard)

    def _on_drop(self, event):
        if url := url_from_drop(self, event.data):
            self.banner.offer(url, "Link dropped")
            self.lift()
        return event.action

    # ---------------------------------------------------------------- downloads

    def make_options(self, url: str, audio_only: bool | None = None, playlist: bool | None = None) -> DownloadOptions:
        return DownloadOptions(
            url=url,
            output_dir=Path(self.dir_var.get().strip() or DEFAULT_OUTPUT).expanduser(),
            audio_only=self.mode_var.get() == "Audio" if audio_only is None else audio_only,
            quality=self.quality_var.get(),
            audio_format=self.audio_var.get(),
            playlist=self.playlist_var.get() if playlist is None else playlist,
            name_template=self._name_template(),
            split_chapters=self.split_var.get(),
            keep_full=self.keep_full_var.get(),
            cookies_browser=self.cookies_browser(),
            cookies_file=self.cookies_file(),
        )

    def _name_template(self) -> str:
        template = self.name_template_var.get().strip()
        return DEFAULT_NAME_TEMPLATE if validate_template(template) else template

    def enqueue(self, opts: DownloadOptions, label: str) -> Job:
        job = self.jobs.add(opts, label)
        self.downloads_tab.log(f"[#{job.id}] Added: {label}")
        return job

    def open_output_folder(self) -> None:
        folder = Path(self.dir_var.get().strip() or DEFAULT_OUTPUT).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        open_path(folder)

    def _on_job_event(self, job_id: int, kind: str, payload: object) -> None:
        if kind == "log":
            self.downloads_tab.log(f"[#{job_id}] {payload}")
        else:
            self.jobs.handle_event(job_id, kind, payload)

    def _on_job_change(self, job: Job) -> None:
        self.downloads_tab.update_job(job)
        self._refresh_status()
        self._save_queue_soon()

    def _on_job_list_change(self) -> None:
        self.downloads_tab.rebuild()
        self._refresh_status()
        self._save_queue_soon()

    def _on_job_finished(self, job: Job) -> None:
        self.downloads_tab.log(f"[#{job.id}] {job.status}: {job.display_title}: {job.detail}")
        if job.status == DONE and job.result:
            for item in job.result.items:
                self.history.add(item.video_id, item.title, item.url, job.mode, item.path)
            self.history_tab.refresh()
        self._batch.append(job)
        self._refresh_status(last=f"{job.status}: {job.display_title}")
        # handle_event pumps the next job after this returns, so check on the next tick.
        self.after(50, self._maybe_notify)

    def _maybe_notify(self) -> None:
        if self.jobs.busy or not self._batch:
            return
        batch, self._batch = self._batch, []
        if not self.notify_var.get() or self.focus_displayof() is not None:
            return  # the user is looking at the app already
        done = sum(1 for j in batch if j.status == DONE)
        failed = sum(1 for j in batch if j.status == FAILED)
        skipped = sum(1 for j in batch if j.status == SKIPPED)
        if not (done or failed):
            return
        if len(batch) == 1 and done:
            title, message = "Download finished", batch[0].display_title
        else:
            title = "Downloads finished"
            parts = [f"{done} done" if done else "", f"{failed} failed" if failed else "",
                     f"{skipped} skipped" if skipped else ""]
            message = ", ".join(p for p in parts if p)
        self.notifier.notify(title, message)

    def _refresh_status(self, last: str | None = None) -> None:
        jobs = self.jobs
        running = [j for j in jobs.jobs if j.status == RUNNING]
        queued = jobs.count("Queued")
        if running:
            text = f"Downloading {len(running)}" + (f"  ·  {queued} queued" if queued else "")
            if len(running) == 1:
                text += f"  ·  {running[0].display_title}"
            self.progress.set(sum(j.percent or 0 for j in running) / (100 * len(running)))
        else:
            text = last or self.status_label.cget("text")
            if not jobs.busy:
                self.progress.set(1 if jobs.count(DONE) else 0)
        self.status_label.configure(text=text)

    def _save_queue_soon(self) -> None:
        if not self._closing:
            self._debounce("queue", 1000, self._save_queue)

    def _save_queue(self) -> None:
        save_json(QUEUE_FILE, self.jobs.snapshot())

    # ---------------------------------------------------------------- updates

    def _startup_checks(self) -> None:
        if self.auto_update_var.get():
            self.check_for_updates(manual=False)

    def check_for_updates(self, manual: bool) -> None:
        self._check_app_release(manual)
        self._run_update(info=None, manual=manual)

    # --- the app itself (GitHub Releases)

    def _check_app_release(self, manual: bool) -> None:
        if self.app_update_busy:
            return
        self.app_update_busy = True
        self.app_update_message = "Checking for a new YTDL version…" if manual else self.app_update_message
        self._refresh_update_ui()

        def work() -> None:
            try:
                release = updater.check_app_release()
                if release and updater.app_is_newer(release):
                    self.call_soon(self._app_check_done, release, f"YTDL {release.version} is available.")
                else:
                    self.call_soon(self._app_check_done, None, "You have the latest version of YTDL.")
            except Exception as exc:
                self.call_soon(self._app_check_done, None, f"Couldn't check for a new YTDL version: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _app_check_done(self, release: AppRelease | None, message: str) -> None:
        self.app_update_busy = False
        self.app_release = release
        self.app_update_message = message
        self._refresh_update_ui()

    def install_app_update(self) -> None:
        release = self.app_release
        if not release or self.app_update_busy:
            return
        if not updater.can_self_update():
            webbrowser.open(release.page_url)
            return
        self.app_update_busy = True
        self.app_update_message = f"Downloading YTDL {release.version}…"
        self._refresh_update_ui()

        def progress(fraction: float) -> None:
            self.call_soon(self._set_app_update_message, f"Downloading YTDL {release.version}… {fraction:.0%}")

        def work() -> None:
            try:
                path = updater.download_app_update(release, progress)
                self.call_soon(self._app_update_downloaded, release, path)
            except Exception as exc:
                self.call_soon(self._app_check_done, release, f"Update failed: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _set_app_update_message(self, message: str) -> None:
        self.app_update_message = message
        self.about_tab.refresh()

    def _app_update_downloaded(self, release: AppRelease, path: Path) -> None:
        self.app_update_busy = False
        if self.jobs.busy and not messagebox.askyesno(
            "Install update",
            f"YTDL {release.version} is ready. The app will restart to install it.\n\n"
            "Running downloads will pause and continue after the restart. Restart now?",
            parent=self,
        ):
            self.app_update_message = f"YTDL {release.version} is downloaded. Click Update now to install it."
            self._refresh_update_ui()
            return
        self._save_queue()
        updater.apply_app_update(path)
        self._shutdown(keep_queue=True)

    # --- yt-dlp (PyPI)

    def install_update(self) -> None:
        if self.update_available:
            self._run_update(info=self.update_available, manual=True)

    def _run_update(self, info: UpdateInfo | None, manual: bool) -> None:
        if self.update_busy:
            return
        install = info is not None or self.auto_update_var.get()
        self.update_busy = True
        self.update_message = "Checking for yt-dlp updates…" if info is None else f"Downloading yt-dlp {info.version}…"
        self._refresh_update_ui()

        def work() -> None:
            try:
                latest = info or updater.check_latest()
                if not updater.is_newer(latest):
                    self.call_soon(self._update_finished, None, None, f"yt-dlp is up to date ({latest.version}).")
                    return
                if not install:
                    self.call_soon(self._update_finished, latest, None, f"yt-dlp {latest.version} is available.")
                    return
                self.call_soon(self._set_update_message, f"Downloading yt-dlp {latest.version}…")
                version = updater.install(latest)
                self.call_soon(self._update_finished, None, version, "")
            except Exception as exc:
                self.call_soon(self._update_finished, None, None, f"Couldn't update yt-dlp: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def _set_update_message(self, message: str) -> None:
        self.update_message = message
        self._refresh_update_ui()

    def _update_finished(self, available: UpdateInfo | None, installed: str | None, message: str) -> None:
        self.update_busy = False
        self.update_available = available
        if installed:
            self.update_pending = installed
        if self.update_pending:
            message = f"yt-dlp {self.update_pending} is downloaded. Restart the app to start using it."
        self.update_message = message
        self._refresh_update_ui()

    def _refresh_update_ui(self) -> None:
        if self.app_release:
            self.app_update_btn.configure(text=f"YTDL {self.app_release.version} available")
            self.app_update_btn.grid(row=0, column=1, rowspan=2, padx=(0, 6))
        else:
            self.app_update_btn.grid_forget()
        if self.update_pending:
            self.update_btn.configure(text=f"Restart to use yt-dlp {self.update_pending}")
            self.update_btn.grid(row=0, column=2, rowspan=2)
        else:
            self.update_btn.grid_forget()
        if hasattr(self, "about_tab"):
            self.about_tab.refresh()

    def restart(self) -> None:
        if self.jobs.busy and not messagebox.askyesno(
            "Restart", "Running downloads will pause and continue after the restart. Restart now?", parent=self
        ):
            return
        self._save_queue()
        updater.restart()
        self._shutdown(keep_queue=True)

    # ---------------------------------------------------------------- closing

    def _on_close(self) -> None:
        if self.jobs.busy and not messagebox.askyesno(
            "Quit", "Downloads are still running. They'll continue the next time you open YTDL.\n\nQuit now?",
            parent=self,
        ):
            return
        self._shutdown(keep_queue=True)

    def _shutdown(self, keep_queue: bool) -> None:
        if keep_queue:
            self._save_queue()  # before cancelling, which marks jobs as cancelled
        self._closing = True
        for timer in self._timers.values():
            self.after_cancel(timer)
        self.jobs.cancel_all()
        self._save_settings()
        self.notifier.remove()
        self.search_tab.shutdown()
        self.destroy()


def _close_splash() -> None:
    try:
        import pyi_splash  # only exists in the PyInstaller build

        pyi_splash.close()
    except Exception:
        pass


def main() -> None:
    ctk.set_default_color_theme("blue")
    app = App()
    app.after(0, _close_splash)
    app.mainloop()
