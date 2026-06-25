import csv
from pathlib import Path

import pytest

from targeted_lipid_panel_calculator import calculator as calc

# Minimal fixtures that reproduce the real-world naming quirks:
#   - results uses spaces + "(IS)" suffix:      "PC (15:0_18:1) d7 (IS)"
#   - reference uses no space, no suffix:       "PC(15:0_18:1) d7"
#   - config matches the results style.

RESULTS = """\
Name,Transition,RT,Area
PC (15:0_18:1) d7 (IS),753.6 -> 184.1,6.7,1000000
PC (14:0_16:0),706.5 -> 184.1,5.0,500000
AC (10:0),316.3 -> 85.1,0.9,154239
LPC (18:1) d7 (IS),529.4 -> 184.1,2.8,2000000
LPC (18:1) d7 (IS),529.4 -> 184.1,2.8,2000000
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


def test_calculate_marks_and_computes(tmp_path: Path):
    _write(tmp_path)
    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    by_name = {r["Name"]: r for r in result.rows}

    # Internal standard: marked y, concentration straight from config.
    istd = by_name["PC (15:0_18:1) d7 (IS)"]
    assert istd[calc.INTERNAL_STANDARD_COLUMN] == "y"
    assert istd[calc.CONCENTRATION_COLUMN] == "212.31"

    # Normal compound: (500000/1000000) * 212.31 * 2 / 1000 = 0.21231
    compound = by_name["PC (14:0_16:0)"]
    assert compound[calc.INTERNAL_STANDARD_COLUMN] == "n"
    assert float(compound[calc.CONCENTRATION_COLUMN]) == pytest.approx(0.21231)


def test_missing_istd_is_blank_and_reported(tmp_path: Path):
    _write(tmp_path)
    result = calc.calculate(
        tmp_path / "results.csv",
        tmp_path / "config_splash_II.csv",
        tmp_path / "reference_compounds.csv",
    )
    by_name = {r["Name"]: r for r in result.rows}

    # AC (10:0)'s ISTD "AC(16:0) d3" is absent from results -> blank + report.
    ac = by_name["AC (10:0)"]
    assert ac[calc.INTERNAL_STANDARD_COLUMN] == "n"
    assert ac[calc.CONCENTRATION_COLUMN] == ""
    assert any(u["Name"] == "AC (10:0)" for u in result.unmatched)


def test_run_on_directory_writes_outputs(tmp_path: Path):
    _write(tmp_path)
    summary = calc.run_on_directory(tmp_path)

    assert summary.output_path == tmp_path / "outputs" / "output.csv"
    assert summary.report_path == tmp_path / "outputs" / "report.csv"
    assert summary.output_path.is_file()
    assert summary.report_path.is_file()
    assert summary.config_name == "config_splash_II.csv"

    with summary.output_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert calc.INTERNAL_STANDARD_COLUMN in rows[0]
    assert calc.CONCENTRATION_COLUMN in rows[0]


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
