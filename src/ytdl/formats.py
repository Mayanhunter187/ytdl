"""List a video's formats as simple choices for the format picker."""

from __future__ import annotations

from dataclasses import dataclass, field

import yt_dlp

from ytdl.downloader import base_opts, with_cookie_fallback

CODEC_NAMES = {"avc1": "H.264", "vp09": "VP9", "vp9": "VP9", "av01": "AV1", "mp4a": "AAC", "opus": "Opus"}


@dataclass
class FormatChoice:
    label: str  # "1080p60"
    detail: str  # "H.264  ·  4.2 Mbps"
    size: int | None
    spec: str  # yt-dlp format selector
    audio_only: bool


@dataclass
class FormatList:
    title: str
    url: str
    duration: float | None
    video: list[FormatChoice] = field(default_factory=list)
    audio: list[FormatChoice] = field(default_factory=list)


def _codec(name: str | None) -> str:
    if not name or name == "none":
        return ""
    return CODEC_NAMES.get(name.split(".")[0], name.split(".")[0])


def _size(fmt: dict, duration: float | None) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if not size and duration and fmt.get("tbr"):
        size = int(fmt["tbr"] * 1000 / 8 * duration)
    return size


def _extract(url: str, cookies_browser: str, cookies_file: str) -> dict:
    opts = base_opts(cookies_browser, cookies_file)
    opts["noplaylist"] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def list_formats(url: str, cookies_browser: str = "", cookies_file: str = "") -> FormatList:
    info = with_cookie_fallback(lambda b, f: _extract(url, b, f), cookies_browser, cookies_file)
    if info.get("_type") == "playlist":
        raise ValueError("That's a playlist. Pick formats for a single video instead.")

    duration = info.get("duration")
    usable = [
        f for f in info.get("formats", [])
        if f.get("protocol", "").startswith("http") and "drc" not in f.get("format_id", "")
        and f.get("format_note") != "storyboard"
    ]
    audio = [f for f in usable if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    video = [f for f in usable if f.get("vcodec") not in (None, "none") and f.get("height")]

    best_m4a = max((f for f in audio if f.get("ext") == "m4a"), key=lambda f: f.get("abr") or 0, default=None)
    best_any = max(audio, key=lambda f: f.get("abr") or 0, default=None)

    result = FormatList(info.get("title", url), info.get("webpage_url", url), duration)

    # One choice per resolution/fps/codec, keeping the highest bitrate.
    best: dict[tuple, dict] = {}
    for f in video:
        key = (f["height"], round(f.get("fps") or 0), _codec(f.get("vcodec")))
        if key not in best or (f.get("tbr") or 0) > (best[key].get("tbr") or 0):
            best[key] = f
    codec_rank = {"H.264": 2, "VP9": 1}  # most compatible first within a resolution
    for (height, fps, codec), f in sorted(
        best.items(), key=lambda kv: (kv[0][0], kv[0][1], codec_rank.get(kv[0][2], 0)), reverse=True
    ):
        has_audio = f.get("acodec") not in (None, "none")
        pair = best_m4a if codec == "H.264" and best_m4a else best_any
        if has_audio or pair is None:
            spec, size = f["format_id"], _size(f, duration)
        else:
            spec = f"{f['format_id']}+{pair['format_id']}"
            v, a = _size(f, duration), _size(pair, duration)
            size = v + a if v and a else v
        label = f"{height}p{fps if fps > 30 else ''}"
        tbr = f.get("tbr")
        detail = "  ·  ".join(x for x in (codec, f"{tbr / 1000:.1f} Mbps" if tbr else "") if x)
        result.video.append(FormatChoice(label, detail, size, spec, audio_only=False))

    for f in sorted(audio, key=lambda f: f.get("abr") or 0, reverse=True):
        abr = f.get("abr")
        label = f"{abr:.0f} kbps" if abr else f["format_id"]
        detail = "  ·  ".join(x for x in (_codec(f.get("acodec")), f.get("ext", "")) if x)
        result.audio.append(FormatChoice(label, detail, _size(f, duration), f["format_id"], audio_only=True))

    return result
