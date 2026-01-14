"""
DTI-ALPS Processing GUI

A tkinter-based graphical interface for end-to-end DTI-ALPS analysis.
"""

import sys


def main():
    """Launch the DTI-ALPS GUI application."""
    import importlib.util

    # Check for required dependencies
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

    # Import and run application
    from .app import DTIALPSApplication

    app = DTIALPSApplication()
    app.mainloop()
