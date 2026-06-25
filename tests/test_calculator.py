import csv
from pathlib import Path

import pytest

from targeted_lipid_panel_calculator import calculator as calc

# Minimal fixtures that reproduce the real-world quirks:
#   - results has a sample-names row on top, then a "Name, Area, Area, ..."
#     header, then one area per sample;
#   - results uses spaces + "(IS)" suffix:      "PC (15:0_18:1) d7 (IS)"
#   - reference uses no space, no suffix:       "PC(15:0_18:1) d7"
#   - config matches the results style.

RESULTS = """\
Compound Method,SampleA,SampleB
Name,Area,Area
PC (15:0_18:1) d7 (IS),1000000,2000000
PC (14:0_16:0),500000,400000
AC (10:0),154239,100000
LPC (18:1) d7 (IS),2000000,2000000
LPC (18:1) d7 (IS),2000000,2000000
"""

REFERENCE = """\
Compound name,ISTD Compound,Response Factor
PC(14:0_16:0),PC(15:0_18:1) d7,2
AC(10:0),AC(16:0) d3,1
"""

CONFIG = """\
Name,MW,Transition,ug/mL,uM or nmol/mL,,
PC (15:0_18:1) d7 (IS),753.6,753.6 -> 184.1,160,212.31,,
LPC (18:1) d7 (IS),529.4,529.4 -> 184.1,25,47.22,,
"""


def _write(directory: Path) -> None:
    (directory / "results.csv").write_text(RESULTS, encoding="utf-8")
    (directory / "reference_compounds.csv").write_text(REFERENCE, encoding="utf-8")
    (directory / "config_splash_II.csv").write_text(CONFIG, encoding="utf-8")


def _rows_by_name(result: calc.CalculationResult) -> dict[str, list[str]]:
    return {row[0]: row for row in result.data_rows}


@pytest.mark.parametrize(
    "a,b",
    [
        ("PC (15:0_18:1) d7 (IS)", "PC(15:0_18:1) d7"),
        ("AC (10:0)", "AC(10:0)"),
        ("DG (15:0 18:1) d7 (IS)", "DG(15:0_18:1) d7"),
    ],
)
def test_normalize_unifies_naming_styles(a, b):
    assert calc.normalize_name(a) == calc.normalize_name(b)


def test_is_marker_detection():
    assert calc.is_marked_internal_standard("LPC (18:1) d7 (IS)")
    assert not calc.is_marked_internal_standard("AC (10:0)")


def test_header_rows_interleave_samples(tmp_path: Path):
    _write(tmp_path)
    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    sample_header, field_header = result.header_rows
    # Sample names appear above both the Area and its nmol/mL column.
    assert sample_header == ["Compound Method", "", "SampleA", "SampleA",
                             "SampleB", "SampleB"]
    assert field_header == ["Name", calc.INTERNAL_STANDARD_COLUMN,
                            "Area", "nmol/mL", "Area", "nmol/mL"]


def test_calculate_per_sample_values(tmp_path: Path):
    _write(tmp_path)
    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    rows = _rows_by_name(result)

    # Internal standard: y, concentration repeated for each sample.
    istd = rows["PC (15:0_18:1) d7 (IS)"]
    assert istd[1] == "y"
    assert istd[3] == "212.31" and istd[5] == "212.31"

    # Compound, sample A: (500000/1000000) * 212.31 * 2 / 1000 = 0.21231
    # Compound, sample B: (400000/2000000) * 212.31 * 2 / 1000 = 0.084924
    compound = rows["PC (14:0_16:0)"]
    assert compound[1] == "n"
    assert float(compound[3]) == pytest.approx(0.21231)
    assert float(compound[5]) == pytest.approx(0.084924)


def test_missing_istd_is_blank_and_reported(tmp_path: Path):
    _write(tmp_path)
    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    rows = _rows_by_name(result)

    # AC (10:0)'s ISTD is absent from results -> blank nmol/mL in every sample.
    ac = rows["AC (10:0)"]
    assert ac[1] == "n"
    assert ac[3] == "" and ac[5] == ""
    # Original areas are preserved even when nmol/mL can't be computed.
    assert ac[2] == "154239" and ac[4] == "100000"
    assert any(u["Name"] == "AC (10:0)" for u in result.unmatched)


def test_single_sample_without_sample_row(tmp_path: Path):
    # A results file whose first row is already the header (no sample-names row).
    (tmp_path / "results.csv").write_text(
        "Name,Area\nPC (14:0_16:0),500000\nPC (15:0_18:1) d7 (IS),1000000\n",
        encoding="utf-8",
    )
    (tmp_path / "reference_compounds.csv").write_text(REFERENCE, encoding="utf-8")
    (tmp_path / "config_splash_II.csv").write_text(CONFIG, encoding="utf-8")

    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    # No sample-name row -> a single header row.
    assert len(result.header_rows) == 1
    rows = _rows_by_name(result)
    assert float(rows["PC (14:0_16:0)"][3]) == pytest.approx(0.21231)


def test_run_on_directory_writes_outputs(tmp_path: Path):
    _write(tmp_path)
    summary = calc.run_on_directory(tmp_path)

    assert summary.output_path == tmp_path / "outputs" / "output.csv"
    assert summary.report_path == tmp_path / "outputs" / "report.csv"
    assert summary.output_path.is_file()
    assert summary.report_path.is_file()

    with summary.output_path.open(newline="", encoding="utf-8") as fh:
        out_rows = list(csv.reader(fh))
    # Two header rows (sample names + fields), then the data rows.
    assert out_rows[0][0] == "Compound Method"
    assert out_rows[1][:2] == ["Name", calc.INTERNAL_STANDARD_COLUMN]


def test_cli_main_headless(tmp_path: Path, capsys):
    from targeted_lipid_panel_calculator.cli import main

    _write(tmp_path)
    exit_code = main([str(tmp_path)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Processed" in out
    assert (tmp_path / "outputs" / "output.csv").is_file()


def test_cli_main_bad_directory(tmp_path: Path, capsys):
    from targeted_lipid_panel_calculator.cli import main

    exit_code = main([str(tmp_path / "does_not_exist")])
    assert exit_code == 1
    assert "Error" in capsys.readouterr().out


def test_tk_available_returns_bool():
    from targeted_lipid_panel_calculator.gui import tk_available

    assert isinstance(tk_available(), bool)


def test_discover_inputs_errors(tmp_path: Path):
    with pytest.raises(calc.InputError):
        calc.discover_inputs(tmp_path)  # empty dir

    _write(tmp_path)
    (tmp_path / "config_other.csv").write_text(CONFIG, encoding="utf-8")
    with pytest.raises(calc.InputError, match="please leave only one"):
        calc.discover_inputs(tmp_path)  # two config files


def test_discover_accepts_reference_suffix(tmp_path: Path):
    _write(tmp_path)
    (tmp_path / "reference_compounds.csv").rename(
        tmp_path / "reference_compounds_2024.csv"
    )
    inputs = calc.discover_inputs(tmp_path)
    assert inputs.reference.name == "reference_compounds_2024.csv"


def test_discover_errors_on_two_reference_files(tmp_path: Path):
    _write(tmp_path)
    (tmp_path / "reference_compounds_extra.csv").write_text(
        REFERENCE, encoding="utf-8"
    )
    with pytest.raises(calc.InputError, match="please leave only one"):
        calc.discover_inputs(tmp_path)
