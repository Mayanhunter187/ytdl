"""Build dist/YTDL.exe with PyInstaller.

Usage:
    uv run python build.py            # single-file exe (default)
    uv run python build.py --onedir   # folder build; starts faster, same features
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import deno
import imageio_ffmpeg
import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / "build" / "bin"
VERSION_FILE = ROOT / "build" / "version_info.txt"
STDLIB_FOR_UPDATES = [
    "asyncio", "bz2", "concurrent.futures", "ctypes", "email.utils", "hmac", "http.cookiejar",
    "http.cookies", "lzma", "secrets", "sqlite3", "unicodedata", "uuid", "xml.dom.minidom",
    "xml.etree.ElementTree", "zipimport",
]


def app_version() -> str:
    text = (ROOT / "src" / "ytdl" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def stage_binaries() -> None:
    """Copy ffmpeg and deno under fixed names; the app looks for bin/<name>.exe."""
    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), STAGE / "ffmpeg.exe")
    shutil.copy2(deno.find_deno_bin(), STAGE / "deno.exe")


def write_version_info() -> Path:
    """Windows file properties. FileDescription is also the name toasts show."""
    version = app_version()
    parts = tuple(int(n) for n in re.findall(r"\d+", version)[:4])
    parts += (0,) * (4 - len(parts))
    strings = {
        "CompanyName": "Mayanhunter187",
        "FileDescription": "YTDL",
        "FileVersion": version,
        "InternalName": "YTDL",
        "OriginalFilename": "YTDL.exe",
        "ProductName": "YTDL YouTube Downloader",
        "ProductVersion": version,
    }
    entries = ",\n            ".join(f"StringStruct({k!r}, {v!r})" for k, v in strings.items())
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts}, prodvers={parts}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
            {entries}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    return VERSION_FILE


def pyinstaller_args(
    script: Path, name: str, onedir: bool = False, windowed: bool = True, splash: bool = True
) -> list[str]:
    sep = ";" if sys.platform == "win32" else ":"
    args = [
        str(script),
        "--name", name,
        "--onedir" if onedir else "--onefile",
        "--windowed" if windowed else "--console",
        "--noconfirm",
        "--clean",
        "--icon", str(ROOT / "assets" / "icon.ico"),
        "--version-file", str(write_version_info()),
        "--paths", str(ROOT / "src"),
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--add-binary", f"{STAGE / 'ffmpeg.exe'}{sep}bin",
        "--add-binary", f"{STAGE / 'deno.exe'}{sep}bin",
        "--collect-data", "customtkinter",
        # The updater compares the bundled yt-dlp version against PyPI.
        "--copy-metadata", "yt-dlp",
        # Stdlib modules that newer yt-dlp releases (loaded from %APPDATA% by
        # the updater) might import even though the bundled one doesn't.
        *[arg for mod in STDLIB_FOR_UPDATES for arg in ("--hidden-import", mod)],
        # The binaries above replace these packages at runtime.
        "--exclude-module", "imageio_ffmpeg",
        "--exclude-module", "deno",
    ]
    if splash:
        # Shown while the onefile exe unpacks; the app closes it once its window is ready.
        args += ["--splash", str(ROOT / "assets" / "splash.png")]
    return args


def main() -> None:
    onedir = "--onedir" in sys.argv[1:]
    stage_binaries()
    PyInstaller.__main__.run(pyinstaller_args(ROOT / "run.py", "YTDL", onedir=onedir))
    target = ROOT / "dist" / ("YTDL/YTDL.exe" if onedir else "YTDL.exe")
    print(f"\nBuilt {target} (version {app_version()})")


if __name__ == "__main__":
    main()
