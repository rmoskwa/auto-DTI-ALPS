"""
DTI-ALPS Processing GUI

A tkinter-based graphical interface for end-to-end DTI-ALPS analysis.
"""

import sys


def _check_dependencies():
    """Check for required GUI dependencies."""
    import importlib.util

    if importlib.util.find_spec("tkinter") is None:
        print("Error: tkinter is required but not installed.")
        print("On Ubuntu/Debian: sudo apt-get install python3-tk")
        sys.exit(1)

    missing_packages = []
    for pkg in ["nibabel", "numpy", "scipy"]:
        if importlib.util.find_spec(pkg) is None:
            missing_packages.append(pkg)

    if missing_packages:
        print(f"Error: Required packages not found: {', '.join(missing_packages)}")
        print("Please install: pip install nibabel numpy scipy matplotlib")
        sys.exit(1)

    # Check for PIL (required by viewer)
    if importlib.util.find_spec("PIL") is None:
        print("Error: Pillow is required but not installed.")
        print("Please install: pip install Pillow")
        sys.exit(1)


def main():
    """Launch the DTI-ALPS GUI application."""
    _check_dependencies()

    # Import and run application
    from .app import DTIALPSApplication

    app = DTIALPSApplication()
    app.mainloop()


def viewer(output_folder: str | None = None):
    """Launch the DTI-ALPS Results Viewer."""
    _check_dependencies()

    from .viewer import launch_viewer

    launch_viewer(output_folder)
