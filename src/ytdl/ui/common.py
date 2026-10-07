"""Small helpers shared by the tabs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import customtkinter as ctk

# Outline style for secondary actions.
SECONDARY = {"fg_color": "transparent", "border_width": 1, "text_color": ("gray10", "gray90")}
DANGER = {"fg_color": "#9b2c2c", "hover_color": "#7a2222"}
MUTED = ("gray35", "gray65")
CARD = ("gray90", "gray17")

STATUS_COLORS = {
    "Queued": ("gray35", "gray65"),
    "Downloading": ("#1f6aa5", "#5aa9e6"),
    "Done": ("#2f7d32", "#6cc070"),
    "Failed": ("#b3261e", "#f2867c"),
    "Cancelled": ("gray35", "gray65"),
    "Skipped": ("#9a6700", "#e3b341"),
}

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[3]))
    return base / "assets" / name


def fmt_size(n: float | None) -> str:
    if not n:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def open_path(path: Path) -> None:
    os.startfile(path)


def reveal(path: Path) -> None:
    """Open Explorer with the file selected (or the folder, if the file is gone)."""
    if path.exists() and path.is_file():
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        folder = path if path.is_dir() else path.parent
        if folder.is_dir():
            os.startfile(folder)


def section_label(master, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, anchor="w", font=ctk.CTkFont(size=15, weight="bold"))


def clear_children(frame) -> None:
    for child in frame.winfo_children():
        child.destroy()
