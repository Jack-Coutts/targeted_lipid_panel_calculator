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
Mystery (1:0),10,20
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


def _calc(directory: Path) -> calc.CalculationResult:
    return calc.calculate(
        directory / "results.csv",
        directory / "config_splash_II.csv",
        directory / "reference_compounds.csv",
    )


def _index(result: calc.CalculationResult) -> dict[str, int]:
    return {name: i for i, name in enumerate(result.names)}


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


def test_calculate_per_sample_values(tmp_path: Path):
    _write(tmp_path)
    result = _calc(tmp_path)
    idx = _index(result)

    # Internal standard: y, concentration repeated for each sample.
    i = idx["PC (15:0_18:1) d7 (IS)"]
    assert result.is_flags[i] == "y"
    assert result.nmols[i] == ["212.31", "212.31"]

    # Compound, sample A: (500000/1000000) * 212.31 * 2 = 212.31
    # Compound, sample B: (400000/2000000) * 212.31 * 2 = 84.924
    c = idx["PC (14:0_16:0)"]
    assert result.is_flags[c] == "n"
    assert result.areas[c] == ["500000", "400000"]
    assert float(result.nmols[c][0]) == pytest.approx(212.31)
    assert float(result.nmols[c][1]) == pytest.approx(84.924)


def test_missing_istd_is_blank_and_reported(tmp_path: Path):
    _write(tmp_path)
    result = _calc(tmp_path)
    idx = _index(result)

    # AC (10:0)'s ISTD is absent -> blank nmol/mL in every sample, areas kept.
    a = idx["AC (10:0)"]
    assert result.is_flags[a] == "n"
    assert result.nmols[a] == ["", ""]
    assert result.areas[a] == ["154239", "100000"]
    assert any(u["Name"] == "AC (10:0)" for u in result.unmatched)


def test_unknown_when_not_in_reference_or_config(tmp_path: Path):
    _write(tmp_path)
    result = _calc(tmp_path)
    unknown = [u for u in result.unmatched if u["Name"] == "Mystery (1:0)"]
    assert len(unknown) == 1
    assert unknown[0]["Role"] == "unknown"
    assert unknown[0]["Issue"] == "not found in reference or config - check this"


def test_single_sample_without_sample_row(tmp_path: Path):
    # A results file whose first row is already the header (no sample-names row).
    (tmp_path / "results.csv").write_text(
        "Name,Area\nPC (14:0_16:0),500000\nPC (15:0_18:1) d7 (IS),1000000\n",
        encoding="utf-8",
    )
    (tmp_path / "reference_compounds.csv").write_text(REFERENCE, encoding="utf-8")
    (tmp_path / "config_splash_II.csv").write_text(CONFIG, encoding="utf-8")

    result = _calc(tmp_path)
    assert result.has_sample_row is False
    idx = _index(result)
    assert float(result.nmols[idx["PC (14:0_16:0)"]][0]) == pytest.approx(212.31)


def test_write_areas_and_nmol_tables(tmp_path: Path):
    _write(tmp_path)
    result = _calc(tmp_path)

    areas_path = tmp_path / "areas.csv"
    nmol_path = tmp_path / "nmol.csv"
    calc.write_areas(areas_path, result)
    calc.write_nmol(nmol_path, result)

    with areas_path.open(newline="", encoding="utf-8") as fh:
        areas = list(csv.reader(fh))
    with nmol_path.open(newline="", encoding="utf-8") as fh:
        nmol = list(csv.reader(fh))

    # Both share the sample-name row and the Name + IS columns.
    assert areas[0] == ["Compound Method", "", "SampleA", "SampleB"]
    assert areas[1] == ["Name", calc.INTERNAL_STANDARD_COLUMN, "Area", "Area"]
    assert nmol[0] == ["Compound Method", "", "SampleA", "SampleB"]
    assert nmol[1] == ["Name", calc.INTERNAL_STANDARD_COLUMN, "nmol/mL", "nmol/mL"]

    # The areas file carries the raw areas; the nmol file the computed values.
    areas_by_name = {r[0]: r for r in areas[2:]}
    nmol_by_name = {r[0]: r for r in nmol[2:]}
    assert areas_by_name["PC (14:0_16:0)"] == [
        "PC (14:0_16:0)", "n", "500000", "400000",
    ]
    assert nmol_by_name["PC (14:0_16:0)"][1] == "n"
    assert float(nmol_by_name["PC (14:0_16:0)"][2]) == pytest.approx(212.31)


def test_run_on_directory_writes_both_outputs(tmp_path: Path):
    _write(tmp_path)
    summary = calc.run_on_directory(tmp_path)

    assert summary.areas_path == tmp_path / "outputs" / "areas.csv"
    assert summary.nmol_path == tmp_path / "outputs" / "nmol_per_mL.csv"
    assert summary.report_path == tmp_path / "outputs" / "report.csv"
    assert summary.areas_path.is_file()
    assert summary.nmol_path.is_file()
    assert summary.report_path.is_file()


def test_cli_main_headless(tmp_path: Path, capsys):
    from targeted_lipid_panel_calculator.cli import main

    _write(tmp_path)
    exit_code = main([str(tmp_path)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Processed" in out
    assert (tmp_path / "outputs" / "areas.csv").is_file()
    assert (tmp_path / "outputs" / "nmol_per_mL.csv").is_file()


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
