#!/usr/bin/env bash
#
# Run the targeted lipid panel calculator locally on a folder of CSV files.
#
# Usage:
#   ./run.sh /path/to/input_folder
#
# The folder must contain results.csv, one reference_compounds*.csv file and
# one config*.csv file. Results are written to <folder>/outputs/.
#
set -euo pipefail

# Always run from the project root (the directory this script lives in) so that
# `uv` finds pyproject.toml regardless of where the script is called from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ $# -lt 1 || -z "${1:-}" ]]; then
  echo "Usage: $0 /path/to/input_folder" >&2
  echo "  The folder must contain results.csv, one reference_compounds*.csv and one config*.csv" >&2
  exit 64
fi

INPUT_DIR="$1"

if [[ ! -d "$INPUT_DIR" ]]; then
  echo "Error: not a folder: $INPUT_DIR" >&2
  exit 66
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: 'uv' is not installed. See https://docs.astral.sh/uv/" >&2
  exit 69
fi

# Make sure the environment is in sync, then run on the given folder.
uv sync --quiet
uv run targeted-lipid-panel-calculator "$INPUT_DIR"
