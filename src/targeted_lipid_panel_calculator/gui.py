"""Minimal tkinter GUI so the packaged app is double-click friendly.

No console required: the user picks a folder with a native dialog and gets a
popup summarising what was written (or what went wrong).
"""

from __future__ import annotations

from pathlib import Path

from targeted_lipid_panel_calculator.calculator import InputError, run_on_directory

APP_TITLE = "Targeted Lipid Panel Calculator"
_PROMPT = (
    "Select the folder containing results.csv, reference_compounds.csv "
    "and a config*.csv file"
)


def tk_available() -> bool:
    """True if a usable tkinter (with the _tkinter C extension) is importable."""
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means no GUI
        return False
    return True


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
            f"Output: {summary.output_path}",
            f"Report: {summary.report_path}",
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
