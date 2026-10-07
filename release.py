"""Publish a new YTDL version.

Usage:
    uv run python release.py 1.4.0

Sets the version, commits, tags v1.4.0 and pushes. GitHub Actions then builds
YTDL.exe and publishes it as a GitHub Release, which installed copies of the
app pick up through About → Update now.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INIT = ROOT / "src" / "ytdl" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else ""


def main() -> None:
    if len(sys.argv) != 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit("Usage: uv run python release.py X.Y.Z")
    version = sys.argv[1]
    tag = f"v{version}"

    if run("git", "status", "--porcelain", capture=True):
        sys.exit("Commit or stash your changes first; the working tree must be clean.")
    if run("git", "tag", "--list", tag, capture=True):
        sys.exit(f"Tag {tag} already exists.")

    current = re.search(r'__version__ = "([^"]+)"', INIT.read_text(encoding="utf-8")).group(1)
    if current != version:
        INIT.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
        text = PYPROJECT.read_text(encoding="utf-8")
        PYPROJECT.write_text(re.sub(r'(?m)^version = "[^"]+"', f'version = "{version}"', text, count=1),
                             encoding="utf-8")
        run("uv", "lock")
        run("git", "add", str(INIT), str(PYPROJECT), "uv.lock")
        run("git", "commit", "-m", f"Release {tag}")

    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True)
    run("git", "tag", "-a", tag, "-m", f"YTDL {version}")
    run("git", "push", "origin", branch, tag)
    print(f"Pushed {tag}. GitHub Actions is building the release:")
    print("  https://github.com/Mayanhunter187/ytdl/actions")


if __name__ == "__main__":
    main()
