"""Core logic for the targeted lipid panel calculator.

Reads three CSV inputs (results, config, reference compounds) and writes an
output CSV that is the original results table with two appended columns:

    * ``Internal Standard (y/n)``
    * ``nmol/mL``

See the module-level :func:`calculate` for the full algorithm.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

INTERNAL_STANDARD_COLUMN = "Internal Standard (y/n)"
CONCENTRATION_COLUMN = "nmol/mL"

# Column names expected in each input file.
RESULTS_NAME = "Name"
RESULTS_AREA = "Area"

# File names looked for inside the input directory.
RESULTS_FILENAME = "results.csv"
REFERENCE_GLOB = "reference_compounds*.csv"
CONFIG_GLOB = "config*.csv"
OUTPUT_DIRNAME = "outputs"
OUTPUT_FILENAME = "output.csv"
REPORT_FILENAME = "report.csv"
REFERENCE_NAME = "Compound name"
REFERENCE_ISTD = "ISTD Compound"
REFERENCE_RF = "Response Factor"
CONFIG_NAME = "Name"
CONFIG_CONCENTRATION = "uM or nmol/mL"

_IS_MARKER = re.compile(r"\(\s*is\s*\)", re.IGNORECASE)
_REFERENCE_MARKER = re.compile(r"\[\s*reference\s*\]", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Collapse the different naming styles used across the three files.

    The files disagree on spacing, ``_`` vs space inside acyl tails, an
    ``(IS)`` suffix on standards, and a ``[reference]`` marker. Stripping all of
    those yields a deterministic key that lines the files up, e.g. both
    ``PC (15:0_18:1) d7 (IS)`` and ``PC(15:0_18:1) d7`` map to ``pc(15:018:1)d7``.
    """
    s = name.lower()
    s = _REFERENCE_MARKER.sub(" ", s)
    s = _IS_MARKER.sub(" ", s)
    s = s.replace("_", "")
    s = _WHITESPACE.sub("", s)
    return s


def is_marked_internal_standard(name: str) -> bool:
    """True if the raw name carries an explicit ``(IS)`` marker."""
    return bool(_IS_MARKER.search(name))


class InputError(Exception):
    """Raised when the input directory is missing or has ambiguous files."""


@dataclass
class DiscoveredInputs:
    results: Path
    config: Path
    reference: Path


@dataclass
class RunSummary:
    output_path: Path
    report_path: Path
    row_count: int
    unmatched_count: int
    config_name: str


@dataclass
class ReferenceEntry:
    istd_raw: str
    istd_norm: str
    response_factor: float | None


@dataclass
class CalculationResult:
    fieldnames: list[str]
    rows: list[dict[str, str]]
    unmatched: list[dict[str, str]] = field(default_factory=list)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    # utf-8-sig strips the BOM present at the start of each input file.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = [row for row in reader]
    return list(fieldnames), rows


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_reference(path: Path) -> tuple[dict[str, ReferenceEntry], set[str]]:
    """Map normalized compound name -> reference entry, plus the set of all
    reference compound names (membership = "this is a measured compound")."""
    _, rows = _read_csv(path)
    by_name: dict[str, ReferenceEntry] = {}
    names: set[str] = set()
    for row in rows:
        raw = (row.get(REFERENCE_NAME) or "").strip()
        if not raw:
            continue
        norm = normalize_name(raw)
        names.add(norm)
        istd_raw = (row.get(REFERENCE_ISTD) or "").strip()
        by_name[norm] = ReferenceEntry(
            istd_raw=istd_raw,
            istd_norm=normalize_name(istd_raw),
            response_factor=_to_float(row.get(REFERENCE_RF)),
        )
    return by_name, names


def load_config(path: Path) -> dict[str, float]:
    """Map normalized compound name -> concentration (uM or nmol/mL)."""
    _, rows = _read_csv(path)
    conc: dict[str, float] = {}
    for row in rows:
        raw = (row.get(CONFIG_NAME) or "").strip()
        if not raw:
            continue
        value = _to_float(row.get(CONFIG_CONCENTRATION))
        if value is not None:
            conc[normalize_name(raw)] = value
    return conc


def load_results(
    path: Path,
) -> tuple[list[str], list[dict[str, str]], dict[str, float]]:
    """Return the original results (fieldnames + rows) and a map of normalized
    name -> area, keeping the first occurrence when a name is duplicated."""
    fieldnames, rows = _read_csv(path)
    area_by_name: dict[str, float] = {}
    for row in rows:
        raw = (row.get(RESULTS_NAME) or "").strip()
        if not raw:
            continue
        norm = normalize_name(raw)
        if norm in area_by_name:
            continue  # duplicate: keep first occurrence
        area = _to_float(row.get(RESULTS_AREA))
        if area is not None:
            area_by_name[norm] = area
    return fieldnames, rows, area_by_name


