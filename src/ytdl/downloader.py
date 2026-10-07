"""Download engine: a thin wrapper around yt-dlp.

ffmpeg (merging video+audio, audio conversion) and deno (YouTube's JS
challenges) are resolved from the PyInstaller bundle when frozen, otherwise
from the imageio-ffmpeg / deno Python packages, falling back to PATH.
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
from dataclasses import asdict, dataclass, field, fields, replace
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

# Error text that usually means "you need to be signed in".
SIGN_IN_HINTS = ("sign in to confirm", "members-only", "members only", "age-restricted", "inappropriate for some users",
                 "private video", "join this channel", "login required", "--cookies")


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
    # Songs to cut an audio download into (from the track picker): [{"start", "end", "name"}]
    tracks: list[dict] = field(default_factory=list)
    album: str = ""
    artist: str = ""
    number_tracks: bool = True
    artist_titles: bool = False  # track names are "Artist - Title"
    keep_full: bool = False  # with tracks: also keep the unsplit file
    # Music sorting into <output>/Artist/Album/. song is decided up front (by the
    # song dialog) for single videos; playlist entries are identified as they go.
    organize_music: bool = False
    lookup_albums: bool = True
    song: dict = field(default_factory=dict)  # {"artist", "album", "title", "year"}
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
    path: str = ""  # the file being written, for "Open folder"


@dataclass
class DownloadedItem:
    video_id: str
    title: str
    url: str
    path: Path  # a file, or the folder of split tracks


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


def _clean(message: str) -> str:
    while message.startswith("ERROR: "):
        message = message.removeprefix("ERROR: ")
    message = " ".join(message.split())  # yt-dlp pads URLs with double spaces
    message = re.sub(r"^\[[\w:]+\] (?:[\w-]+: )?", "", message)  # "[youtube] abc123: ", "[generic] "
    # Command-line advice ("Use --cookies ...", "See ...wiki/FAQ...") means nothing in a GUI.
    message = re.split(r" (?:Use --|See https://github\.com/yt-dlp/yt-dlp/wiki/FAQ)", message)[0]
    return message.strip()


def friendly_error(message: str) -> str:
    needs_sign_in = is_sign_in_error(message)  # before _clean drops the "--cookies" advice
    message = _clean(message)
    if needs_sign_in and "Settings" not in message:
        message += " (try turning on sign-in cookies in Settings)"
    return message


def is_sign_in_error(message: str) -> bool:
    return any(hint in message.lower() for hint in SIGN_IN_HINTS)


def is_cookie_error(message: str) -> bool:
    """yt-dlp couldn't load the cookies themselves (locked, encrypted, missing)."""
    lower = message.lower()
    return "cookie" in lower and any(
        word in lower for word in ("could not copy", "could not find", "decrypt", "database", "failed to load",
                                   "unsupported", "keyring", "permission")
    )


def cookie_source(cookies_browser: str, cookies_file: str) -> str:
    return "your cookies.txt file" if cookies_file else cookies_browser.title()


def cookie_advice(cookies_browser: str, cookies_file: str) -> str:
    if cookies_file:
        return "Check that the cookies.txt file exists and was exported while signed in to YouTube."
    if cookies_browser == "firefox":
        return "Make sure Firefox is installed and you're signed in to YouTube in it."
    return (f"{cookies_browser.title()} locks and encrypts its cookies while it runs. Close it completely and try "
            "again, or switch to Firefox or a cookies.txt file in Settings.")


