"""Command-line / double-click entry point.

Usage:
    * No argument  -> opens a folder-picker GUI (the double-click experience).
    * <directory>  -> headless run on that folder, prints a summary.

Either way it reads results.csv, reference_compounds.csv and a config*.csv
from the folder and writes ``<folder>/outputs/output.csv`` and
``<folder>/outputs/report.csv``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from targeted_lipid_panel_calculator.calculator import InputError, run_on_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="targeted-lipid-panel-calculator",
        description=(
            "Append 'Internal Standard (y/n)' and 'nmol/mL' columns to "
            "results.csv. Reads the input files from a folder and writes an "
            "'outputs' subfolder. Run with no argument to pick a folder via a "
            "dialog."
        ),
    )
    parser.add_argument(
        "directory",
        type=Path,
        nargs="?",
        default=None,
        help="Folder containing the input CSVs. Omit to open a folder picker.",
    )
    return parser


def _prompt_for_directory() -> Path | None:
    """Console fallback when no Tk GUI is available."""
    try:
        raw = input(
            "Enter the path to the folder containing the CSV files "
            "(or press Enter to cancel): "
        ).strip()
    except EOFError:
        return None
    raw = raw.strip().strip('"').strip("'")
    return Path(raw) if raw else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    directory = args.directory
    if directory is None:
        from targeted_lipid_panel_calculator.gui import run_gui, tk_available

        if tk_available():
            return run_gui()
        # No GUI toolkit (e.g. a console-only Python build): ask on the console.
        directory = _prompt_for_directory()
        if directory is None:
            print("No folder provided; nothing to do.")
            return 0

    try:
        summary = run_on_directory(directory)
    except InputError as exc:
        print(f"Error: {exc}")
        return 1

    print(f"Processed {summary.row_count} rows using {summary.config_name}.")
    print(f"Output: {summary.output_path}")
    print(f"Report: {summary.report_path}")
    if summary.unmatched_count:
        print(
            f"{summary.unmatched_count} row(s) could not be fully matched "
            "- see the report."
        )
    else:
        print("All rows matched.")
    return 0
