"""
Entry point for python -m dti_alps

Usage:
    python -m dti_alps                           # Launch GUI (default)
    python -m dti_alps --gui                     # Launch GUI explicitly
    python -m dti_alps --viewer                  # Launch Results Viewer
    python -m dti_alps --viewer /path/to/output  # Launch viewer with folder
"""

import sys


def main():
    """Main entry point that dispatches to GUI or viewer."""
    # Check if viewer mode
    if len(sys.argv) >= 2 and sys.argv[1] == "--viewer":
        from .gui.viewer import launch_viewer

        # Check if output folder path was provided
        output_folder = sys.argv[2] if len(sys.argv) > 2 else None
        launch_viewer(output_folder)
        return

    # Default: Launch GUI
    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
