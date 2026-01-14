"""
Entry point for python -m dti_alps

Usage:
    python -m dti_alps                      # Launch GUI (default)
    python -m dti_alps --gui                # Launch GUI explicitly
    python -m dti_alps FA.nii.gz V1.nii.gz  # Run CLI with arguments
"""

import sys


def main():
    """Main entry point that dispatches to GUI or CLI."""
    # Check if GUI mode or no args (default to GUI)
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] == '--gui'):
        # Launch GUI
        from .gui import main as gui_main
        gui_main()
    elif sys.argv[1] == '--gui':
        # --gui with extra args: strip --gui and launch GUI
        sys.argv.pop(1)
        from .gui import main as gui_main
        gui_main()
    else:
        # Run CLI
        from .cli import main as cli_main
        cli_main()


if __name__ == '__main__':
    main()
