"""Core logic for the targeted lipid panel calculator.

Reads three CSV inputs (results, config, reference compounds) and writes two
output CSVs: an areas table and an nmol/mL table. The results file holds one
``Area`` column per sample; each output table keeps the ``Name`` column, adds a
single ``Internal Standard (y/n)`` column, then has one column per sample (the
``Area`` in one file, the calculated ``nmol/mL`` in the other).

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
AREAS_FILENAME = "areas.csv"
NMOL_FILENAME = "nmol_per_mL.csv"
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
    areas_path: Path
    nmol_path: Path
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
class ResultsTable:
    """Parsed results file.

    The results file has one row per compound and one ``Area`` column per
    sample. Optionally the very first row holds the sample names (its first cell
    is something other than ``Name``); that row is not data and is only used to
    label the output.
    """

    first_cell: str  # original top-left cell, e.g. "Compound Method" or "Name"
    sample_names: list[str]  # one per area column
    has_sample_row: bool
    names: list[str]  # compound name per data row (original spelling)
    areas_raw: list[list[str]]  # per data row: original area strings per sample
    areas: list[list[float | None]]  # per data row: parsed areas per sample
    area_by_norm: dict[str, list[float | None]]  # normalized name -> areas


@dataclass
class CalculationResult:
    """Computed results, kept column-agnostic so the area table and the
    nmol/mL table can both be written from the same data."""

    first_cell: str  # original top-left cell of the results file
    sample_names: list[str]  # one per sample
    has_sample_row: bool
    names: list[str]  # compound name per data row
    is_flags: list[str]  # "y"/"n"/"not found" per data row
    areas: list[list[str]]  # per data row: original area string per sample
    nmols: list[list[str]]  # per data row: formatted nmol/mL string per sample
    unmatched: list[dict[str, str]] = field(default_factory=list)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    # utf-8-sig strips the BOM present at the start of each input file.
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = [row for row in reader]
    return list(fieldnames), rows


def _require_columns(path: Path, fieldnames: list[str], required: list[str]) -> None:
    """Raise a clear input error if an input file is missing required columns."""
    present = {name.strip() for name in fieldnames}
    missing = [name for name in required if name not in present]
    if missing:
        missing_text = ", ".join(f"'{name}'" for name in missing)
        raise InputError(f"{path.name} is missing required column(s): {missing_text}")


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
    fieldnames, rows = _read_csv(path)
    _require_columns(path, fieldnames, [REFERENCE_NAME, REFERENCE_ISTD, REFERENCE_RF])
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
    fieldnames, rows = _read_csv(path)
    _require_columns(path, fieldnames, [CONFIG_NAME, CONFIG_CONCENTRATION])
    conc: dict[str, float] = {}
    for row in rows:
        raw = (row.get(CONFIG_NAME) or "").strip()
        if not raw:
            continue
        value = _to_float(row.get(CONFIG_CONCENTRATION))
        if value is not None:
            conc[normalize_name(raw)] = value
    return conc


def load_results(path: Path) -> ResultsTable:
    """Parse the (possibly multi-sample) results file.

    Layout going forward: an optional first row of sample names, then a header
    row of ``Name`` followed by one ``Area`` column per sample, then one row per
    compound. Retention-time / transition columns are not expected and any
    non-``Area`` column is ignored. The first row is treated as sample names
    whenever its first cell is not ``Name``.
    """
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [row for row in csv.reader(fh) if any(cell.strip() for cell in row)]

    if not rows:
        raise InputError(f"Results file is empty: {path}")

    first_is_header = rows[0] and rows[0][0].strip().lower() == RESULTS_NAME.lower()
    if first_is_header:
        sample_row: list[str] | None = None
        header = rows[0]
        data = rows[1:]
    else:
        sample_row = rows[0]
        if len(rows) < 2:
            raise InputError(f"Results file has no header row: {path}")
        header = rows[1]
        data = rows[2:]

    name_col = next(
        (
            i
            for i, h in enumerate(header)
            if h.strip().lower() == RESULTS_NAME.lower()
        ),
        None,
    )
    if name_col is None:
        raise InputError(
            f"Results file is missing required column '{RESULTS_NAME}': {path}"
        )

    area_cols = [i for i, h in enumerate(header) if h.strip().lower() == "area"]
    if not area_cols:
        raise InputError(
            f"Results file has no 'Area' columns: {path}. Expected a header row "
            f"of 'Name' followed by one 'Area' column per sample."
        )

    if sample_row is not None:
        sample_names = [
            (sample_row[i].strip() if i < len(sample_row) else "") for i in area_cols
        ]
        first_cell = sample_row[0].strip() if sample_row else RESULTS_NAME
    else:
        sample_names = ["" for _ in area_cols]
        first_cell = RESULTS_NAME

    names: list[str] = []
    areas_raw: list[list[str]] = []
    areas: list[list[float | None]] = []
    area_by_norm: dict[str, list[float | None]] = {}

    for row in data:
        raw_name = row[name_col].strip() if name_col < len(row) else ""
        if not raw_name:
            continue
        raw_vals = [(row[i] if i < len(row) else "").strip() for i in area_cols]
        parsed = [_to_float(v) for v in raw_vals]
        names.append(raw_name)
        areas_raw.append(raw_vals)
        areas.append(parsed)
        norm = normalize_name(raw_name)
        if norm not in area_by_norm:  # duplicate name: keep first occurrence
            area_by_norm[norm] = parsed

    return ResultsTable(
        first_cell=first_cell,
        sample_names=sample_names,
        has_sample_row=sample_row is not None,
        names=names,
        areas_raw=areas_raw,
        areas=areas,
        area_by_norm=area_by_norm,
    )


def calculate(
    results_path: Path,
    config_path: Path,
    reference_path: Path,
) -> CalculationResult:
    """Run the full calculation.

    For every compound it records the per-sample ``Area`` (carried through
    unchanged) and the calculated per-sample ``nmol/mL``. These are written out
    as two separate tables (see :func:`write_areas` and :func:`write_nmol`),
    each with a ``Name`` and a single ``Internal Standard (y/n)`` column.
    """
    reference, reference_names = load_reference(reference_path)
    config = load_config(config_path)
    table = load_results(results_path)
    n_samples = len(table.sample_names)

    names: list[str] = []
    is_flags: list[str] = []
    areas_out: list[list[str]] = []
    nmols_out: list[list[str]] = []
    unmatched: list[dict[str, str]] = []

    def flag_unmatched(name: str, role: str, issue: str) -> None:
        unmatched.append({"Name": name, "Role": role, "Issue": issue})

    for idx, raw_name in enumerate(table.names):
        norm = normalize_name(raw_name)
        sample_areas = table.areas[idx]
        sample_areas_raw = table.areas_raw[idx]

        is_internal_standard = is_marked_internal_standard(raw_name) or (
            norm not in reference_names
        )

        if is_internal_standard:
            conc = config.get(norm)
            conc_str = "" if conc is None else _format_number(conc)
            if conc is None:
                # Labelled an internal standard only because it isn't in the
                # reference; with no config concentration either, its identity
                # is genuinely unknown.
                flag_unmatched(
                    raw_name,
                    "unknown",
                    "not found in reference or config - check this",
                )
                is_flag = "not found"
            else:
                is_flag = "y"
            # An internal standard's concentration is the same for every sample.
            nmols = [conc_str for _ in range(n_samples)]
        else:
            is_flag = "n"
            entry = reference[norm]
            istd_areas = table.area_by_norm.get(entry.istd_norm)
            istd_conc = config.get(entry.istd_norm)
            issue = _compound_issue(
                istd_areas=istd_areas,
                istd_conc=istd_conc,
                response_factor=entry.response_factor,
                istd_raw=entry.istd_raw,
            )
            if issue is not None:
                flag_unmatched(raw_name, "compound", issue)
            if any(area == 0 for area in sample_areas):
                flag_unmatched(
                    raw_name,
                    "compound",
                    "compound area is zero in one or more samples",
                )
            if istd_areas is not None and any(area == 0 for area in istd_areas):
                istd_label = entry.istd_raw or "(unspecified)"
                flag_unmatched(
                    raw_name,
                    "compound",
                    f"internal standard '{istd_label}' area is zero "
                    "in one or more samples",
                )
            nmols = []
            for j in range(n_samples):
                value = None
                if issue is None:
                    value = _per_sample_value(
                        compound_area=sample_areas[j],
                        istd_area=istd_areas[j],  # type: ignore[index]
                        istd_conc=istd_conc,  # type: ignore[arg-type]
                        response_factor=entry.response_factor,  # type: ignore[arg-type]
                    )
                nmols.append("" if value is None else _format_number(value))

        names.append(raw_name)
        is_flags.append(is_flag)
        areas_out.append(list(sample_areas_raw))
        nmols_out.append(nmols)

    return CalculationResult(
        first_cell=table.first_cell,
        sample_names=table.sample_names,
        has_sample_row=table.has_sample_row,
        names=names,
        is_flags=is_flags,
        areas=areas_out,
        nmols=nmols_out,
        unmatched=unmatched,
    )


def _compound_issue(
    istd_areas: list[float | None] | None,
    istd_conc: float | None,
    response_factor: float | None,
    istd_raw: str,
) -> str | None:
    """Return a compound-level reason it can't be computed, else ``None``.

    Per-sample problems (a single blank/zero area) are handled per cell; this
    only covers reasons that apply to the whole compound across all samples.
    """
    istd_label = istd_raw or "(unspecified)"
    if istd_areas is None:
        return f"internal standard '{istd_label}' not found in results"
    if istd_conc is None:
        return f"internal standard '{istd_label}' has no concentration in config"
    if response_factor is None:
        return "missing response factor in reference compounds"
    return None


def _per_sample_value(
    compound_area: float | None,
    istd_area: float | None,
    istd_conc: float,
    response_factor: float,
) -> float | None:
    """(area / istd_area) * istd_conc * RF for one sample, or None."""
    if (
        compound_area is None
        or compound_area == 0
        or istd_area is None
        or istd_area == 0
    ):
        return None
    return (compound_area / istd_area) * istd_conc * response_factor


def _format_number(value: float) -> str:
    """Render a number compactly without trailing float noise."""
    return f"{value:.6g}"


def _write_table(
    path: Path,
    result: CalculationResult,
    value_label: str,
    values: list[list[str]],
) -> None:
    """Write one wide table: Name, Internal Standard (y/n), one column/sample."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if result.has_sample_row:
            writer.writerow([result.first_cell, "", *result.sample_names])
        value_cols = [value_label] * len(result.sample_names)
        writer.writerow([RESULTS_NAME, INTERNAL_STANDARD_COLUMN, *value_cols])
        for name, flag, row_values in zip(
            result.names, result.is_flags, values, strict=True
        ):
            writer.writerow([name, flag, *row_values])


def write_areas(path: Path, result: CalculationResult) -> None:
    """Write the areas table (one ``Area`` column per sample)."""
    _write_table(path, result, RESULTS_AREA, result.areas)


def write_nmol(path: Path, result: CalculationResult) -> None:
    """Write the concentration table (one ``nmol/mL`` column per sample)."""
    _write_table(path, result, CONCENTRATION_COLUMN, result.nmols)


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
    areas_path = output_dir / AREAS_FILENAME
    nmol_path = output_dir / NMOL_FILENAME
    report_path = output_dir / REPORT_FILENAME

    result = calculate(inputs.results, inputs.config, inputs.reference)
    write_areas(areas_path, result)
    write_nmol(nmol_path, result)
    write_report(report_path, result)

    return RunSummary(
        areas_path=areas_path,
        nmol_path=nmol_path,
        report_path=report_path,
        row_count=len(result.names),
        unmatched_count=len(result.unmatched),
        config_name=inputs.config.name,
    )