def calculate(
    results_path: Path,
    config_path: Path,
    reference_path: Path,
) -> CalculationResult:
    """Run the full calculation and return rows ready to be written out."""
    reference, reference_names = load_reference(reference_path)
    config = load_config(config_path)
    fieldnames, rows, area_by_name = load_results(results_path)

    # Source files often have trailing commas, producing empty-named columns;
    # drop those so the output is clean.
    out_fields = [name for name in fieldnames if name and name.strip()]
    for col in (INTERNAL_STANDARD_COLUMN, CONCENTRATION_COLUMN):
        if col not in out_fields:
            out_fields.append(col)

    out_rows: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []

    def flag_unmatched(name: str, role: str, issue: str) -> None:
        unmatched.append({"Name": name, "Role": role, "Issue": issue})

    for row in rows:
        raw_name = (row.get(RESULTS_NAME) or "").strip()
        new_row = dict(row)
        norm = normalize_name(raw_name)

        is_internal_standard = is_marked_internal_standard(raw_name) or (
            norm not in reference_names
        )

        if is_internal_standard:
            new_row[INTERNAL_STANDARD_COLUMN] = "y"
            conc = config.get(norm)
            if conc is None:
                new_row[CONCENTRATION_COLUMN] = ""
                flag_unmatched(
                    raw_name,
                    "internal standard",
                    "no matching concentration in config file",
                )
            else:
                new_row[CONCENTRATION_COLUMN] = _format_number(conc)
        else:
            new_row[INTERNAL_STANDARD_COLUMN] = "n"
            entry = reference[norm]
            compound_area = area_by_name.get(norm)
            istd_area = area_by_name.get(entry.istd_norm)
            istd_conc = config.get(entry.istd_norm)
            value, issue = _compute_concentration(
                compound_area=compound_area,
                istd_area=istd_area,
                istd_conc=istd_conc,
                response_factor=entry.response_factor,
                istd_raw=entry.istd_raw,
            )
            new_row[CONCENTRATION_COLUMN] = (
                "" if value is None else _format_number(value)
            )
            if issue is not None:
                flag_unmatched(raw_name, "compound", issue)

        out_rows.append(new_row)

    return CalculationResult(fieldnames=out_fields, rows=out_rows, unmatched=unmatched)


def _compute_concentration(
    compound_area: float | None,
    istd_area: float | None,
    istd_conc: float | None,
    response_factor: float | None,
    istd_raw: str,
) -> tuple[float | None, str | None]:
    """Apply ((area / istd_area) * istd_conc * RF) / 1000, or report why not."""
    istd_label = istd_raw or "(unspecified)"
    if compound_area is None:
        return None, "compound has no usable area in results"
    if istd_area is None:
        return None, f"internal standard '{istd_label}' not found in results"
    if istd_area == 0:
        return None, f"internal standard '{istd_label}' has zero area"
    if istd_conc is None:
        return None, f"internal standard '{istd_label}' has no concentration in config"
    if response_factor is None:
        return None, "missing response factor in reference compounds"
    value = (compound_area / istd_area) * istd_conc * response_factor / 1000
    return value, None


def _format_number(value: float) -> str:
    """Render a number compactly without trailing float noise."""
    return f"{value:.6g}"


def write_output(path: Path, result: CalculationResult) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=result.fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result.rows)


def write_report(path: Path, result: CalculationResult) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["Name", "Role", "Issue"])
        writer.writeheader()
        writer.writerows(result.unmatched)


def _find_one(directory: Path, pattern: str, problems: list[str]) -> Path | None:
    """Return the single file in ``directory`` matching ``pattern``.

    Appends a message to ``problems`` (and returns ``None``) if there are zero
    or more than one match.
    """
    matches = sorted(directory.glob(pattern))
    if not matches:
        problems.append(f"No file matching '{pattern}'.")
        return None
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        problems.append(
            f"Found {len(matches)} files matching '{pattern}' "
            f"({names}); please leave only one."
        )
        return None
    return matches[0]


def discover_inputs(directory: Path) -> DiscoveredInputs:
    """Locate the three input files inside ``directory``.

    Raises :class:`InputError` with a human-readable message if a required file
    is missing or if the config/reference file is ambiguous. The config and
    reference files are matched by prefix (``config*.csv`` and
    ``reference_compounds*.csv``), so anything may follow the prefix.
    """
    if not directory.is_dir():
        raise InputError(f"Not a folder: {directory}")

    problems: list[str] = []

    results = directory / RESULTS_FILENAME
    if not results.is_file():
        problems.append(f"Missing '{RESULTS_FILENAME}'.")

    reference = _find_one(directory, REFERENCE_GLOB, problems)
    config = _find_one(directory, CONFIG_GLOB, problems)

    if problems:
        joined = "\n  - ".join(problems)
        raise InputError(
            f"Could not read inputs from:\n  {directory}\n\n  - {joined}"
        )

    assert reference is not None and config is not None
    return DiscoveredInputs(results=results, config=config, reference=reference)


def run_on_directory(directory: Path) -> RunSummary:
    """Discover inputs in ``directory`` and write ``outputs/`` results.

    Returns a :class:`RunSummary`. Raises :class:`InputError` on bad input.
    """
    directory = directory.expanduser()
    inputs = discover_inputs(directory)

    output_dir = directory / OUTPUT_DIRNAME
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / OUTPUT_FILENAME
    report_path = output_dir / REPORT_FILENAME

    result = calculate(inputs.results, inputs.config, inputs.reference)
    write_output(output_path, result)
    write_report(report_path, result)

    return RunSummary(
        output_path=output_path,
        report_path=report_path,
        row_count=len(result.rows),
        unmatched_count=len(result.unmatched),
        config_name=inputs.config.name,
    )
