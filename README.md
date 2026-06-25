# targeted-lipid-panel-calculator

A Python tool for calculating targeted lipid panels.

## Requirements

- [uv](https://docs.astral.sh/uv/) for environment and dependency management
- Python 3.14 (managed automatically by uv via `.python-version`)

## Setup

```bash
# Install the project and its dependencies into a managed virtual environment
uv sync
```

## Usage

Run the CLI entry point:

```bash
uv run targeted-lipid-panel-calculator
```

Or run a module/script inside the project environment:

```bash
uv run python -m targeted_lipid_panel_calculator
```

## Development

```bash
# Add a runtime dependency
uv add <package>

# Add a development-only dependency
uv add --dev <package>

# Run the test suite
uv run pytest

# Lint and format
uv run ruff check .
uv run ruff format .
```

## Project layout

```
.
├── pyproject.toml      # Project metadata and dependencies
├── uv.lock             # Locked dependency versions (committed)
├── .python-version     # Pinned Python version for uv
├── src/
│   └── targeted_lipid_panel_calculator/
│       └── __init__.py
└── tests/
    └── test_basic.py
```
