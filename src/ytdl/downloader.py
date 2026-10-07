"""Download engine: a thin wrapper around yt-dlp.

ffmpeg (merging video+audio, audio conversion) and deno (YouTube's JS
challenges) are resolved from the PyInstaller bundle when frozen, otherwise
from the imageio-ffmpeg / deno Python packages, falling back to PATH.
"""

from __future__ import annotations

import shutil
import sys
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor

VIDEO_QUALITIES = ["Best", "2160p", "1440p", "1080p", "720p", "480p", "360p"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "wav"]
COOKIE_BROWSERS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"]

# File name templates (yt-dlp syntax, without extension). "/" makes folders.
NAME_PRESETS = {
    "Title [id]": "%(title)s [%(id)s]",
    "Title": "%(title)s",
    "Channel - Title": "%(channel)s - %(title)s",
    "Channel folder / Title": "%(channel)s/%(title)s",
    "Date - Title": "%(upload_date>%Y-%m-%d)s - %(title)s",
}
DEFAULT_NAME_TEMPLATE = NAME_PRESETS["Title [id]"]
CHAPTER_SUFFIX = "/%(section_number)02d - %(section_title)s.%(ext)s"

# Error text that usually means "you need to be signed in".
SIGN_IN_HINTS = ("sign in to confirm", "members-only", "members only", "age-restricted", "inappropriate for some users",
                 "private video", "join this channel")


class DownloadCancelled(Exception):
    pass


@dataclass
class DownloadOptions:
    url: str
    output_dir: Path
    audio_only: bool = False
    quality: str = "Best"
    audio_format: str = "mp3"
    playlist: bool = False
    name_template: str = DEFAULT_NAME_TEMPLATE
    split_chapters: bool = False
    keep_full: bool = False  # with split_chapters: also keep the unsplit file
    cookies_browser: str = ""
    cookies_file: str = ""
    format_spec: str = ""  # explicit yt-dlp format from the format picker
    format_label: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["output_dir"] = str(self.output_dir)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> DownloadOptions:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        values["output_dir"] = Path(values["output_dir"])
        return cls(**values)


@dataclass
class Progress:
    title: str = ""
    percent: float | None = None
    detail: str = ""
    item: str = ""


@dataclass
class DownloadedItem:
    video_id: str
    title: str
    url: str
    path: Path  # a file, or the folder of chapter tracks


@dataclass
class Result:
    items: list[DownloadedItem] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (title, reason)


def _bundled(name: str) -> str | None:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        path = Path(base) / "bin" / name
        if path.exists():
            return str(path)
    return None


def find_ffmpeg() -> str | None:
    if path := _bundled("ffmpeg.exe"):
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def find_deno() -> str | None:
    if path := _bundled("deno.exe"):
        return path
    try:
        import deno

        return deno.find_deno_bin()
    except Exception:
        return shutil.which("deno")


def base_opts(cookies_browser: str = "", cookies_file: str = "") -> dict:
    """Options every yt-dlp call needs: tools and (optional) sign-in cookies."""
    opts: dict = {"quiet": True, "no_warnings": True}
    if ffmpeg := find_ffmpeg():
        opts["ffmpeg_location"] = ffmpeg
    if deno := find_deno():
        opts["js_runtimes"] = {"deno": {"path": deno}}
    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


def friendly_error(message: str) -> str:
    message = message.removeprefix("ERROR: ").strip()
    if any(hint in message.lower() for hint in SIGN_IN_HINTS):
        message += " (try turning on sign-in cookies in Settings)"
    return message


def validate_template(template: str) -> str | None:
    """Return an error message, or None if the name template is usable."""
    if not template.strip():
        return "The template is empty."
    err = yt_dlp.YoutubeDL.validate_outtmpl(template + ".%(ext)s")
    return str(err) if err else None


def preview_template(template: str) -> str:
    sample = {
        "id": "dQw4w9WgXcQ", "title": "Example Video Title", "channel": "Example Channel",
        "uploader": "Example Channel", "upload_date": "20240131", "ext": "mp4",
        "playlist_title": "Example Playlist", "playlist_index": 1,
    }
    with yt_dlp.YoutubeDL({"quiet": True, "windowsfilenames": True}) as ydl:
        name = ydl.evaluate_outtmpl(template + ".%(ext)s", sample, sanitize=True)
    return yt_dlp.utils.sanitize_path(name, force=True).replace("\\", "/")


