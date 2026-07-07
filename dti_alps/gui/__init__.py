"""
DTI-ALPS Processing GUI

A PySide6 (Qt) graphical interface for end-to-end DTI-ALPS analysis.
"""

import sys


def _check_science_deps():
    """Check for the numpy/nibabel/scipy science stack (toolkit-independent).

    Factored out of :func:`_check_dependencies` so the viewer entry point can
    validate the science stack independently (PRD 0013, Decision 12).
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


def _check_viewer_dependencies():
    """Check for PySide6 (Qt), required by the whole GUI (main window + viewer).

    Named for its original PRD 0010 role (Qt was first required only by the
    viewer); since the PRD 0013 flip the main window is Qt too, so this is the
    GUI-wide Qt check.
    """
    import importlib.util

    if importlib.util.find_spec("PySide6") is None:
        print("Error: PySide6 is required by the DTI-ALPS GUI but not installed.")
        print('Please install: pip install "dti-alps[gui]"  (or: pip install PySide6)')
        sys.exit(1)


def _check_dependencies():
    """Check for required GUI dependencies (PySide6 + the science stack)."""
    _check_viewer_dependencies()
    _check_science_deps()


def main():
    """Launch the DTI-ALPS GUI application."""
    _check_dependencies()

    from .app import launch_app

    launch_app()


def viewer(output_folder: str | None = None):
    """Launch the DTI-ALPS Results Viewer."""
    _check_dependencies()

    from .viewer import launch_viewer

    launch_viewer(output_folder)
