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


RELEASES_URL = "https://github.com/rmoskwa/auto-DTI-ALPS/releases"


def qt_load_failure_message(exc: BaseException) -> list[str]:
    """Build the operator-facing lines for a PySide6 that imports but won't load.

    Kept as a pure, Qt-free function returning lines (not printing) so the
    wording is testable without a Qt install. The failure it explains is a host
    system-library problem, not a DTI-ALPS defect: PySide6 is present, but
    loading Qt pulls in shared libraries from the surrounding environment, and a
    mismatched set there (classically a conda prefix whose ``harfbuzz`` is newer
    than its ``freetype``) aborts the import.
    """
    return [
        "Error: PySide6 is installed, but its Qt libraries failed to load.",
        f"  {exc}",
        "",
        "This is a problem with the shared libraries in the current environment,",
        "not with DTI-ALPS itself. Common fixes, in order of preference:",
        "",
        "  1. Install outside conda, in an isolated environment:",
        "       conda deactivate",
        "       pipx install dti-alps",
        "",
        "  2. Use the AppImage, which bundles its own Qt (no Python needed):",
        f"       {RELEASES_URL}",
        "",
        "  3. If you must stay in a conda environment, realign its font stack:",
        '       conda install -c conda-forge "freetype>=2.12" harfbuzz --update-deps',
    ]


def _check_viewer_dependencies():
    """Check for PySide6 (Qt), required by the whole GUI (main window + viewer).

    Named for its original PRD 0010 role (Qt was first required only by the
    viewer); since the PRD 0013 flip the main window is Qt too, so this is the
    GUI-wide Qt check.

    Two distinct failures are separated here: PySide6 not installed at all
    (``find_spec`` misses), and PySide6 installed but unable to load its Qt
    shared libraries. ``find_spec`` cannot see the second -- it only locates the
    package, never executes it -- so the Qt libraries are pulled in explicitly
    below, while there is still a plain stdout to report on.
    """
    import importlib.util

    if importlib.util.find_spec("PySide6") is None:
        print("Error: PySide6 is required by the DTI-ALPS GUI but not installed.")
        print('Please install: pip install "dti-alps[gui]"  (or: pip install PySide6)')
        sys.exit(1)

    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError as exc:
        for line in qt_load_failure_message(exc):
            print(line)
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
