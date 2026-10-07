"""Find the individual songs in an album / mix video and split the audio into them.

Songs come from, in order of preference: the video's chapters, a tracklist in
the description, or a tracklist in one of the top comments.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yt_dlp

from ytdl.downloader import base_opts, find_ffmpeg, with_cookie_fallback

# A video shorter than this is assumed to be a single song (skips the scan).
MIN_MULTI_SONG_SECONDS = 6 * 60
# Average segment length that looks like songs rather than tutorial chapters.
SONG_SECONDS = (75, 15 * 60)
TOP_COMMENTS = 20
CREATE_NO_WINDOW = 0x08000000

TIMESTAMP = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
NOT_SONGS = re.compile(r"\b(intro|outro|tracklist|track list|credits|sponsor|advert|commercial|end ?screen)\b", re.I)
ALBUM_NOISE = re.compile(
    r"[\[\(]?\s*\b(full album|full ep|album stream|tracklist|track list|official audio|official visuali[sz]er|"
    r"lyrics?|with lyrics|audio only|hq|hd|playlist)\b\s*[\]\)]?",
    re.I,
)


@dataclass
class Track:
    start: float
    end: float
    raw: str
    name: str
    selected: bool = True

    @property
    def length(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class TrackScan:
    url: str
    title: str
    channel: str
    duration: float
    source: str  # "chapters", "description" or "a comment"
    tracks: list[Track] = field(default_factory=list)
    album: str = ""
    artist: str = ""
    artist_titles: bool = False  # names look like "Artist - Title"


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- naming

def clean_name(raw: str) -> str:
    """'03:04   Track 01  -  MEGAVERSE' -> 'MEGAVERSE', '1. Song (0:00)' -> 'Song'."""
    name = TIMESTAMP.sub(" ", raw)
    name = re.sub(r"[\[\(]\s*[-–—~]?\s*[\]\)]", " ", name)  # brackets emptied by the timestamp removal
    name = name.strip(" \t-–—~|:•·*>")
    # "Track 01 - X" -> "X", but only with a separator after the number ("Song 2" is a real title).
    name = re.sub(r"^(?:track|song|part)\s*#?\d{1,3}\s*[.):\-–—]+\s*(?=\S)", "", name, flags=re.I)
    name = re.sub(r"^#?\d{1,3}\s*[.):\-–—]\s*", "", name)  # "01. ", "1) ", "#3 - "
    name = re.sub(r"^0\d\s+", "", name)  # "01 Song" (zero-padded only; "7 Rings" stays)
    name = name.strip(" \t-–—~|:•·*>")
    if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'“”":
        name = name[1:-1]
    name = name.replace("“", "").replace("”", "") if name.count("“") == 1 else name
    return " ".join(name.split())


def clean_album(title: str) -> str:
    album = ALBUM_NOISE.sub(" ", title)
    album = re.sub(r"\(\s*\)|\[\s*\]", " ", album)
    album = " ".join(album.split()).strip(" -–—|:")
    album = re.sub(r"\s*(\|\||\|)\s*$", "", album).strip(" -–—|:")
    return album or title


def guess_artist_album(title: str, channel: str) -> tuple[str, str]:
    album = clean_album(title)
    artist = re.sub(r"\s*-\s*Topic$", "", channel or "").strip()
    # 'ITZY "Motto"' and "Steely Dan - Can't Buy A Thrill (1972)" both name their artist.
    if quoted := re.fullmatch(r"(.{1,40}?)\s+[\"“'‘](.+?)[\"”'’](.*)", album):
        artist, album = quoted.group(1).strip(), (quoted.group(2) + quoted.group(3)).strip()
    elif " - " in album:
        left, right = album.split(" - ", 1)
        if left and right and len(left) <= 40:
            artist, album = left.strip(), right.strip()
    return artist, album


def fix_names(tracks: list[Track]) -> None:
    """Fill empty names and make duplicates unique, in place."""
    seen: dict[str, int] = {}
    for i, track in enumerate(tracks, start=1):
        name = track.name.strip() or f"Track {i}"
        key = name.lower()
        if key in seen:
            seen[key] += 1
            name = f"{name} ({seen[key]})"
        else:
            seen[key] = 1
        track.name = name


def auto_select(tracks: list[Track]) -> None:
    for track in tracks:
        track.selected = track.length >= 15 and not NOT_SONGS.search(track.name)


# ---------------------------------------------------------------- detection

def parse_tracklist(text: str, duration: float) -> list[Track]:
    """Tracks from lines carrying a timestamp, if they form a plausible tracklist."""
    entries: list[tuple[float, str]] = []
    for line in text.splitlines():
        stamps = list(TIMESTAMP.finditer(line))
        if not stamps or len(stamps) > 2:  # 2 allows "0:00 - 3:21 Song"
            continue
        h, m, s = stamps[0].groups()
        start = int(h or 0) * 3600 + int(m) * 60 + int(s)
        name = clean_name(line)
        if name and len(name) <= 150:
            entries.append((start, line.strip()))
    # Keep the longest strictly increasing run; comments often mix in other numbers.
    run: list[tuple[float, str]] = []
    best: list[tuple[float, str]] = []
    for entry in entries:
        if run and entry[0] <= run[-1][0]:
            best = max(best, run, key=len)
            run = []
        run.append(entry)
    best = max(best, run, key=len)
    if len(best) < 3 or best[0][0] > 60 or (duration and best[-1][0] >= duration):
        return []
    tracks = []
    for i, (start, raw) in enumerate(best):
        end = best[i + 1][0] if i + 1 < len(best) else duration
        tracks.append(Track(start, end, raw, clean_name(raw)))
    return tracks


def _from_chapters(chapters: list[dict]) -> list[Track]:
    return [
        Track(c["start_time"], c["end_time"], c.get("title") or "", clean_name(c.get("title") or ""))
        for c in chapters
    ]


def looks_like_songs(tracks: list[Track], duration: float) -> bool:
    if len(tracks) < 2:
        return False
    average = sum(t.length for t in tracks) / len(tracks)
    return SONG_SECONDS[0] <= average <= SONG_SECONDS[1]


def _extract(url: str, cookies_browser: str, cookies_file: str, comments: bool) -> dict:
    opts = base_opts(cookies_browser, cookies_file)
    opts["noplaylist"] = True
    if comments:
        opts["getcomments"] = True
        # max comments, max top-level threads, max replies, max replies per thread
        opts["extractor_args"] = {"youtube": {"max_comments": [str(TOP_COMMENTS), str(TOP_COMMENTS), "0", "0"],
                                              "comment_sort": ["top"]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def scan(url: str, cookies_browser: str = "", cookies_file: str = "") -> TrackScan | None:
    """Look for separate songs in a video. None if it seems to be a single piece."""
    info = with_cookie_fallback(lambda b, f: _extract(url, b, f, comments=False), cookies_browser, cookies_file)
    if info.get("_type") == "playlist":
        return None
    duration = float(info.get("duration") or 0)
    if duration < MIN_MULTI_SONG_SECONDS:
        return None

    candidates = [
        ("chapters", _from_chapters(info.get("chapters") or [])),
        ("description", parse_tracklist(info.get("description") or "", duration)),
    ]
    source, tracks = next(((s, t) for s, t in candidates if looks_like_songs(t, duration)), ("", []))
    if not tracks:
        try:
            with_comments = with_cookie_fallback(lambda b, f: _extract(url, b, f, comments=True),
                                                 cookies_browser, cookies_file)
        except Exception:
            with_comments = {}
        lists = [parse_tracklist(c.get("text") or "", duration) for c in with_comments.get("comments") or []]
        tracks = max((t for t in lists if looks_like_songs(t, duration)), key=len, default=[])
        source = "a comment"
    if not tracks:
        return None

    fix_names(tracks)
    auto_select(tracks)
    artist, album = guess_artist_album(info.get("title") or "", info.get("channel") or info.get("uploader") or "")
    artist_titles = sum(" - " in t.name for t in tracks) >= 0.6 * len(tracks)
    return TrackScan(
        url=info.get("webpage_url") or url, title=info.get("title") or url,
        channel=info.get("channel") or "", duration=duration, source=source, tracks=tracks,
        album=album, artist=artist, artist_titles=artist_titles,
    )


# ---------------------------------------------------------------- splitting

# Characters Windows forbids in file names, swapped for look-alikes the way yt-dlp does.
LOOKALIKES = str.maketrans({":": "：", "?": "？", '"': "＂", "<": "＜", ">": "＞", "|": "｜", "*": "＊",
                            "/": "⧸", "\\": "⧹"})


def safe_filename(name: str, limit: int = 150) -> str:
    name = re.sub(r"[\x00-\x1f]", "", name.translate(LOOKALIKES)).strip(" .")
    if re.fullmatch(r"(con|prn|aux|nul|com\d|lpt\d)(\..*)?", name, re.I):
        name = f"_{name}"
    return name[:limit].rstrip(" .") or "Untitled"


def split_audio(
    source: Path,
    folder: Path,
    tracks: list[dict],
    album: str,
    artist: str,
    number_files: bool,
    artist_titles: bool,
    on_progress: Callable[[int, int, str], None],
    cancel: threading.Event,
) -> list[Path]:
    """Cut source into one tagged file per track. tracks: [{"start", "end", "name"}]."""
    ffmpeg = find_ffmpeg() or "ffmpeg"
    folder.mkdir(parents=True, exist_ok=True)
    total = len(tracks)
    outputs = []
    for number, track in enumerate(tracks, start=1):
        if cancel.is_set():
            break
        name = track["name"]
        on_progress(number, total, name)
        title, track_artist = name, artist
        if artist_titles and " - " in name:
            track_artist, title = (part.strip() for part in name.split(" - ", 1))
        stem = f"{number:02d} - {name}" if number_files else name
        out = folder / f"{safe_filename(stem)}{source.suffix}"
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{track['start']:.3f}", "-i", str(source), "-t", f"{track['end'] - track['start']:.3f}",
            # Audio plus cover art only: m4a files can carry data streams the muxer
            # refuses, and the full video's chapters would be wrong inside a track.
            "-map", "0:a", "-map", "0:v?", "-c", "copy", "-map_metadata", "0", "-map_chapters", "-1",
            "-metadata", f"title={title}", "-metadata", f"album={album}",
            "-metadata", f"track={number}/{total}",
        ]
        if track_artist:
            cmd += ["-metadata", f"artist={track_artist}", "-metadata", f"album_artist={artist or track_artist}"]
        if source.suffix.lower() == ".mp3":
            cmd += ["-id3v2_version", "3"]
        elif source.suffix.lower() in (".opus", ".ogg"):
            # Ogg keeps tags on the audio stream, which -map_metadata copied from the full video.
            cmd += ["-metadata:s:a:0", f"title={title}", "-metadata:s:a:0", f"track={number}/{total}"]
        cmd.append(str(out))
        proc = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        if proc.returncode != 0:
            raise RuntimeError(f"Couldn't cut track {number} ({name}): {proc.stderr.strip()[-300:]}")
        outputs.append(out)
    return outputs
