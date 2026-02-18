#!/usr/bin/env python3
"""Build script – packages the Kalshi BTC Bot into a single Windows .exe.

Usage:
    python build_exe.py            # one-file build (smaller, slower startup)
    python build_exe.py --onedir   # one-dir build (larger, faster startup)

Requirements:
    pip install pyinstaller PySide6
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).parent / "src"
ENTRY = SRC / "app_gui" / "main.py"
NAME = "KalshiBtcBot"


def build(onedir: bool = False) -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", NAME,
        "--paths", str(SRC),
        "--add-data", f"{SRC / 'bot'}:bot",
        "--add-data", f"{SRC / 'app_gui'}:app_gui",
        "--hidden-import", "bot",
        "--hidden-import", "bot.cli",
        "--hidden-import", "bot.config",
        "--hidden-import", "bot.main",
        "--hidden-import", "bot.infra",
        "--hidden-import", "bot.kalshi",
        "--hidden-import", "bot.pricing",
        "--hidden-import", "bot.strategy",
        "--hidden-import", "bot.sim",
        "--hidden-import", "app_gui",
        "--hidden-import", "PySide6",
        "--collect-all", "PySide6",
    ]

    if onedir:
        cmd.append("--onedir")
    else:
        cmd.append("--onefile")

    if sys.platform == "win32":
        cmd.append("--windowed")

    cmd.append(str(ENTRY))

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"\nBuild complete. Output in dist/{NAME}" + (".exe" if not onedir else "/"))


if __name__ == "__main__":
    onedir = "--onedir" in sys.argv
    build(onedir=onedir)
