#!/usr/bin/env python3
"""
Launcher script for DTI-ALPS GUI.

Usage:
    python run_gui.py
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))

from dti_alps_gui.main import main

if __name__ == "__main__":
    main()
