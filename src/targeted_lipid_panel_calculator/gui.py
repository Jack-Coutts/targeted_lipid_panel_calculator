"""Minimal tkinter GUI so the packaged app is double-click friendly.

No console required: the user picks a folder with a native dialog and gets a
popup summarising what was written (or what went wrong).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from targeted_lipid_panel_calculator.calculator import InputError, run_on_directory

APP_TITLE = "Targeted Lipid Panel Calculator"
_PROMPT = (
    "Select the folder containing results.csv, reference_compounds*.csv "
    "and a config*.csv file"
)


def tk_available() -> bool:
    """True if a usable tkinter (with the _tkinter C extension) is importable."""
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means no GUI
        return False
    return True


def macos_dialog_available() -> bool:
    """True if macOS's built-in AppleScript dialog tool can be used."""
    return sys.platform == "darwin" and Path("/usr/bin/osascript").is_file()


def _quote_applescript(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _show_macos_message(title: str, message: str, icon: str) -> None:
    script = (
        f"display dialog {_quote_applescript(message)} "
        f"with title {_quote_applescript(title)} "
        'buttons {"OK"} default button "OK" '
        f"with icon {icon}"
    )
    subprocess.run(["/usr/bin/osascript", "-e", script], check=False)


def run_macos_dialog() -> int:
    """Native macOS fallback for builds made with a Python that lacks tkinter."""
    choose_folder = (
        f"POSIX path of (choose folder with prompt {_quote_applescript(_PROMPT)})"
    )
    proc = subprocess.run(
        ["/usr/bin/osascript", "-e", choose_folder],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0  # user cancelled

    try:
        summary = run_on_directory(Path(proc.stdout.strip()))
    except InputError as exc:
        _show_macos_message(APP_TITLE, str(exc), "stop")
        return 1
    except Exception as exc:  # noqa: BLE001 - surface any failure to the user
        _show_macos_message(APP_TITLE, f"Unexpected error:\n\n{exc}", "stop")
        return 1

    lines = [
        f"Processed {summary.row_count} rows using {summary.config_name}.",
        "",
        f"Areas:   {summary.areas_path}",
        f"nmol/mL: {summary.nmol_path}",
        f"Report:  {summary.report_path}",
    ]
    if summary.unmatched_count:
        lines += [
            "",
            f"{summary.unmatched_count} row(s) could not be fully matched "
            "- see the report for details.",
        ]
    else:
        lines += ["", "All rows matched."]
    _show_macos_message(APP_TITLE, "\n".join(lines), "note")
    return 0


def run_gui() -> int:
    # Imported lazily so headless environments (CI, tests) can import the
    # package without a display / Tk available.
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()

    try:
        directory = filedialog.askdirectory(title=_PROMPT, mustexist=True)
        if not directory:
            return 0  # user cancelled

        try:
            summary = run_on_directory(Path(directory))
        except InputError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return 1
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            messagebox.showerror(APP_TITLE, f"Unexpected error:\n\n{exc}")
            return 1

        lines = [
            f"Processed {summary.row_count} rows using {summary.config_name}.",
            "",
            f"Areas:   {summary.areas_path}",
            f"nmol/mL: {summary.nmol_path}",
            f"Report:  {summary.report_path}",
        ]
        if summary.unmatched_count:
            lines += [
                "",
                f"{summary.unmatched_count} row(s) could not be fully matched "
                "- see the report for details.",
            ]
        else:
            lines += ["", "All rows matched."]
        messagebox.showinfo(APP_TITLE, "\n".join(lines))
        return 0
    finally:
        root.destroy()
