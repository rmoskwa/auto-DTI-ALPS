"""
Entry point for python -m dti_alps

Usage:
    python -m dti_alps                           # Launch GUI (default)
    python -m dti_alps --gui                     # Launch GUI explicitly
    python -m dti_alps --viewer                  # Launch Results Viewer
    python -m dti_alps --viewer /path/to/output  # Launch viewer with folder
    python -m dti_alps --report /path/to/output  # Generate quality reports

ROI Reanalysis (post-processing with different ROI shapes):
    python -m dti_alps --reanalyze /path/to/output --sphere 3.0
    python -m dti_alps --reanalyze /path/to/output --squarev9
    python -m dti_alps --reanalyze /path/to/output --squarev4
    python -m dti_alps --reanalyze /path/to/output --sphere 2.5 --adaptive
    python -m dti_alps --reanalyze /path/to/output --sphere 2,3,4
    python -m dti_alps --reanalyze /path/to/output --sphere 3 --squarev4

Output naming:
    Without --adaptive: rois_{shape}/ and alps_results_{shape}.csv
    With --adaptive:    rois_{shape}_adaptive/ and alps_results_{shape}_adaptive.csv
    The default 3 mm sphere collapses to the bare rois/ and alps_results.csv.

    Examples:
        --sphere 3          -> rois/, alps_results.csv
        --sphere 3 --adaptive -> rois_adaptive/, alps_results_rois_adaptive.csv
        --squarev9          -> rois_squarev9/, alps_results_squarev9.csv
        --squarev9 --adaptive -> rois_squarev9_adaptive/, alps_results_squarev9_adaptive.csv
        --squarev4          -> rois_squarev4/, alps_results_squarev4.csv
        --sphere 2.5        -> rois_sphere2p5/, alps_results_sphere2p5.csv
        --sphere 2.5 --adaptive -> rois_sphere2p5_adaptive/, alps_results_sphere2p5_adaptive.csv

Quality Report Generation:
    python -m dti_alps --report /path/to/output
        Generates quality_report_{shape}.csv for each ROI shape found.
        Reports include:
        - Directional Alignment (V1): How well fibers align with expected direction
        - Angular Dispersion (V1): Standard deviation of fiber angles
        - Fractional Anisotropy: Mean FA within each ROI
"""

import argparse
import sys

from .processing.constants import ROI_SPHERE_RADIUS_RANGE

# Sphere radius validation bounds, read from the engine's single source of truth.
SPHERE_RADIUS_MIN, SPHERE_RADIUS_MAX = ROI_SPHERE_RADIUS_RANGE


def _validate_sphere_radii(value: str) -> list[float]:
    """Validate comma-separated sphere radii are within allowed range."""
    radii = []
    for part in value.split(","):
        part = part.strip()
        try:
            radius = float(part)
        except ValueError as err:
            raise argparse.ArgumentTypeError(f"invalid float value: '{part}'") from err

        if radius < SPHERE_RADIUS_MIN or radius > SPHERE_RADIUS_MAX:
            raise argparse.ArgumentTypeError(
                f"radius must be between {SPHERE_RADIUS_MIN} and {SPHERE_RADIUS_MAX} mm, "
                f"got {radius}"
            )
        radii.append(radius)
    return radii


