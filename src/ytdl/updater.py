"""In-place yt-dlp updates for the frozen exe.

The exe bundles a yt-dlp release. Newer releases are downloaded from PyPI as
pure-Python wheels (yt-dlp plus the yt-dlp-ejs version it pins) and unpacked
into %APPDATA%\\ytdl\\lib\\<version>. On startup, activate() puts that folder at
the front of sys.path so it shadows the bundled copy. It must run before
anything imports yt_dlp.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ytdl"
LIB_DIR = DATA_DIR / "lib"
STATE_FILE = LIB_DIR / "state.json"
PYPI_URL = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_URL = "https://pypi.org/pypi/{name}/{version}/json"
TIMEOUT = 30


@dataclass
class ActiveVersion:
    version: str
    source: str  # "bundled" or "updated"
    bundled: str
    note: str = ""


@dataclass
class UpdateInfo:
    version: str
    wheel_url: str
    sha256: str


ACTIVE = ActiveVersion("unknown", "bundled", "unknown")


def version_key(version: str | None) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version or "0"))


def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _cleanup(keep: str | None) -> None:
    if not LIB_DIR.is_dir():
        return
    for child in LIB_DIR.iterdir():
        if child.is_dir() and child.name != keep:
            shutil.rmtree(child, ignore_errors=True)


def _forget_modules() -> None:
    for name in list(sys.modules):
        if name.split(".")[0] in ("yt_dlp", "yt_dlp_ejs"):
            del sys.modules[name]


def activate() -> ActiveVersion:
    """Use the downloaded yt-dlp if it is newer than the bundled one."""
    global ACTIVE
    try:
        bundled = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        bundled = "unknown"

    state = _read_state()
    candidate = state.get("version")
    folder = LIB_DIR / candidate if candidate else None
    if candidate and folder.is_dir() and version_key(candidate) > version_key(bundled):
        sys.path.insert(0, str(folder))
        try:
            import yt_dlp.version

            ACTIVE = ActiveVersion(yt_dlp.version.__version__, "updated", bundled)
            _cleanup(keep=candidate)
            return ACTIVE
        except Exception as exc:
            sys.path.remove(str(folder))
            _forget_modules()
            note = f"Downloaded yt-dlp {candidate} failed to load ({exc}); using the bundled version."
            _write_state({"broken": candidate})  # don't auto-install it again
            _cleanup(keep=None)
            ACTIVE = ActiveVersion(bundled, "bundled", bundled, note)
            return ACTIVE

    _cleanup(keep=None)
    ACTIVE = ActiveVersion(bundled, "bundled", bundled)
    return ACTIVE


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ytdl-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def _wheel_from(data: dict) -> UpdateInfo:
    for file in data["urls"]:
        if file["packagetype"] == "bdist_wheel" and file["filename"].endswith("-none-any.whl"):
            return UpdateInfo(data["info"]["version"], file["url"], file["digests"]["sha256"])
    raise RuntimeError(f"No pure-Python wheel published for {data['info']['name']} {data['info']['version']}")


def check_latest() -> UpdateInfo:
    return _wheel_from(_get_json(PYPI_URL.format(name="yt-dlp")))


def installed_pending() -> str | None:
    """A downloaded version that will be used after a restart, if any."""
    version = _read_state().get("version")
    if version and version_key(version) > version_key(ACTIVE.version):
        return version
    return None


def is_newer(info: UpdateInfo) -> bool:
    if info.version == _read_state().get("broken"):
        return False
    newest_local = max(ACTIVE.version, installed_pending() or "0", key=version_key)
    return version_key(info.version) > version_key(newest_local)


def _download_wheel(info: UpdateInfo) -> zipfile.ZipFile:
    req = urllib.request.Request(info.wheel_url, headers={"User-Agent": "ytdl-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read()
    if hashlib.sha256(data).hexdigest() != info.sha256:
        raise RuntimeError("Downloaded file failed its checksum; update aborted.")
    return zipfile.ZipFile(io.BytesIO(data))


def _extract(wheel: zipfile.ZipFile, dest: Path) -> None:
    for member in wheel.namelist():
        top = member.split("/")[0]
        if top.endswith(".data"):  # man pages, shell completions
            continue
        wheel.extract(member, dest)


def _pinned_ejs(wheel: zipfile.ZipFile) -> str | None:
    meta = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
    text = wheel.read(meta).decode("utf-8", "replace")
    match = re.search(r"^Requires-Dist: yt-dlp-ejs==([\w.]+).*extra == .default.", text, re.MULTILINE)
    return match.group(1) if match else None


def install(info: UpdateInfo) -> str:
    """Download and unpack a yt-dlp release. Takes effect on next start."""
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    staging = LIB_DIR / f"{info.version}.partial"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        wheel = _download_wheel(info)
        _extract(wheel, staging)
        if ejs_version := _pinned_ejs(wheel):
            ejs = _wheel_from(_get_json(PYPI_VERSION_URL.format(name="yt-dlp-ejs", version=ejs_version)))
            _extract(_download_wheel(ejs), staging)
        final = LIB_DIR / info.version
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _write_state({"version": info.version})
    return info.version


def _fresh_env() -> dict:
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"  # don't reuse this onefile's unpack dir
    return env


def restart() -> None:
    """Launch a fresh copy of the app. The caller should then close the window."""
    if getattr(sys, "frozen", False):
        args = [sys.executable, *sys.argv[1:]]
    else:
        args = [sys.executable, "-m", "ytdl"]
    subprocess.Popen(args, env=_fresh_env(), close_fds=True)


# ---------------------------------------------------------------- app updates
#
# Releases are published by .github/workflows/release.yml as YTDL.exe plus
# YTDL.exe.sha256. The running exe can't overwrite itself, so the new one is
# downloaded next to it and a small batch script swaps them after we exit.

REPO = "Mayanhunter187/ytdl"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "YTDL.exe"
CREATE_NO_WINDOW = 0x08000000


@dataclass
class AppRelease:
    version: str
    page_url: str
    asset_url: str | None
    sha256: str | None


def check_app_release() -> AppRelease | None:
    """Latest published release, or None if the repo has none yet."""
    req = urllib.request.Request(
        RELEASES_API, headers={"User-Agent": "ytdl-updater", "Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    assets = {a["name"]: a for a in data.get("assets", [])}
    exe = assets.get(ASSET_NAME)
    sha = None
    if exe and (exe.get("digest") or "").startswith("sha256:"):
        sha = exe["digest"].split(":", 1)[1]
    elif checksum := assets.get(ASSET_NAME + ".sha256"):
        with urllib.request.urlopen(checksum["browser_download_url"], timeout=TIMEOUT) as resp:
            sha = resp.read().decode().split()[0]
    return AppRelease(
        version=data["tag_name"].lstrip("v"),
        page_url=data.get("html_url", RELEASES_PAGE),
        asset_url=exe["browser_download_url"] if exe else None,
        sha256=sha,
    )


def app_is_newer(release: AppRelease) -> bool:
    from ytdl import __version__

    return version_key(release.version) > version_key(__version__)


def can_self_update() -> bool:
    return getattr(sys, "frozen", False) and sys.platform == "win32"


def download_app_update(release: AppRelease, on_progress=None) -> Path:
    """Download the new exe next to the running one. Returns its path."""
    if not (release.asset_url and release.sha256):
        raise RuntimeError("This release has no YTDL.exe with a checksum to install automatically.")
    target = Path(sys.executable).with_name("YTDL.update.exe")
    digest = hashlib.sha256()
    req = urllib.request.Request(release.asset_url, headers={"User-Agent": "ytdl-updater"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(target, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if on_progress and total:
                on_progress(done / total)
    if digest.hexdigest().lower() != release.sha256.lower():
        target.unlink(missing_ok=True)
        raise RuntimeError("The downloaded update failed its checksum and was discarded.")
    return target


def apply_app_update(new_exe: Path) -> None:
    """Swap in the new exe once this process exits, then start it.

    The caller must close the app right after this returns.
    """
    exe = Path(sys.executable)
    script = Path(os.environ.get("TEMP", exe.parent)) / "ytdl_update.cmd"
    # The onefile bootloader holds the exe open until it exits, so retry the move.
    script.write_text(
        "@echo off\r\n"
        "chcp 65001 >NUL\r\n"  # the paths below are UTF-8
        "set tries=0\r\n"
        ":retry\r\n"
        "ping -n 2 127.0.0.1 >NUL\r\n"
        f'move /Y "{new_exe}" "{exe}" >NUL 2>&1 && goto start\r\n'
        "set /a tries+=1\r\n"
        "if %tries% LSS 30 goto retry\r\n"
        ":start\r\n"
        f'start "" "{exe}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(["cmd", "/c", str(script)], env=_fresh_env(), creationflags=CREATE_NO_WINDOW, close_fds=True)
