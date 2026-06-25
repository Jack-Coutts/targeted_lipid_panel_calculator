# targeted-lipid-panel-calculator

Enriches a lipidomics `results.csv` (which holds one `Area` column per sample):

- adds an **`Internal Standard (y/n)`** column per compound, and
- adds a **`nmol/mL`** value next to every sample's `Area` (blank when it can't
  be computed).

You point the tool at a **folder** containing the three input files; it writes the
results into an `outputs/` subfolder of that same folder.

---

## Contents

- [Workflow](#workflow)
- [Input files (exact requirements)](#input-files-exact-requirements)
- [Output files](#output-files)
- [How the calculation works](#how-the-calculation-works)
- [Running it](#running-it)
- [Development](#development)
- [Building a click-to-run executable](#building-a-click-to-run-executable)
- [Project layout](#project-layout)

---

## Workflow

```mermaid
flowchart TD
    subgraph IN[Input folder]
      A[results.csv<br/>Name + one Area per sample]
      B[reference_compounds*.csv<br/>Compound name, ISTD Compound, Response Factor]
      C[config*.csv<br/>Name, uM or nmol/mL]
    end

    A --> N[Normalise names<br/>so the 3 files line up]
    B --> N
    C --> N
    N --> R{For each compound:<br/>is it an internal standard?<br/>has '(IS)' OR not in reference}

    R -->|Yes| ISY["Internal Standard = y<br/>each sample's nmol/mL = config concentration"]
    R -->|No| CALC[Internal Standard = n<br/>find its ISTD from reference]

    CALC --> F{ISTD area in results<br/>AND ISTD conc in config?}
    F -->|No| SKIP[leave nmol/mL blank for all samples<br/>+ record in report.csv]
    F -->|Yes| FORMULA["for each sample:<br/>nmol/mL = (sample area / sample istd_area)<br/>× istd_conc × RF"]

    ISY --> OUT[(outputs/output.csv<br/>Area + nmol/mL per sample)]
    SKIP --> OUT
    FORMULA --> OUT
    SKIP --> REP[(outputs/report.csv)]
```

---

## Input files (exact requirements)

All three files must be in the **same folder**. Files are read as CSV with a
UTF-8 (BOM tolerated) encoding. Only the columns listed below are used; any extra
columns are ignored, and trailing empty columns are fine.

### 1. `results.csv` (exact name)

The measured peak areas, with **one `Area` column per sample**. The layout is:

- **Row 1** — sample names. The first cell is a label (e.g. `Compound Method`);
  each following cell is the sample name for that column. This row is not data
  and is only used to label the output.
- **Row 2** — the column header: `Name`, then one `Area` per sample.
- **Rows 3+** — one compound per row: its name, then its area in each sample.

There is no retention-time or transition column; any column that isn't `Area` is
ignored. **Required:** a `Name` column and one or more `Area` columns.

| Compound Method | Sample_01 | Sample_02 | Sample_03 |
|---|---|---|---|
| **Name** | **Area** | **Area** | **Area** |
| AC (10:0) | 154239 | 98231 | 120044 |
| PC (14:0_16:0) | 500000 | 412300 | 538100 |
| PC (15:0_18:1) d7 (IS) | 58328343 | 60112022 | 57991233 |

(A single-sample file with just a `Name,Area` header and no sample-names row also
works.)

### 2. `reference_compounds*.csv` (one file starting with `reference_compounds`)

The lookup table linking each compound to its internal standard. The filename
must start with `reference_compounds` (e.g. `reference_compounds.csv` or
`reference_compounds_2024.csv`); exactly one such file must be present.
**Required columns:** `Compound name`, `ISTD Compound`, `Response Factor`.

| Compound name | ISTD Compound | Response Factor |
|---|---|---|
| PC(14:0_16:0) | PC(15:0_18:1) d7 | 1 |
| AC(10:0) | AC(16:0) d3 | 1 |

- `Compound name` is matched against `Name` in `results.csv`.
- `ISTD Compound` names the internal standard used for that compound; its area is
  looked up in `results.csv` and its concentration in the config file.
- A compound in `results.csv` that is **not** listed here is treated as an
  internal standard.

### 3. `config*.csv` (one file matching `config*.csv`, e.g. `config_splash_II.csv`)

The internal-standard mix used in this run. **Required columns:** `Name`,
`uM or nmol/mL`.

| Name | MW | Transition | ug/mL | uM or nmol/mL |
|---|---|---|---|---|
| PC (15:0_18:1) d7 (IS) | 753.6 | 753.6 -> 184.1 | 160 | 212.31 |
| LPC (18:1) d7 (IS) | 529.4 | 529.4 -> 184.1 | 25 | 47.22 |

Exactly **one** `config*.csv` must be present in the folder (the tool errors if
there are zero or more than one).

> **Naming note.** The same compound is spelled slightly differently in each file
> (`AC (10:0)` vs `AC(10:0)`; `PC (15:0_18:1) d7 (IS)` vs `PC(15:0_18:1) d7`). The
> tool normalises names before matching (case-insensitive; ignores spaces, `_` vs
> space, and the `(IS)` / `[reference]` markers), so these line up automatically.

---

## Output files

Written to `<input folder>/outputs/`:

- **`output.csv`** — `Name`, a single `Internal Standard (y/n)` column, then for
  **every sample** its `Area` immediately followed by the calculated `nmol/mL`.
  The sample names are carried across as a top header row, labelling each
  `Area` + `nmol/mL` pair, e.g.:

  | Compound Method | | Sample_01 | Sample_01 | Sample_02 | Sample_02 |
  |---|---|---|---|---|---|
  | **Name** | **Internal Standard (y/n)** | **Area** | **nmol/mL** | **Area** | **nmol/mL** |
  | PC (14:0_16:0) | n | 500000 | 212.31 | 412300 | 175.03 |
  | PC (15:0_18:1) d7 (IS) | y | 58328343 | 212.31 | 60112022 | 212.31 |

- **`report.csv`** — one row per compound that could **not** be fully computed.
  Its columns are:
  - **`Name`** — the compound name from `results.csv`.
  - **`Role`** — how the compound was treated: `compound` (a measured compound
    whose internal standard couldn't be resolved), `internal standard`, or
    `unknown`.
  - **`Issue`** — why it couldn't be computed (see the next section).

---

## How the calculation works

Whether a row is an internal standard is decided once per compound; the
concentration is then calculated **independently for every sample** (every
`Area` column).

1. **Internal standard?** A row is an internal standard if its name has an `(IS)`
   marker **or** it does not appear in the `Compound name` column of
   `reference_compounds.csv`.
   - If so → `Internal Standard = y`, and each sample's `nmol/mL` is the config
     file's `uM or nmol/mL` value for that name (the same for every sample).
   - **Unknown rows.** A row that is treated as an internal standard *only*
     because it isn't in the reference, **and** also has no concentration in the
     config, can't be identified as either a real compound or a known standard.
     Its `nmol/mL` is left blank and it is listed in `report.csv` with
     `Role = unknown` and `Issue = "not found in reference or config - check
     this"` so it can be investigated.
2. **Otherwise** → `Internal Standard = n`, and for each sample:

   ```
   nmol/mL = (compound area / ISTD area) * ISTD concentration * Response Factor
   ```

   - **compound area** — that sample's area for the compound.
   - **ISTD** — taken from the compound's `ISTD Compound` in the reference file.
   - **ISTD area** — that **same sample's** area for the internal standard.
   - **ISTD concentration** — looked up in the config (`uM or nmol/mL`).
   - **Response Factor** — from the compound's row in the reference file.
   - If the ISTD is missing from `results.csv`, or its concentration is missing
     from the config, the compound's `nmol/mL` is left blank in every sample and
     it is listed in `report.csv`. An individual sample with a missing/zero area
     is left blank just for that sample. Duplicate result rows use the first
     occurrence for ISTD-area lookups.

---

## Running it

You need [uv](https://docs.astral.sh/uv/) installed.

### Option A — shell script (simplest, pass the folder)

```bash
./run.sh /path/to/input_folder
```

`run.sh` syncs the environment and runs the calculator on the given folder. Run
it with no argument to see usage. Example using the bundled sample data:

```bash
./run.sh data
```

### Option B — uv directly

```bash
uv sync                                          # one-time / after changes
uv run targeted-lipid-panel-calculator data      # headless on a folder
uv run targeted-lipid-panel-calculator           # no arg -> folder-picker GUI
```

### Option C — double-click app

See [Building a click-to-run executable](#building-a-click-to-run-executable).
Double-clicking opens a folder picker; pick the folder with your CSVs and the
`outputs/` folder is written there.

After any of these, look in `<folder>/outputs/` for `output.csv` and `report.csv`.
Any compound whose `nmol/mL` could not be calculated (for example because its
internal standard is not present in this run) is listed in `report.csv` with the
reason.

---

## Development

```bash
uv sync                    # install project + dev tools into .venv
uv run pytest              # run the test suite
uv run ruff check .        # lint
uv run ruff format .       # auto-format
```

- Core logic lives in `src/targeted_lipid_panel_calculator/calculator.py`.
- Tests in `tests/test_calculator.py` use small fixtures that reproduce the
  cross-file naming quirks.

---

## Building a click-to-run executable

Executables are built with [PyInstaller](https://pyinstaller.org/). **PyInstaller
cannot cross-compile** — build the Windows app on Windows, the macOS app on macOS:

```bash
uv sync
uv run python build.py
```

Results land in `dist/`:

- **Windows:** `dist/TargetedLipidPanelCalculator.exe` (single file).
- **macOS:** `dist/TargetedLipidPanelCalculator.app` (double-clickable bundle).

### GUI requirement: Tk

The folder-picker GUI uses `tkinter`, so the **build machine's** Python must
include Tk:

- **Windows / macOS (python.org installers):** Tk is included — nothing to do.
- **macOS (Homebrew Python):** `brew install python-tk@3.14`.

If Tk is unavailable the app still runs, but instead of a dialog it prompts for the
folder path on the console.

---

## Project layout

```
.
├── run.sh                  # run locally: ./run.sh <input_folder>
├── app.py                  # PyInstaller entry script
├── build.py                # builds the executable for the current OS
├── pyproject.toml
├── data/                   # example input files
├── src/targeted_lipid_panel_calculator/
│   ├── calculator.py       # core logic (load, normalise, compute, report)
│   ├── cli.py              # CLI + GUI/console dispatch
│   └── gui.py              # tkinter folder picker
└── tests/
    └── test_calculator.py
```
