"""PyInstaller entry point for ProtocolDesigner.exe (windowed)."""

import sys

from protocol_designer.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
