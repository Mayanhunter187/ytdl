# YTDL: YouTube Downloader

A Windows desktop app for downloading YouTube videos and audio to your computer.
Built with [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter),
and shipped as a single `YTDL.exe`. Nothing else needs to be installed.

**[Download the latest YTDL.exe](https://github.com/Mayanhunter187/ytdl/releases/latest)**

## Features

**Finding videos**
- **Search** with filters for type (videos, playlists, channels), upload date, length and sort order. **Load more** pages through results.
- **Browse** a playlist or channel by pasting its link into the search box, then pick individual videos.
- **Copy a YouTube link** anywhere and YTDL offers to download it. You can also **drag a link** (or a browser tab's address, or a `.url` shortcut) onto the window.
- Live streams are left out of results and skipped if downloaded by link.

**Downloading**
- **Video** as MP4 (Best down to 360p), or **audio** as MP3, M4A, Opus or WAV with title metadata and cover art.
- **Choose the exact format**: every resolution and codec with its estimated size, or a specific audio bitrate.
- **Split by chapters**: albums and long mixes become one file per chapter, in a folder named after the video.
- **File names your way**: presets like *Channel folder / Title*, or any yt-dlp template, with a live preview.
- **Sign-in cookies** (Firefox, Chrome, Edge… or a `cookies.txt` file) for age-restricted and members-only videos.

**Managing downloads**
- **Downloads page**: per-item progress, reorder, cancel, retry, open / show in Explorer. Run 1–4 downloads at the same time.
- **Queue survives restarts**: close the app mid-download and it picks up where it left off next time.
- **History** of everything downloaded. Videos you already have are skipped, so re-downloading a playlist only gets new videos.
- **Windows notification** when the queue finishes while YTDL is in the background.

**Staying up to date**
- **yt-dlp updates itself** from PyPI and applies on the next start, so YouTube changes don't break the exe.
- **YTDL updates itself** from this repo's GitHub Releases: About → *Update now* downloads the new exe, verifies its SHA-256 and restarts into it.
- The **About** page shows the versions of the app, yt-dlp, yt-dlp-ejs, ffmpeg and deno.

## Using it

Run `YTDL.exe`. A splash screen shows while it unpacks (a few seconds).

| Tab | What it's for |
| --- | --- |
| Search | Find videos. Each result has **Formats**, **Video** and **Audio** buttons |
| Link | Paste a link you already have, or **Choose format…** for it |
| Downloads | Progress and queue management. **Show log** shows yt-dlp's output |
| History | Past downloads |
| Settings | Quality, formats, folder, file names, chapters, cookies, clipboard, notifications, theme |
| About | Versions and updates |

Files go to `Downloads\YTDL` by default. Settings, history, the queue and yt-dlp updates live in `%APPDATA%\ytdl`.

Windows SmartScreen may warn the first time because the exe isn't code-signed. Click *More info → Run anyway*.

## Releasing a new version

```bat
uv run python release.py 1.4.0
```

This sets the version, commits, tags `v1.4.0` and pushes. The [Build workflow](.github/workflows/build.yml) then
builds `YTDL.exe` with the newest yt-dlp and publishes it with its checksum as a GitHub Release. Everyone's app
offers the update from About.

Every push and pull request also builds the exe and attaches it to the workflow run, without publishing a release.

## Building locally

Requirements: Windows and [uv](https://docs.astral.sh/uv/). uv downloads Python for you.

```bat
build.bat
```

This writes `dist\YTDL.exe`. `build.bat --onedir` builds a folder version (`dist\YTDL\YTDL.exe`) that starts faster.

## Running from source

```bat
uv sync
uv run ytdl
```

## How the updates work

- **yt-dlp**: `ytdl/__main__.py` calls `updater.activate()` before anything imports yt-dlp. If `%APPDATA%\ytdl\lib\<version>`
  holds a newer release than the bundled one, it goes to the front of `sys.path`. Updates are the official pure-Python
  wheels from PyPI (yt-dlp plus the yt-dlp-ejs version it pins), checked against PyPI's SHA-256. If one fails to load,
  the app falls back to the bundled copy and won't install that release again.
- **The app**: the running exe can't overwrite itself, so the new one is downloaded next to it as `YTDL.update.exe`
  and checked against the release's SHA-256. A small script then swaps it in after the app exits and starts it.
  If the exe sits somewhere without write access, *Update now* opens the release page instead.

## Project layout

| Path | Purpose |
| --- | --- |
| `src/ytdl/__main__.py` | Entry point: activates yt-dlp updates, then starts the UI |
| `src/ytdl/downloader.py` | yt-dlp wrapper: formats, templates, chapters, cookies, live/duplicate skipping |
| `src/ytdl/formats.py` | Format list for the format picker |
| `src/ytdl/search.py` | Search with filters and paging, playlist/channel listing, thumbnails |
| `src/ytdl/jobs.py` | Download queue: concurrency, reorder, cancel, retry, persistence |
| `src/ytdl/history.py` | Download history (`history.json`) |
| `src/ytdl/updater.py` | yt-dlp updates (PyPI) and app updates (GitHub Releases) |
| `src/ytdl/ui/` | Main window, one module per tab, format dialog, link banner, notifications |
| `build.py` / `build.bat` | Build script (PyInstaller, splash, version info) |
| `release.py` | Tag and push a release |
| `.github/workflows/build.yml` | CI build and release publishing |

## Note

Only download content you have the right to download, and follow YouTube's Terms of Service.
