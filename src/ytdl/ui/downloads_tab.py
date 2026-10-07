from __future__ import annotations

from typing import TYPE_CHECKING

import customtkinter as ctk

from ytdl.jobs import CANCELLED, DONE, FAILED, QUEUED, RUNNING, SKIPPED, Job
from ytdl.ui.common import CARD, DANGER, MUTED, SECONDARY, STATUS_COLORS, clear_children, open_path, reveal

if TYPE_CHECKING:
    from ytdl.ui.app import App

MAX_LOG_LINES = 5000


class JobRow(ctk.CTkFrame):
    def __init__(self, master, job: Job, tab: DownloadsTab) -> None:
        super().__init__(master, fg_color=CARD)
        self.job = job
        self.tab = tab
        self.shown_status: str | None = None
        self.grid_columnconfigure(0, weight=1)

        self.title = ctk.CTkLabel(
            self, text="", anchor="w", justify="left", wraplength=520, font=ctk.CTkFont(weight="bold")
        )
        self.title.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.status = ctk.CTkLabel(line, text="", font=ctk.CTkFont(weight="bold"))
        self.status.pack(side="left")
        self.detail = ctk.CTkLabel(line, text="", anchor="w", text_color=MUTED)
        self.detail.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.progress = ctk.CTkProgressBar(self, height=8)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.grid(row=0, column=1, rowspan=3, padx=10)
        self.refresh()

    def refresh(self) -> None:
        job = self.job
        kind = "Audio" if job.opts.audio_only else "Video"
        if job.opts.format_label:
            kind += f" {job.opts.format_label}"
        self.title.configure(text=job.display_title)
        self.status.configure(text=f"{job.status}", text_color=STATUS_COLORS.get(job.status, MUTED))
        self.detail.configure(text=f"{kind}  ·  {job.detail}" if job.detail else kind)

        # A bar at 0% still draws a sliver, so only show it while it means something.
        if job.status in (RUNNING, DONE):
            self.progress.grid()
        else:
            self.progress.grid_remove()
        if job.status == RUNNING and job.percent is None:
            if self.progress.cget("mode") != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start()
        else:
            if self.progress.cget("mode") != "determinate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.set((job.percent or 0) / 100)

        if job.status != self.shown_status:
            self.shown_status = job.status
            self._build_actions()

    def _build_actions(self) -> None:
        clear_children(self.actions)
        jobs, job = self.tab.app.jobs, self.job

        def button(text, command, width=64, **style):
            ctk.CTkButton(self.actions, text=text, width=width, command=command, **style).pack(side="left", padx=2)

        if job.status == QUEUED:
            button("▲", lambda: jobs.move(job, -1), width=32, **SECONDARY)
            button("▼", lambda: jobs.move(job, 1), width=32, **SECONDARY)
            button("Cancel", lambda: jobs.cancel(job), width=70, **SECONDARY)
        elif job.status == RUNNING:
            button("Cancel", lambda: jobs.cancel(job), width=70, **DANGER)
        elif job.status == DONE:
            items = job.result.items if job.result else []
            if len(items) == 1:
                button("Open", lambda: open_path(items[0].path))
            if items:
                button("Folder", lambda: reveal(items[0].path), **SECONDARY)
            button("✕", lambda: jobs.remove(job), width=32, **SECONDARY)
        elif job.status in (FAILED, CANCELLED, SKIPPED):
            button("Retry", lambda: jobs.retry(job))
            button("✕", lambda: jobs.remove(job), width=32, **SECONDARY)


class DownloadsTab:
    """Download manager: per-item progress and controls, plus the log."""

    def __init__(self, app: App, tab: ctk.CTkFrame) -> None:
        self.app = app
        self.rows: dict[int, JobRow] = {}
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(tab, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")
        self.summary = ctk.CTkLabel(bar, text="", anchor="w", text_color=MUTED)
        self.summary.pack(side="left")
        self.log_btn = ctk.CTkButton(bar, text="Show log", width=90, command=self.toggle_log, **SECONDARY)
        self.log_btn.pack(side="right")
        ctk.CTkButton(bar, text="Clear finished", width=110, command=app.jobs.clear_finished, **SECONDARY).pack(
            side="right", padx=6
        )
        ctk.CTkButton(bar, text="Cancel all", width=90, command=app.jobs.cancel_all, **SECONDARY).pack(side="right")

        self.list = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.list.grid_columnconfigure(0, weight=1)
        self.empty = ctk.CTkLabel(
            self.list, text="Nothing here yet. Use Search or Link to add downloads.", text_color=MUTED
        )

        self.log_box = ctk.CTkTextbox(tab, height=170, font=ctk.CTkFont(family="Consolas", size=12), wrap="word")
        self.log_box.configure(state="disabled")
        self.log_visible = False

        self.rebuild()

    def toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
            self.log_box.see("end")
        else:
            self.log_box.grid_forget()
        self.log_btn.configure(text="Hide log" if self.log_visible else "Show log")

    def rebuild(self) -> None:
        """Sync rows with the job list (adds, removals, reordering)."""
        jobs = self.app.jobs.jobs
        live = {j.id for j in jobs}
        for job_id in [i for i in self.rows if i not in live]:
            self.rows.pop(job_id).destroy()
        for index, job in enumerate(jobs):
            row = self.rows.get(job.id)
            if row is None:
                row = self.rows[job.id] = JobRow(self.list, job, self)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 6))
        if jobs:
            self.empty.grid_forget()
        else:
            self.empty.grid(row=0, column=0, pady=40)
        self.update_summary()

    def update_job(self, job: Job) -> None:
        if row := self.rows.get(job.id):
            row.refresh()
        self.update_summary()

    def update_summary(self) -> None:
        jobs = self.app.jobs
        parts = [f"{jobs.count(s)} {s.lower()}" for s in (RUNNING, QUEUED, DONE, FAILED, SKIPPED) if jobs.count(s)]
        self.summary.configure(text="  ·  ".join(parts) if parts else "No downloads")

    def log(self, message: str) -> None:
        box = self.log_box
        box.configure(state="normal")
        box.insert("end", message + "\n")
        if int(box.index("end-1c").split(".")[0]) > MAX_LOG_LINES:
            box.delete("1.0", "1001.0")
        box.see("end")
        box.configure(state="disabled")
