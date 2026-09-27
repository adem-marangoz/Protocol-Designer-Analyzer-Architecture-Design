"""PyInstaller entry point for pdcli.exe (console)."""

import sys

from protocol_designer.cli import main

if __name__ == "__main__":
    sys.exit(main())
