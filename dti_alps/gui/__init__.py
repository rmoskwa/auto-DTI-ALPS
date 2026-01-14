"""
DTI-ALPS Processing GUI

A tkinter-based graphical interface for end-to-end DTI-ALPS analysis.
"""

import sys


def main():
    """Launch the DTI-ALPS GUI application."""
    # Check for required dependencies
    try:
        import tkinter as tk
    except ImportError:
        print("Error: tkinter is required but not installed.")
        print("On Ubuntu/Debian: sudo apt-get install python3-tk")
        sys.exit(1)

    try:
        import nibabel
        import numpy
        import scipy
    except ImportError as e:
        print(f"Error: Required package not found: {e}")
        print("Please install: pip install nibabel numpy scipy matplotlib")
        sys.exit(1)

    # Import and run application
    from .app import DTIALPSApplication

    app = DTIALPSApplication()
    app.mainloop()
