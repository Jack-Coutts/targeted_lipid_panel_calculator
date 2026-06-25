"""Build a standalone, double-clickable executable with PyInstaller.

Run on the OS you want to target (PyInstaller cannot cross-compile):

    uv run python build.py

Outputs:
    * Windows: dist/TargetedLipidPanelCalculator.exe
    * macOS:   dist/TargetedLipidPanelCalculator.app (and a CLI binary)
    * Linux:   dist/TargetedLipidPanelCalculator
"""

from __future__ import annotations

import sys

import PyInstaller.__main__

APP_NAME = "TargetedLipidPanelCalculator"


def main() -> int:
    args = [
        "app.py",
        "--name",
        APP_NAME,
        # No console window: the app talks to the user via the tkinter dialogs.
        "--windowed",
        "--noconfirm",
        "--clean",
    ]
    # On macOS a windowed build is delivered as a .app bundle, which must be
    # onedir. On Windows/Linux a single onefile binary is the nicest to share.
    if sys.platform != "darwin":
        args.append("--onefile")

    PyInstaller.__main__.run(args)
    print(f"\nBuilt {APP_NAME} into ./dist", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
