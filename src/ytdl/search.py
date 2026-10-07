"""YouTube search and playlist/channel listing via yt-dlp's flat extraction."""

from __future__ import annotations

import base64
import io
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import yt_dlp
from PIL import Image

from ytdl.downloader import find_deno

THUMB_SIZE = (128, 72)


@dataclass
class SearchResult:
    title: str
    url: str
    channel: str = ""
    duration: float | None = None
    views: int | None = None
    thumbnail: str | None = None
    is_playlist: bool = False

    @property
    def meta(self) -> str:
        parts = [self.channel] if self.channel else []
        if self.is_playlist:
            parts.append("Playlist")
        elif self.duration:
            parts.append(format_duration(self.duration))
        if self.views is not None:
            parts.append(f"{format_count(self.views)} views")
        return "  ·  ".join(parts)


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def format_count(n: int) -> str:
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= threshold:
            return f"{n / threshold:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n)


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://", "www.", "youtube.com/", "youtu.be/"))


def _pick_thumbnail(entry: dict) -> str | None:
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        # Smallest one that is still at least as wide as what we display.
        wide_enough = [t for t in thumbs if (t.get("width") or 0) >= THUMB_SIZE[0]]
        return min(wide_enough or thumbs, key=lambda t: t.get("width") or 0)["url"]
    if entry.get("ie_key") == "Youtube" and entry.get("id"):
        return f"https://i.ytimg.com/vi/{entry['id']}/mqdefault.jpg"
    return None


# YouTube's "sp" search parameter is a base64 protobuf: field 1 is the sort
# order, field 2 a nested message of filters (1 = upload date, 2 = type,
# 3 = duration). Values below are the enum numbers YouTube's own UI sends.
SORT_OPTIONS = {"Relevance": 0, "Upload date": 2, "View count": 3, "Rating": 1}
DATE_OPTIONS = {"Any time": 0, "Last hour": 1, "Today": 2, "This week": 3, "This month": 4, "This year": 5}
TYPE_OPTIONS = {"Videos": 1, "Playlists": 3, "Channels": 2, "All": 0}
DURATION_OPTIONS = {"Any length": 0, "Under 4 min": 1, "4–20 min": 3, "Over 20 min": 2}


@dataclass
class SearchFilters:
    sort: str = "Relevance"
    date: str = "Any time"
    type: str = "Videos"
    duration: str = "Any length"

    def sp(self) -> str:
        inner = b""
        for field_no, value in (
            (1, DATE_OPTIONS[self.date]),
            (2, TYPE_OPTIONS[self.type]),
            (3, DURATION_OPTIONS[self.duration]),
        ):
            if value:
                inner += bytes([field_no << 3, value])
        raw = b""
        if sort := SORT_OPTIONS[self.sort]:
            raw += bytes([0x08, sort])
        if inner:
            raw += bytes([0x12, len(inner)]) + inner
        return base64.b64encode(raw).decode()


def search_target(query: str, filters: SearchFilters) -> str:
    query = query.strip()
    if is_url(query):
        return query if query.startswith("http") else f"https://{query}"
    params = {"search_query": query}
    if sp := filters.sp():
        params["sp"] = sp
    return "https://www.youtube.com/results?" + urllib.parse.urlencode(params)


@dataclass
class SearchPage:
    next_start: int
    has_more: bool
    results: list[SearchResult] = field(default_factory=list)


def search(query: str, filters: SearchFilters | None = None, start: int = 1, count: int = 20) -> SearchPage:
    """Search YouTube, or list the videos of a pasted playlist/channel/video link.

    start is 1-based, so passing len(results) + 1 fetches the next page.
    Live and upcoming streams are left out.
    """
    target = search_target(query, filters or SearchFilters())

    opts: dict = {
        "extract_flat": "in_playlist",
        "playliststart": start,
        "playlistend": start + count - 1,
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if deno := find_deno():
        opts["js_runtimes"] = {"deno": {"path": deno}}

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)

    entries = info.get("entries")
    if entries is None:  # a single video link
        entries = [info]
    entries = list(entries)
    page = SearchPage(next_start=start + len(entries), has_more=len(entries) >= count and "entries" in info)

    results = page.results
    for entry in entries:
        if not entry or entry.get("live_status") in ("is_live", "is_upcoming"):
            continue
        url = entry.get("webpage_url") or entry.get("url")
        if not url:
            continue
        if not url.startswith("http") and entry.get("id"):
            url = f"https://www.youtube.com/watch?v={entry['id']}"
        results.append(
            SearchResult(
                title=entry.get("title") or url,
                url=url,
                channel=entry.get("channel") or entry.get("uploader") or "",
                duration=entry.get("duration"),
                views=entry.get("view_count"),
                thumbnail=_pick_thumbnail(entry),
                is_playlist=entry.get("ie_key") == "YoutubeTab" or entry.get("_type") == "playlist",
            )
        )
    return page


def fetch_thumbnail(url: str) -> Image.Image:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    # Center-crop to 16:9 so letterboxed thumbnails line up, then shrink.
    w, h = img.size
    target_h = int(w * THUMB_SIZE[1] / THUMB_SIZE[0])
    if target_h < h:
        top = (h - target_h) // 2
        img = img.crop((0, top, w, top + target_h))
    return img.resize(THUMB_SIZE, Image.LANCZOS)