def _parse_reanalysis_args() -> argparse.Namespace:
    """Parse command line arguments for reanalysis mode."""
    from .processing.constants import FA_THRESHOLD

    parser = argparse.ArgumentParser(
        description="DTI-ALPS ROI Reanalysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --reanalyze /path/to/output --sphere 3.0
      Reanalyze with 3mm radius spherical ROIs

  %(prog)s --reanalyze /path/to/output --squarev9
      Reanalyze with 3x3 voxel square ROIs (9 voxels)

  %(prog)s --reanalyze /path/to/output --squarev4
      Reanalyze with 2x2 voxel square ROIs (4 voxels, V1-optimized)

  %(prog)s --reanalyze /path/to/output --sphere 2.5 --adaptive
      Reanalyze with 2.5mm spheres and adaptive ROI placement enabled

  %(prog)s --reanalyze /path/to/output --sphere 2,3,4
      Reanalyze with 2mm, 3mm, and 4mm spheres in one run

  %(prog)s --reanalyze /path/to/output --sphere 3 --squarev4
      Reanalyze with both 3mm sphere and 2x2 square ROIs

  %(prog)s --reanalyze /path/to/output --squarev9 --adaptive --method ALPS-LAB
      Reanalyze with square ROIs, adaptive placement, and only ALPS-LAB calculation
        """,
    )

    parser.add_argument(
        "--reanalyze",
        metavar="OUTPUT_DIR",
        required=True,
        help="Path to output directory containing processed subjects",
    )

    # ROI shape options (can be combined)
    parser.add_argument(
        "--sphere",
        type=_validate_sphere_radii,
        metavar="RADIUS[,RADIUS,...]",
        help=(
            f"Create spherical ROIs with given radius/radii "
            f"({SPHERE_RADIUS_MIN}-{SPHERE_RADIUS_MAX} mm). "
            f"Comma-separated for multiple (e.g., --sphere 2,3,4)"
        ),
    )
    parser.add_argument(
        "--squarev9",
        action="store_true",
        help="Create 3x3 voxel square ROIs in the axial plane (9 voxels total)",
    )
    parser.add_argument(
        "--squarev4",
        action="store_true",
        help="Create 2x2 voxel square ROIs in the axial plane (4 voxels, V1-optimized)",
    )

    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="Enable adaptive ROI placement based on fiber orientation",
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
        default=FA_THRESHOLD,
        metavar="THRESHOLD",
        help=f"FA threshold for filtering CSF voxels (default: {FA_THRESHOLD})",
    )

    return parser.parse_args()


def _run_reanalysis() -> None:
    """Run ROI reanalysis from command line arguments."""
    args = _parse_reanalysis_args()

    from .processing.reanalysis import ROIShape, run_reanalysis

    # Build list of ROI shapes from all specified flags
    roi_shapes: list[ROIShape] = []
    if args.sphere:
        for radius in args.sphere:
            roi_shapes.append(ROIShape(shape_type="sphere", sphere_radius=radius))
    if args.squarev9:
        roi_shapes.append(ROIShape(shape_type="squarev9"))
    if args.squarev4:
        roi_shapes.append(ROIShape(shape_type="squarev4"))

    if not roi_shapes:
        print(
            "ERROR: At least one ROI shape must be specified (--sphere, --squarev9, or --squarev4)"
        )
        sys.exit(1)

    # Run reanalysis for each shape
    for roi_shape in roi_shapes:
        if len(roi_shapes) > 1:
            print(f"\n{'=' * 60}")
            print(f"Reanalysis: {roi_shape.name}")
            print(f"{'=' * 60}\n")

        run_reanalysis(
            output_dir=args.reanalyze,
            roi_shape=roi_shape,
            enable_adaptive=args.adaptive,
            alps_method=args.method,
            fa_threshold=args.fa_threshold,
        )


def main():
    """Main entry point that dispatches to GUI, viewer, report, or reanalysis."""
    # Check for reanalysis mode first (needs argparse)
    if len(sys.argv) >= 2 and sys.argv[1] == "--reanalyze":
        _run_reanalysis()
        return

    # Check if viewer mode
    if len(sys.argv) >= 2 and sys.argv[1] == "--viewer":
        # Validate Qt up front so a missing PySide6 fails with a clear,
        # actionable message instead of a raw import traceback (Decision 7).
        from .gui import _check_viewer_dependencies

        _check_viewer_dependencies()
        from .gui.viewer import launch_viewer

        # Check if output folder path was provided
        output_folder = sys.argv[2] if len(sys.argv) > 2 else None
        launch_viewer(output_folder)
        return

    # Check if report mode
    if len(sys.argv) >= 2 and sys.argv[1] == "--report":
        from .processing.report import run_report

        if len(sys.argv) < 3:
            print("ERROR: --report requires an output directory path")
            print("Usage: python -m dti_alps --report /path/to/output")
            sys.exit(1)

        output_folder = sys.argv[2]
        run_report(output_folder)
        return

    # Check for help on reanalysis
    if len(sys.argv) >= 2 and sys.argv[1] in ["--help", "-h"]:
        print(__doc__)
        print("\nFor reanalysis options, use: python -m dti_alps --reanalyze --help")
        return

    # GUI mode: launched explicitly with --gui, or as the default with no args.
    # Reject anything else rather than silently launching the GUI.
    if len(sys.argv) >= 2 and sys.argv[1] != "--gui":
        print(f"ERROR: unknown option '{sys.argv[1]}'")
        print(__doc__)
        sys.exit(2)

    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()
