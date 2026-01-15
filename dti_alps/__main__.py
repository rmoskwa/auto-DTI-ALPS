"""
Entry point for python -m dti_alps

Usage:
    python -m dti_alps                      # Launch GUI (default)
    python -m dti_alps --gui                # Launch GUI explicitly
    python -m dti_alps --viewer             # Launch Results Viewer
    python -m dti_alps --viewer /path/to/output  # Launch viewer with folder
    python -m dti_alps FA.nii.gz V1.nii.gz  # Run CLI with arguments
"""

import sys


def main():
    """Main entry point that dispatches to GUI, viewer, or CLI."""
    # Check if viewer mode
    if len(sys.argv) >= 2 and sys.argv[1] == "--viewer":
        from .gui.viewer import launch_viewer

        # Check if output folder path was provided
        output_folder = sys.argv[2] if len(sys.argv) > 2 else None
        launch_viewer(output_folder)
        return

    # Check if GUI mode or no args (default to GUI)
    if len(sys.argv) == 1 or (len(sys.argv) == 2 and sys.argv[1] == "--gui"):
        # Launch GUI
        from .gui import main as gui_main

        gui_main()
    elif sys.argv[1] == "--gui":
        # --gui with extra args: strip --gui and launch GUI
        sys.argv.pop(1)
        from .gui import main as gui_main

        gui_main()
    else:
        # Run CLI
        from .cli import main as cli_main

        cli_main()


if __name__ == "__main__":
    main()
