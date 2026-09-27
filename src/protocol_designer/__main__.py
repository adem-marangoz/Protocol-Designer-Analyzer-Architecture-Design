"""Entry point: ``python -m protocol_designer [file.pdproj]`` starts the GUI.

``python -m protocol_designer cli ...`` runs the command line interface.
"""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        from .cli import main as cli_main

        return cli_main(sys.argv[2:])
    from .ui.app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
