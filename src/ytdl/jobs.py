"""Download queue with concurrent workers.

All JobManager methods run on the UI thread. Workers only post events to a
queue; the UI thread feeds them back through handle_event().
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable

from ytdl.downloader import DownloadCancelled, DownloadOptions, Progress, Result, download, friendly_error

QUEUED, RUNNING, DONE, FAILED, CANCELLED, SKIPPED = "Queued", "Downloading", "Done", "Failed", "Cancelled", "Skipped"
FINISHED = (DONE, FAILED, CANCELLED, SKIPPED)

_ids = itertools.count(1)


@dataclass
class Job:
    opts: DownloadOptions
    label: str
    id: int = field(default_factory=lambda: next(_ids))
    status: str = QUEUED
    percent: float | None = 0.0
    title: str = ""
    detail: str = ""
    path: str = ""  # latest file being written, for "Open folder"
    result: Result | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def mode(self) -> str:
        return "audio" if self.opts.audio_only else "video"

    @property
    def display_title(self) -> str:
        return self.title or self.label


class JobManager:
    def __init__(
        self,
        post: Callable[[int, str, object], None],
        on_change: Callable[[Job], None],
        on_list_change: Callable[[], None],
        on_finished: Callable[[Job], None],
        skip_ids: Callable[[str], set[str]],
    ) -> None:
        self.jobs: list[Job] = []
        self.max_concurrent = 1
        self._post = post
        self._on_change = on_change
        self._on_list_change = on_list_change
        self._on_finished = on_finished
        self._skip_ids = skip_ids

    # -------------------------------------------------------------- queries

    def by_id(self, job_id: int) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def count(self, status: str) -> int:
        return sum(1 for j in self.jobs if j.status == status)

    @property
    def busy(self) -> bool:
        return any(j.status in (QUEUED, RUNNING) for j in self.jobs)

    # -------------------------------------------------------------- actions

    def add(self, opts: DownloadOptions, label: str) -> Job:
        job = Job(opts, label)
        self.jobs.append(job)
        self._on_list_change()
        self.pump()
        return job

    def pump(self) -> None:
        """Start queued jobs while there are free slots."""
        while self.count(RUNNING) < self.max_concurrent:
            job = next((j for j in self.jobs if j.status == QUEUED), None)
            if job is None:
                return
            self._start(job)

    def cancel(self, job: Job) -> None:
        if job.status == QUEUED:
            job.status = CANCELLED
            job.detail = ""
            self._on_change(job)
        elif job.status == RUNNING:
            job.cancel.set()
            job.detail = "Cancelling…"
            self._on_change(job)

    def cancel_all(self) -> None:
        for job in self.jobs:
            self.cancel(job)

    def retry(self, job: Job) -> None:
        if job.status in (FAILED, CANCELLED, SKIPPED):
            job.status, job.percent, job.detail, job.result = QUEUED, 0.0, "", None
            job.cancel = threading.Event()
            self._on_change(job)
            self.pump()

    def remove(self, job: Job) -> None:
        if job.status == RUNNING:
            return
        self.jobs.remove(job)
        self._on_list_change()

    def move(self, job: Job, delta: int) -> None:
        """Move a queued job earlier/later among the queued jobs."""
        queued = [j for j in self.jobs if j.status == QUEUED]
        if job not in queued:
            return
        target = queued.index(job) + delta
        if not 0 <= target < len(queued):
            return
        other = queued[target]
        a, b = self.jobs.index(job), self.jobs.index(other)
        self.jobs[a], self.jobs[b] = self.jobs[b], self.jobs[a]
        self._on_list_change()

    def clear_finished(self) -> None:
        self.jobs = [j for j in self.jobs if j.status not in FINISHED]
        self._on_list_change()

    # -------------------------------------------------------------- persistence

    def snapshot(self) -> list[dict]:
        """Unfinished jobs, in queue order, for resuming in the next session."""
        return [
            {"label": j.label, "title": j.title, "opts": j.opts.to_dict()}
            for j in self.jobs if j.status in (QUEUED, RUNNING)
        ]

    def restore(self, saved: list[dict]) -> int:
        restored = 0
        for item in saved:
            try:
                job = Job(DownloadOptions.from_dict(item["opts"]), item["label"], title=item.get("title", ""))
            except (KeyError, TypeError, ValueError):
                continue
            job.detail = "Resumed from last session"
            self.jobs.append(job)
            restored += 1
        if restored:
            self._on_list_change()
            self.pump()
        return restored

    # -------------------------------------------------------------- workers

    def _start(self, job: Job) -> None:
        job.status, job.percent, job.detail = RUNNING, None, "Fetching video info"
        self._on_change(job)
        skip = frozenset(self._skip_ids(job.mode))
        threading.Thread(target=self._run, args=(job, skip), daemon=True).start()

    def _run(self, job: Job, skip: frozenset[str]) -> None:
        post = self._post
        try:
            result = download(
                job.opts,
                on_progress=lambda p: post(job.id, "progress", p),
                on_log=lambda m: post(job.id, "log", m),
                cancel=job.cancel,
                skip_ids=skip,
            )
            post(job.id, "done", result)
        except DownloadCancelled:
            post(job.id, "cancelled", None)
        except Exception as exc:  # yt-dlp raises many types; surface them all
            post(job.id, "error", exc)

    def handle_event(self, job_id: int, kind: str, payload: object) -> None:
        job = self.by_id(job_id)
        if job is None:
            return
        if kind == "progress":
            if job.cancel.is_set():
                return
            p: Progress = payload
            job.title = f"[{p.item}] {p.title}" if p.item else p.title or job.title
            job.percent, job.detail = p.percent, p.detail
            job.path = p.path or job.path
            self._on_change(job)
            return

        if kind == "done":
            result: Result = payload
            job.result = result
            saved, skipped = len(result.items), len(result.skipped)
            if saved == 0 and skipped:
                job.status = SKIPPED
                reasons = sorted({reason for _title, reason in result.skipped})
                job.detail = f"Skipped: {', '.join(reasons)}"
            else:
                job.status, job.percent = DONE, 100.0
                if job.opts.tracks and saved:
                    songs = len(job.opts.tracks)
                    job.detail = f"Saved {songs} song{'s' if songs != 1 else ''} in {result.items[0].path.name}"
                else:
                    job.detail = f"Saved {saved} file{'s' if saved != 1 else ''}"
                if skipped:
                    job.detail += f", skipped {skipped}"
        elif kind == "cancelled":
            job.status, job.percent, job.detail = CANCELLED, 0.0, "Partial files are kept so a retry resumes"
        elif kind == "error":
            message = friendly_error(str(payload))
            job.status, job.percent = FAILED, 0.0
            job.detail = message.splitlines()[0] if message else type(payload).__name__
        else:
            return
        self._on_change(job)
        self._on_finished(job)
        self.pump()
