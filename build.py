#!/usr/bin/env python3
"""Build Quick Camera Profile into a standalone .exe using PyInstaller.

Usage:
    python build.py          # one-file .exe
    python build.py --dir    # one-dir bundle (recommended for installer)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_scanin() -> str:
    found = shutil.which("scanin")
    if found:
        return found
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for d in (Path(local) / "Microsoft/WinGet/Packages").glob("GraemeGill.ArgyllCMS*"):
                for exe in d.rglob("scanin.exe"):
                    return str(exe)
    raise FileNotFoundError("scanin not found. Install Argyll CMS.")


def find_argyll_ref() -> str:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            for d in (Path(local) / "Microsoft/WinGet/Packages").glob("GraemeGill.ArgyllCMS*"):
                for r in d.rglob("ref"):
                    if (r / "ColorChecker.cht").is_file():
                        return str(r)
    s = shutil.which("scanin")
    if s:
        r = Path(s).resolve().parent.parent / "ref"
        if (r / "ColorChecker.cht").is_file():
            return str(r)
    raise FileNotFoundError("Argyll CMS ref/ directory not found.")


def find_dcamprof() -> str:
    # First try bundled-in-monorepo location
    root = Path(__file__).resolve().parent
    candidate = root.parent / "bin" / "dcamprof.exe"
    if candidate.is_file():
        return str(candidate)
    # Fallback to PATH
    found = shutil.which("dcamprof") or shutil.which("dcamprof.exe")
    if found:
        return found
    raise FileNotFoundError("dcamprof not found. Put dcamprof.exe in ../bin or PATH.")


def main():
    parser = argparse.ArgumentParser(description="Build Quick Camera Profile")
    parser.add_argument("--dir", action="store_true", help="Create one-directory bundle")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    entry = project_root / "main.py"

    dcamprof = find_dcamprof()
    scanin = find_scanin()
    argyll_ref = find_argyll_ref()
    sep = ";" if os.name == "nt" else ":"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name", "QuickProfile",
        "--windowed",
        "--icon", str(project_root / "assets" / "icon.ico"),
        "--onefile" if not args.dir else "--onedir",
        "--add-data", f"{dcamprof}{sep}bin",
        "--add-data", f"{scanin}{sep}argyll",
        "--add-data", f"{argyll_ref}{sep}argyll/ref",
        "--collect-all", "customtkinter",
        "--hidden-import", "rawpy",
        "--hidden-import", "rawpy._rawpy",
        "--hidden-import", "tifffile",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL._tkinter_finder",
        str(entry),
    ]

    subprocess.run(cmd, check=True, cwd=str(project_root))
    print("Build complete.")


if __name__ == "__main__":
    main()
