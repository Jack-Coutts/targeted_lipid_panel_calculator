import targeted_lipid_panel_calculator as pkg


def test_main_runs(capsys):
    pkg.main()
    captured = capsys.readouterr()
    assert "targeted-lipid-panel-calculator" in captured.out