def _fmt_bytes(n: float | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def _fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class _Logger:
    """Routes yt-dlp's console output to a callback."""

    def __init__(self, log: Callable[[str], None]):
        self._log = log

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            self.info(msg)

    def info(self, msg: str) -> None:
        # yt-dlp probes thumbnails one by one; these lines are just noise.
        if "thumbnail" in msg.lower() and msg.startswith("[info]") and "Writing" not in msg:
            return
        self._log(msg)

    def warning(self, msg: str) -> None:
        self._log(f"WARNING: {msg}")

    def error(self, msg: str) -> None:
        self._log(msg)


def build_ydl_opts(opts: DownloadOptions) -> dict:
    name = opts.name_template.strip() or DEFAULT_NAME_TEMPLATE
    if opts.playlist:
        # Playlists get their own folder; only the file-name part of the template applies.
        name = "%(playlist_title)s/%(playlist_index)03d - " + name.split("/")[-1]

    ydl_opts = base_opts(opts.cookies_browser, opts.cookies_file)
    ydl_opts.update(
        paths={"home": str(opts.output_dir)},
        outtmpl={"default": name + ".%(ext)s", "chapter": name + CHAPTER_SUFFIX},
        noplaylist=not opts.playlist,
        windowsfilenames=True,
        noprogress=True,
        postprocessors=[],
    )

    if opts.audio_only:
        ydl_opts["format"] = opts.format_spec or "bestaudio/best"
        ydl_opts["writethumbnail"] = opts.audio_format in ("mp3", "m4a")
        ydl_opts["postprocessors"] += [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": opts.audio_format,
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ]
        if ydl_opts["writethumbnail"]:
            ydl_opts["postprocessors"].append({"key": "EmbedThumbnail"})
    else:
        if opts.format_spec:
            ydl_opts["format"] = opts.format_spec
        else:
            # Prefer H.264/AAC at the chosen resolution so the MP4 plays everywhere;
            # resolution still wins over codec (YouTube only serves 4K as VP9/AV1).
            sort = ["vcodec:h264", "acodec:aac"]
            if opts.quality != "Best":
                sort.insert(0, f"res:{opts.quality.rstrip('p')}")
            ydl_opts["format"] = "bestvideo*+bestaudio/best"
            ydl_opts["format_sort"] = sort
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})

    if opts.split_chapters:
        ydl_opts["postprocessors"].append({"key": "FFmpegSplitChapters", "force_keyframes": False})

    return ydl_opts


class _Collector(PostProcessor):
    """Runs after the file reaches its final location and records it."""

    def __init__(self, result: Result, opts: DownloadOptions) -> None:
        super().__init__()
        self.result = result
        self.opts = opts

    def run(self, info: dict):
        path = info.get("filepath")
        if not path:
            return [], info
        path = Path(path)
        if self.opts.split_chapters and info.get("chapters"):
            sample = {**info, "section_number": 1, "section_title": "x"}
            chapter_dir = Path(self._downloader.prepare_filename(sample, "chapter")).parent
            if chapter_dir.is_dir():
                if not self.opts.keep_full:
                    path.unlink(missing_ok=True)
                path = chapter_dir
        self.result.items.append(
            DownloadedItem(
                video_id=info.get("id", ""),
                title=info.get("title", ""),
                url=info.get("webpage_url") or info.get("original_url", ""),
                path=path,
            )
        )
        return [], info


def download(
    opts: DownloadOptions,
    on_progress: Callable[[Progress], None],
    on_log: Callable[[str], None],
    cancel: threading.Event,
    skip_ids: set[str] | frozenset[str] = frozenset(),
) -> Result:
    """Run a download synchronously. Call from a worker thread.

    Live/upcoming streams are always skipped, as are videos whose ID is in skip_ids.
    """
    result = Result()

    def match_filter(info: dict, *, incomplete: bool = False) -> str | None:
        reason = None
        if info.get("live_status") in ("is_live", "is_upcoming") or info.get("is_live"):
            reason = "live stream"
        elif info.get("id") in skip_ids:
            reason = "already downloaded"
        if reason:
            result.skipped.append((info.get("title") or info.get("id", "?"), reason))
        return reason

    def item_label(info: dict) -> str:
        idx, total = info.get("playlist_index"), info.get("n_entries")
        return f"{idx}/{total}" if idx and total else ""

    def progress_hook(d: dict) -> None:
        if cancel.is_set():
            raise DownloadCancelled()
        info = d.get("info_dict", {})
        title = info.get("title", "")
        if d["status"] == "downloading":
            done = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            percent = done / total * 100 if total else None
            speed = d.get("speed")
            detail = (
                f"{_fmt_bytes(done)} / {_fmt_bytes(total)}"
                f"  ·  {_fmt_bytes(speed)}/s  ·  ETA {_fmt_eta(d.get('eta'))}"
            )
            on_progress(Progress(title, percent, detail, item_label(info)))
        elif d["status"] == "finished":
            on_progress(Progress(title, 100.0, "Download complete", item_label(info)))

    def postprocessor_hook(d: dict) -> None:
        if cancel.is_set():
            raise DownloadCancelled()
        if d["status"] == "started":
            info = d.get("info_dict", {})
            name = d.get("postprocessor", "")
            on_progress(Progress(info.get("title", ""), None, f"Processing ({name})…", item_label(info)))

    ydl_opts = build_ydl_opts(opts)
    ydl_opts["logger"] = _Logger(on_log)
    ydl_opts["progress_hooks"] = [progress_hook]
    ydl_opts["postprocessor_hooks"] = [postprocessor_hook]
    ydl_opts["match_filter"] = match_filter

    opts.output_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.add_post_processor(_Collector(result, opts), when="after_move")
        ret = ydl.download([opts.url])
    if cancel.is_set():
        raise DownloadCancelled()
    if ret != 0:
        raise RuntimeError("yt-dlp reported errors; see the log for details.")
    return result
