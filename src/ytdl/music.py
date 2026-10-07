"""Work out a song's artist, title and album so downloads can be sorted into
Artist / Album folders.

YouTube only provides this for auto-generated "Topic" uploads. Everything
else is parsed from the video title ("Artist - Song (Official Video)") and the
album is looked up on MusicBrainz, a free public music database. Only the
artist and song title are sent there.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from ytdl import __version__
from ytdl.updater import DATA_DIR

SINGLES = "Singles"
CACHE_FILE = DATA_DIR / "musicbrainz_cache.json"
MB_URL = "https://musicbrainz.org/ws/2/recording/"
MB_HEADERS = {
    "User-Agent": f"YTDL/{__version__} ( https://github.com/Mayanhunter187/ytdl )",
    "Accept": "application/json",
}
MB_INTERVAL = 1.1  # MusicBrainz allows one request per second

# "(Official Music Video)", "[4K]", "| Lyrics" and similar noise in video titles.
TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(?:official|music video|video|audio|lyrics?|lyric video|visuali[sz]er|hd|hq|4k|8k|\d{3,4}p|"
    r"explicit|clean|color coded|mv|m/v)\b[^\)\]]*[\)\]]",
    re.I,
)
TITLE_TAIL = re.compile(r"\s*(?:\|{1,2}|//)\s*.*$")  # "Song | Lyrics", "Song || Album"
FEATURING = re.compile(r"\s+(?:ft\.?|feat\.?|featuring)\s+.*$", re.I)
SEPARATOR = re.compile(r"\s+[-–—]\s+")


@dataclass
class AlbumMatch:
    title: str
    year: str = ""
    kind: str = "Album"  # Album, EP, Single, Compilation, Live...


@dataclass
class Song:
    artist: str = ""
    title: str = ""
    album: str = ""
    year: str = ""
    artist_source: str = ""  # "youtube", "title" or "channel" (a guess)
    album_source: str = ""  # "youtube", "musicbrainz" or "" (unknown)
    album_options: list[AlbumMatch] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        """What stops this song from being sorted with confidence."""
        issues = []
        if not self.artist:
            issues.append("artist")
        elif self.artist_source == "channel":
            issues.append("artist_guess")
        if not self.album:
            issues.append("album")
        return issues

    def as_dict(self) -> dict:
        return {"artist": self.artist, "title": self.title, "album": self.album, "year": self.year}


# ---------------------------------------------------------------- parsing

def clean_channel(channel: str) -> str:
    channel = re.sub(r"\s*-\s*Topic$", "", channel or "")
    channel = re.sub(r"VEVO$", "", channel)
    channel = re.sub(r"\s*\b(official|music)\b\s*$", "", channel, flags=re.I)
    return channel.strip()


def clean_song_title(title: str) -> str:
    title = TITLE_NOISE.sub("", title)
    title = TITLE_TAIL.sub("", title)
    title = re.sub(r"\s+(?:official\s+)?(?:music\s+)?video\s*$", "", title, flags=re.I)
    return " ".join(title.split()).strip(" -–—\"'“”")


def split_title(title: str) -> tuple[str, str]:
    """'Pantera - Walk (Official Music Video) [4K]' -> ('Pantera', 'Walk')."""
    cleaned = clean_song_title(title)
    parts = SEPARATOR.split(cleaned, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip(" \"'“”")
    return "", cleaned


def main_artist(artist: str) -> str:
    """Folder-worthy artist: drop 'feat. X' and keep the first of a list."""
    artist = FEATURING.sub("", artist)
    return artist.split(",")[0].strip()


def is_music(info: dict) -> bool:
    return bool(info.get("track") or info.get("artist") or "Music" in (info.get("categories") or []))


# ---------------------------------------------------------------- MusicBrainz

_lock = threading.Lock()
_last_request = 0.0
_cache: dict[str, list] | None = None


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save_cache() -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(_cache), encoding="utf-8")
    except OSError:
        pass


def _lucene(text: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/&|])', r"\\\1", text)


def _norm(text: str) -> str:
    return re.sub(r"[^\w]+", " ", text.lower()).strip()


def find_albums(artist: str, title: str) -> list[AlbumMatch]:
    """Releases containing this song, best guess first (studio albums, earliest)."""
    if not artist or not title:
        return []
    query_title = re.sub(r"\s*[\(\[].*?[\)\]]", "", title).strip() or title  # drop "(2003 Remaster)"
    key = f"{_norm(artist)}|{_norm(query_title)}"
    cache = _load_cache()
    if key in cache:
        return [AlbumMatch(*m) for m in cache[key]]

    global _last_request
    query = f'recording:"{_lucene(query_title)}" AND artist:"{_lucene(artist)}"'
    url = MB_URL + "?" + urllib.parse.urlencode({"query": query, "fmt": "json", "limit": 100})
    with _lock:  # one request at a time, spaced out, across all download threads
        wait = _last_request + MB_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=MB_HEADERS), timeout=20) as resp:
                data = json.load(resp)
        finally:
            _last_request = time.monotonic()

    rank = {"Album": 0, "EP": 1, "Single": 2}
    found: dict[str, tuple] = {}
    wanted_artist = _norm(main_artist(artist))
    for rec in data.get("recordings", []):
        if rec.get("score", 0) < 85:
            continue
        credit = _norm(" ".join(c.get("name", "") for c in rec.get("artist-credit", [])))
        if wanted_artist not in credit:
            continue
        for rel in rec.get("releases", []):
            if rel.get("status") not in (None, "Official"):
                continue
            group = rel.get("release-group", {})
            secondary = group.get("secondary-types") or []
            kind = secondary[0] if secondary else group.get("primary-type") or "Other"
            order = (rank.get(kind, 3 if not secondary else 4), rel.get("date") or "9999")
            name = rel.get("title") or ""
            if name and (name.lower() not in found or order < found[name.lower()][0]):
                found[name.lower()] = (order, AlbumMatch(name, (rel.get("date") or "")[:4], kind))
    matches = [m for _o, m in sorted(found.values(), key=lambda pair: pair[0])][:12]
    cache[key] = [[m.title, m.year, m.kind] for m in matches]
    _save_cache()
    return matches


# ---------------------------------------------------------------- identify

JUNK_FROM = re.compile(r"\s*(?:--|\b(?:official|music video|video|lyrics?|audio|hd|hq|4k|remaster(?:ed)?|ai)\b).*$",
                       re.I)


def _guess_unseparated(title: str) -> tuple[str, str, list[AlbumMatch]] | None:
    """'Pantera Walk Official Music Video' has no ' - '; try the first 1-3 words as the artist."""
    words = JUNK_FROM.sub("", title).split()
    for cut in range(1, min(3, len(words) - 1) + 1):
        artist, song = " ".join(words[:cut]), " ".join(words[cut:])
        try:
            matches = find_albums(artist, song)
        except Exception:
            return None
        if matches:
            return artist, song, matches
    return None

def identify(info: dict, lookup: bool = True) -> Song:
    """Best-effort artist / title / album for a video's audio."""
    song = Song()
    if info.get("artists") or info.get("artist"):
        song.artist = main_artist((info.get("artists") or [info.get("artist")])[0])
        song.title = info.get("track") or split_title(info.get("title") or "")[1]
        song.artist_source = "youtube"
    else:
        artist, title = split_title(info.get("title") or "")
        song.title = title
        if artist:
            song.artist, song.artist_source = main_artist(artist), "title"
        elif lookup and (guess := _guess_unseparated(title)):
            song.artist, song.title, song.artist_source = guess[0], guess[1], "title"
            song.album_options = guess[2]
        elif channel := clean_channel(info.get("channel") or info.get("uploader") or ""):
            song.artist, song.artist_source = channel, "channel"

    if info.get("album"):
        song.album, song.album_source = info["album"], "youtube"
        song.year = str(info.get("release_year") or "")

    if lookup and song.artist and song.title and not song.album_options:
        try:
            song.album_options = find_albums(song.artist, song.title)
        except Exception:
            song.album_options = []
    if song.album_options:
        if song.artist_source == "channel":
            song.artist_source = "title"  # MusicBrainz knows this artist has this song
        if not song.album:
            best = song.album_options[0]
            if best.kind in ("Album", "EP"):
                song.album, song.year, song.album_source = best.title, best.year, "musicbrainz"
    return song
