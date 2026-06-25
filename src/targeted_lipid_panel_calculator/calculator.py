"""Core logic for the targeted lipid panel calculator.

Reads three CSV inputs (results, config, reference compounds) and writes an
output CSV. The results file holds one ``Area`` column per sample; the output
keeps the ``Name`` column, adds a single ``Internal Standard (y/n)`` column,
then for each sample emits its ``Area`` immediately followed by the calculated
``nmol/mL``.

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
    header_rows: list[list[str]]  # 1 or 2 header rows (sample names + fields)
    data_rows: list[list[str]]
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

    area_cols = [i for i, h in enumerate(header) if h.strip().lower() == "area"]
    if not area_cols:
        raise InputError(
            f"Results file has no 'Area' columns: {path}. Expected a header row "
            f"of 'Name' followed by one 'Area' column per sample."
        )
    name_col = next(
        (i for i, h in enumerate(header) if h.strip().lower() == RESULTS_NAME.lower()),
        0,
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
    """Run the full calculation and return rows ready to be written out.

    The output keeps the ``Name`` column, adds a single ``Internal Standard
    (y/n)`` column, then for every sample emits its ``Area`` immediately
    followed by the calculated ``nmol/mL``.
    """
    reference, reference_names = load_reference(reference_path)
    config = load_config(config_path)
    table = load_results(results_path)
    n_samples = len(table.sample_names)

    # Header rows: optional sample-name row, then the field header.
    field_header = [RESULTS_NAME, INTERNAL_STANDARD_COLUMN]
    for _ in range(n_samples):
        field_header += [RESULTS_AREA, CONCENTRATION_COLUMN]

    header_rows: list[list[str]] = []
    if table.has_sample_row:
        sample_header = [table.first_cell, ""]
        for name in table.sample_names:
            sample_header += [name, name]  # label both Area and its nmol/mL
        header_rows.append(sample_header)
    header_rows.append(field_header)

    data_rows: list[list[str]] = []
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

        out_row: list[str] = [raw_name, "y" if is_internal_standard else "n"]

        if is_internal_standard:
            conc = config.get(norm)
            conc_str = "" if conc is None else _format_number(conc)
            if conc is None:
                flag_unmatched(
                    raw_name,
                    "internal standard",
                    "no matching concentration in config file",
                )
            # An internal standard's concentration is the same for every sample.
            for j in range(n_samples):
                out_row += [sample_areas_raw[j], conc_str]
        else:
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
            for j in range(n_samples):
                value = None
                if issue is None:
                    value = _per_sample_value(
                        compound_area=sample_areas[j],
                        istd_area=istd_areas[j],  # type: ignore[index]
                        istd_conc=istd_conc,  # type: ignore[arg-type]
                        response_factor=entry.response_factor,  # type: ignore[arg-type]
                    )
                out_row += [
                    sample_areas_raw[j],
                    "" if value is None else _format_number(value),
                ]

        data_rows.append(out_row)

    return CalculationResult(
        header_rows=header_rows, data_rows=data_rows, unmatched=unmatched
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
    """((area / istd_area) * istd_conc * RF) / 1000 for one sample, or None."""
    if compound_area is None or istd_area is None or istd_area == 0:
        return None
    return (compound_area / istd_area) * istd_conc * response_factor / 1000


def _format_number(value: float) -> str:
    """Render a number compactly without trailing float noise."""
    return f"{value:.6g}"


def write_output(path: Path, result: CalculationResult) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerows(result.header_rows)
        writer.writerows(result.data_rows)


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
        row_count=len(result.data_rows),
        unmatched_count=len(result.unmatched),
        config_name=inputs.config.name,
    )