def with_cookie_fallback(fn: Callable, cookies_browser: str, cookies_file: str,
                         log: Callable[[str], None] | None = None):
    """Run fn(cookies_browser, cookies_file) without cookies first.

    Cookies are only used if YouTube says the video needs a signed-in account,
    so a locked or unreadable browser profile never breaks normal downloads.
    """
    if not (cookies_browser or cookies_file):
        return fn("", "")
    try:
        return fn("", "")
    except DownloadCancelled:
        raise
    except Exception as exc:
        if not is_sign_in_error(str(exc)):
            raise
    source = cookie_source(cookies_browser, cookies_file)
    if cookies_file and not Path(cookies_file).is_file():
        raise RuntimeError(f"YouTube wants a signed-in account for this, but the cookies file {cookies_file} "
                           "doesn't exist. Choose it again in Settings.")
    if log:
        log(f"YouTube wants a signed-in account for this; retrying with cookies from {source}.")
    try:
        return fn(cookies_browser, cookies_file)
    except DownloadCancelled:
        raise
    except Exception as exc:
        message = _clean(str(exc))
        if is_cookie_error(message):
            raise RuntimeError(f"Couldn't read cookies from {source}. "
                               f"{cookie_advice(cookies_browser, cookies_file)}") from exc
        if is_sign_in_error(message):
            raise RuntimeError(f"{message.rstrip('.')}. Even with cookies from {source}, YouTube still wants a sign-in. "
                               f"Make sure you're signed in to YouTube there (Settings).") from exc
        raise


def test_cookies(cookies_browser: str, cookies_file: str) -> str:
    """Try loading the configured cookies. Returns a message for the Settings tab."""
    if not (cookies_browser or cookies_file):
        return "Cookies are off."
    if cookies_file and not Path(cookies_file).is_file():
        return f"✗ Can't find {cookies_file}. Choose the cookies.txt file with Browse…"
    source = cookie_source(cookies_browser, cookies_file)
    try:
        with yt_dlp.YoutubeDL(base_opts(cookies_browser, cookies_file)) as ydl:
            jar = ydl.cookiejar
            youtube = sum(1 for c in jar if c.domain.endswith(("youtube.com", "google.com")))
    except Exception as exc:
        message = _clean(str(exc))
        if is_cookie_error(message):
            return f"✗ Couldn't read cookies from {source}. {cookie_advice(cookies_browser, cookies_file)}"
        return f"✗ {message}"
    if not youtube:
        return f"Read cookies from {source}, but none are for YouTube. Sign in to YouTube there first."
    return f"✓ Found {youtube} YouTube/Google cookies in {source}. They'll be used when a video needs sign-in."


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
        outtmpl=name + ".%(ext)s",
        noplaylist=not opts.playlist,
        windowsfilenames=True,
        allow_playlist_files=False,  # no stray playlist cover/description files
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

    return ydl_opts


class _MusicTagger(PostProcessor):
    """Before download: settle the song's artist/album/title so tags and the
    Artist/Album folder agree. Runs per video, so playlists are sorted per song."""

    def __init__(self, opts: DownloadOptions) -> None:
        super().__init__()
        self.opts = opts

    def run(self, info: dict):
        from ytdl.music import SINGLES, identify, is_music

        opts = self.opts
        if opts.song:
            song = dict(opts.song)
        elif opts.organize_music and opts.playlist and is_music(info):
            found = identify(info, lookup=opts.lookup_albums)
            if not found.artist or found.artist_source == "channel":
                return [], info  # no trustworthy artist; leave it in the playlist folder
            song = found.as_dict()
            song["album"] = song["album"] or SINGLES
        else:
            return [], info
        info["artist"], info["album"], info["track"] = song["artist"], song["album"], song["title"]
        if song.get("year"):
            info["release_year"] = song["year"]
        info["ytdl_sort"] = song
        return [], info


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
        if song := info.get("ytdl_sort"):
            path = _move_into_library(path, self.opts.output_dir, song)
            info["filepath"] = str(path)
        self.result.items.append(
            DownloadedItem(
                video_id=info.get("id", ""),
                title=info.get("title", ""),
                url=info.get("webpage_url") or info.get("original_url", ""),
                path=path,
            )
        )
        return [], info


