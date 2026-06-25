"""PyInstaller entry point.

Kept as a tiny top-level script (rather than a module) because PyInstaller
analyses a script file. With no command-line argument this launches the
folder-picker GUI, which is the double-click experience for the bundled app.
"""

import sys

from targeted_lipid_panel_calculator.cli import main

if __name__ == "__main__":
    sys.exit(main())
