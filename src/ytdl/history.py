"""Persistent record of finished downloads (%APPDATA%\\ytdl\\history.json)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ytdl.updater import DATA_DIR

HISTORY_FILE = DATA_DIR / "history.json"


@dataclass
class HistoryEntry:
    video_id: str
    title: str
    url: str
    mode: str  # "video" or "audio"
    path: str
    size: int
    timestamp: float

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()  # a file, or a folder of chapter tracks


class History:
    def __init__(self, path: Path = HISTORY_FILE) -> None:
        self.path = path
        self.entries: list[HistoryEntry] = []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.entries = [HistoryEntry(**item) for item in raw]
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps([asdict(e) for e in self.entries], indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def add(self, video_id: str, title: str, url: str, mode: str, path: Path) -> HistoryEntry:
        # Re-downloading to the same file replaces the old record.
        self.entries = [e for e in self.entries if e.path != str(path)]
        if path.is_dir():
            size = sum(f.stat().st_size for f in path.iterdir() if f.is_file())
        else:
            size = path.stat().st_size if path.is_file() else 0
        entry = HistoryEntry(video_id, title, url, mode, str(path), size, time.time())
        self.entries.append(entry)
        self.save()
        return entry

    def remove(self, entry: HistoryEntry) -> None:
        self.entries = [e for e in self.entries if e is not entry]
        self.save()

    def clear(self) -> None:
        self.entries = []
        self.save()

    def downloaded_ids(self, mode: str) -> set[str]:
        """IDs whose file for this mode is still on disk."""
        return {e.video_id for e in self.entries if e.mode == mode and e.video_id and e.exists}

    def newest_first(self) -> list[HistoryEntry]:
        return sorted(self.entries, key=lambda e: e.timestamp, reverse=True)