def _move_into_library(path: Path, root: Path, song: dict) -> Path:
    """<root>/Artist/Album/Title.ext. The same song again replaces the old file."""
    from ytdl.tracks import safe_filename

    folder = root / safe_filename(song["artist"]) / safe_filename(song["album"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{safe_filename(song['title'] or path.stem)}{path.suffix}"
    if target != path:
        target.unlink(missing_ok=True)
        shutil.move(str(path), target)
        # Tidy a template folder (e.g. a channel folder) the move left empty.
        if path.parent != root and path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    return target


def download(
    opts: DownloadOptions,
    on_progress: Callable[[Progress], None],
    on_log: Callable[[str], None],
    cancel: threading.Event,
    skip_ids: set[str] | frozenset[str] = frozenset(),
) -> Result:
    """Run a download synchronously. Call from a worker thread.

    Live/upcoming streams are always skipped, as are videos whose ID is in skip_ids.
    Sign-in cookies are only used if YouTube asks for them.
    """

    def attempt(cookies_browser: str, cookies_file: str) -> Result:
        with_cookies = replace(opts, cookies_browser=cookies_browser, cookies_file=cookies_file)
        return _download_once(with_cookies, on_progress, on_log, cancel, skip_ids)

    return with_cookie_fallback(attempt, opts.cookies_browser, opts.cookies_file, on_log)


def _download_once(
    opts: DownloadOptions,
    on_progress: Callable[[Progress], None],
    on_log: Callable[[str], None],
    cancel: threading.Event,
    skip_ids: set[str] | frozenset[str],
) -> Result:
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
            on_progress(Progress(title, percent, detail, item_label(info), d.get("filename", "")))
        elif d["status"] == "finished":
            on_progress(Progress(title, 100.0, "Download complete", item_label(info), d.get("filename", "")))

    def postprocessor_hook(d: dict) -> None:
        if cancel.is_set():
            raise DownloadCancelled()
        if d["status"] == "started":
            info = d.get("info_dict", {})
            name = d.get("postprocessor", "")
            on_progress(Progress(info.get("title", ""), None, f"Processing ({name})…", item_label(info),
                                 info.get("filepath", "")))

    ydl_opts = build_ydl_opts(opts)
    ydl_opts["logger"] = _Logger(on_log)
    ydl_opts["progress_hooks"] = [progress_hook]
    ydl_opts["postprocessor_hooks"] = [postprocessor_hook]
    ydl_opts["match_filter"] = match_filter

    opts.output_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if opts.audio_only and (opts.song or opts.organize_music) and not opts.tracks:
            ydl.add_post_processor(_MusicTagger(opts), when="pre_process")
        ydl.add_post_processor(_Collector(result, opts), when="after_move")
        ret = ydl.download([opts.url])
    if cancel.is_set():
        raise DownloadCancelled()
    if ret != 0:
        raise RuntimeError("yt-dlp reported errors; see the log for details.")
    if opts.tracks and opts.audio_only and result.items:
        _split_into_tracks(opts, result.items[0], on_progress, cancel)
    return result


def _split_into_tracks(opts: DownloadOptions, item: DownloadedItem,
                       on_progress: Callable[[Progress], None], cancel: threading.Event) -> None:
    """Cut the downloaded audio into the songs picked in the track dialog."""
    from ytdl.tracks import safe_filename, split_audio  # tracks imports this module

    full = item.path
    album = safe_filename(opts.album or item.title)
    if opts.organize_music and opts.artist:
        folder = opts.output_dir / safe_filename(opts.artist) / album  # the music library layout
    else:
        folder = full.parent / album

    def progress(number: int, total: int, name: str) -> None:
        on_progress(Progress(item.title, (number - 1) / total * 100, f"Saving song {number} of {total}: {name}",
                             path=str(folder)))

    split_audio(full, folder, opts.tracks, opts.album or item.title, opts.artist, opts.number_tracks,
                opts.artist_titles, progress, cancel)
    if cancel.is_set():
        raise DownloadCancelled()
    if not opts.keep_full:
        full.unlink(missing_ok=True)
    item.path = folder
