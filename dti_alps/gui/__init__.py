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


def _check_viewer_dependencies():
    """Check for Qt, required only by the Results Viewer (PRD 0010, Decision 7).

    Kept off the Tk app's ``main()`` path so the still-Tk app is never made to
    require PySide6 during the transition.
    """
    import importlib.util

    if importlib.util.find_spec("PySide6") is None:
        print("Error: PySide6 is required by the Results Viewer but not installed.")
        print('Please install: pip install "dti-alps[gui]"  (or: pip install PySide6)')
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
    _check_viewer_dependencies()

    from .viewer_qt import launch_viewer

    launch_viewer(output_folder)
