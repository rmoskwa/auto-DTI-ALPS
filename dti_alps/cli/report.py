"""
The ``report`` verb: generate ROI quality reports over a finished output
directory.

Reads the ROI masks and metric maps each subject already has on disk and writes
one ``quality_report_{shape}.csv`` per ROI shape found.
"""

import argparse


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Wire the ``report`` flags onto ``parser``."""
    parser.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
        help="Path to output directory containing processed subjects",
    )


def execute(args: argparse.Namespace) -> int:
    """Generate a quality report for every ROI shape in the output directory."""
    from ..processing.report import run_report

    run_report(args.output_dir)
    return 0
