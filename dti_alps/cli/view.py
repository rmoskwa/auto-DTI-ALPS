"""
The ``view`` verb: launch the results viewer.

The one CLI module that reaches for a GUI toolkit, and it does so only inside
:func:`execute` -- importing this module must still leave Qt unloaded, so the
``run`` verb keeps working on a display-less compute node with no PySide6 import
cost. The Qt dependency check runs before the viewer import so a missing or
unloadable PySide6 fails with actionable guidance rather than a raw traceback.
"""

import argparse


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Wire the ``view`` flags onto ``parser``."""
    parser.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
        nargs="?",
        default=None,
        help="Output directory to open (optional; the viewer can browse for one)",
    )


def execute(args: argparse.Namespace) -> int:
    """Launch the results viewer, optionally pre-loaded with an output folder."""
    from ..gui import _check_viewer_dependencies

    _check_viewer_dependencies()
    from ..gui.viewer import launch_viewer

    launch_viewer(args.output_dir)
    return 0
