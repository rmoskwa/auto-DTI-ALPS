"""
Entry point for python -m dti_alps

Usage:
    python -m dti_alps                           # Launch GUI (default)
    python -m dti_alps --gui                     # Launch GUI explicitly
    python -m dti_alps --viewer                  # Launch Results Viewer
    python -m dti_alps --viewer /path/to/output  # Launch viewer with folder

ROI Reanalysis (post-processing with different ROI shapes):
    python -m dti_alps --reanalyze /path/to/output --sphere 3.0
    python -m dti_alps --reanalyze /path/to/output --squarev9
    python -m dti_alps --reanalyze /path/to/output --sphere 2.5 --refine
"""

import argparse
import sys


def _parse_reanalysis_args() -> argparse.Namespace:
    """Parse command line arguments for reanalysis mode."""
    parser = argparse.ArgumentParser(
        description="DTI-ALPS ROI Reanalysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --reanalyze /path/to/output --sphere 3.0
      Reanalyze with 3mm radius spherical ROIs

  %(prog)s --reanalyze /path/to/output --squarev9
      Reanalyze with 3x3 voxel square ROIs

  %(prog)s --reanalyze /path/to/output --sphere 2.5 --refine
      Reanalyze with 2.5mm spheres and ROI refinement enabled

  %(prog)s --reanalyze /path/to/output --squarev9 --refine --method ALPS-LAB
      Reanalyze with square ROIs, refinement, and only ALPS-LAB calculation
        """,
    )

    parser.add_argument(
        "--reanalyze",
        metavar="OUTPUT_DIR",
        required=True,
        help="Path to output directory containing processed subjects",
    )

    # ROI shape options (mutually exclusive)
    shape_group = parser.add_mutually_exclusive_group(required=True)
    shape_group.add_argument(
        "--sphere",
        type=float,
        metavar="RADIUS",
        help="Create spherical ROIs with given radius in millimeters",
    )
    shape_group.add_argument(
        "--squarev9",
        action="store_true",
        help="Create 3x3 voxel square ROIs in the axial plane (9 voxels total)",
    )

    parser.add_argument(
        "--refine",
        action="store_true",
        help="Enable ROI refinement based on fiber orientation",
    )

    parser.add_argument(
        "--method",
        choices=["ALPS-LAB", "ALPS-PAS", "Both"],
        default="Both",
        help="ALPS calculation method (default: Both)",
    )

    parser.add_argument(
        "--fa-threshold",
        type=float,
        default=0.2,
        metavar="THRESHOLD",
        help="FA threshold for filtering CSF voxels (default: 0.2)",
    )

    return parser.parse_args()


def _run_reanalysis() -> None:
    """Run ROI reanalysis from command line arguments."""
    args = _parse_reanalysis_args()

    from .processing.reanalysis import ROIShape, run_reanalysis

    # Create ROI shape configuration
    if args.sphere:
        roi_shape = ROIShape(shape_type="sphere", sphere_radius=args.sphere)
    else:
        roi_shape = ROIShape(shape_type="squarev9")

    # Run reanalysis
    run_reanalysis(
        output_dir=args.reanalyze,
        roi_shape=roi_shape,
        enable_refinement=args.refine,
        alps_method=args.method,
        fa_threshold=args.fa_threshold,
    )


def main():
    """Main entry point that dispatches to GUI, viewer, or reanalysis."""
    # Check for reanalysis mode first (needs argparse)
    if len(sys.argv) >= 2 and sys.argv[1] == "--reanalyze":
        _run_reanalysis()
        return

    # Check if viewer mode
    if len(sys.argv) >= 2 and sys.argv[1] == "--viewer":
        from .gui.viewer import launch_viewer

        # Check if output folder path was provided
        output_folder = sys.argv[2] if len(sys.argv) > 2 else None
        launch_viewer(output_folder)
        return

    # Check for help on reanalysis
    if len(sys.argv) >= 2 and sys.argv[1] in ["--help", "-h"]:
        print(__doc__)
        print("\nFor reanalysis options, use: python -m dti_alps --reanalyze --help")
        return

    # Default: Launch GUI
    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
