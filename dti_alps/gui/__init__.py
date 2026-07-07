"""
DTI-ALPS Processing GUI

A tkinter-based graphical interface for end-to-end DTI-ALPS analysis.
"""

import sys


def _check_science_deps():
    """Check for the numpy/nibabel/scipy science stack (toolkit-independent).

    Factored out of :func:`_check_dependencies` so the Qt entry points
    (``main_qt`` / the viewer) can validate the science stack without also
    requiring tkinter (PRD 0013, Decision 12).
    """
    import importlib.util

    missing_packages = []
    for pkg in ["nibabel", "numpy", "scipy"]:
        if importlib.util.find_spec(pkg) is None:
            missing_packages.append(pkg)

    if missing_packages:
        print(f"Error: Required packages not found: {', '.join(missing_packages)}")
        print("Please install: pip install nibabel numpy scipy")
        sys.exit(1)


def _check_dependencies():
    """Check for required GUI dependencies (Tk app)."""
    import importlib.util

    if importlib.util.find_spec("tkinter") is None:
        print("Error: tkinter is required but not installed.")
        print("On Ubuntu/Debian: sudo apt-get install python3-tk")
        sys.exit(1)

    _check_science_deps()


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


def main_qt():
    """Launch the PySide6 main application (temporary ``--gui-qt`` entry point).

    Mirrors :func:`viewer`: it validates PySide6 (same message as the viewer)
    plus the science stack, and never requires tkinter. Removed at the final
    flip when ``--gui`` points at the Qt window (PRD 0013, Decision 12).
    """
    _check_viewer_dependencies()
    _check_science_deps()

    from .app_qt import launch_app_qt

    launch_app_qt()


def viewer(output_folder: str | None = None):
    """Launch the DTI-ALPS Results Viewer."""
    _check_dependencies()
    _check_viewer_dependencies()

    from .viewer import launch_viewer

    launch_viewer(output_folder)
