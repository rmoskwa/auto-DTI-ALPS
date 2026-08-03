"""
The ``reanalyze`` verb: re-run ROI placement + ALPS calculation over an output
directory the pipeline already produced.

Reanalysis reuses each subject's existing tensors and warps, so it changes ROI
geometry, placement method, ALPS method or FA threshold without repeating the
hours of preprocessing that produced them.
"""

import argparse

from ..processing.constants import (
    ALPS_METHODS,
    DEFAULT_ALPS_METHOD,
    DEFAULT_ROI_METHOD,
    FA_THRESHOLD,
    ROI_METHOD_OPTIONS,
    AdaptiveSearchConfig,
)
from .validators import (
    SEARCH_MAX,
    SEARCH_MIN,
    SPHERE_RADIUS_MAX,
    SPHERE_RADIUS_MIN,
    validate_search_value,
    validate_sphere_radii,
)

EPILOG = """
Output naming:
  Standard placement: rois_{shape}/ and alps_results_{shape}.csv
  Adaptive placement: rois_{shape}_adaptive/ and alps_results_{shape}_adaptive.csv
  --roi-method Both writes both pairs, one per pass.
  The default 3 mm sphere collapses to the bare rois/ and alps_results.csv.

Examples:
  %(prog)s /path/to/output --sphere 3.0
      Reanalyze with 3mm radius spherical ROIs

  %(prog)s /path/to/output --squarev9
      Reanalyze with 3x3 voxel square ROIs (9 voxels)

  %(prog)s /path/to/output --squarev4
      Reanalyze with 2x2 voxel square ROIs (4 voxels, V1-optimized)

  %(prog)s /path/to/output --sphere 2.5 --roi-method Adaptive
      Reanalyze with 2.5mm spheres, adaptive placement only

  %(prog)s /path/to/output --sphere 2,3,4
      Reanalyze with 2mm, 3mm, and 4mm spheres in one run

  %(prog)s /path/to/output --sphere 3 --squarev4
      Reanalyze with both 3mm sphere and 2x2 square ROIs

  %(prog)s /path/to/output --squarev9 --roi-method Standard --method ALPS-LAB
      Reanalyze with square ROIs, standard placement, and only ALPS-LAB
"""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Wire the ``reanalyze`` flags onto ``parser``."""
    search_defaults = AdaptiveSearchConfig()

    parser.add_argument(
        "output_dir",
        metavar="OUTPUT_DIR",
        help="Path to output directory containing processed subjects",
    )

    # ROI shape options (can be combined)
    parser.add_argument(
        "--sphere",
        type=validate_sphere_radii,
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
        "--roi-method",
        choices=ROI_METHOD_OPTIONS,
        default=DEFAULT_ROI_METHOD,
        help=(
            f"ROI placement method; Both runs each pass and writes a CSV per pass "
            f"(default: {DEFAULT_ROI_METHOD}). The run verb takes the same flag."
        ),
    )

    # Adaptive search envelope. Each is validated to the shared 1-4 range and
    # defaults to the historical value; all are inert on the Standard pass,
    # which runs no search.
    search_help_suffix = (
        f"(±voxels, {SEARCH_MIN}-{SEARCH_MAX}; inert unless Adaptive placement runs)"
    )
    parser.add_argument(
        "--search-x",
        type=validate_search_value,
        default=search_defaults.search_x,
        metavar="N",
        help=f"Adaptive search window in X {search_help_suffix}",
    )
    parser.add_argument(
        "--search-y",
        type=validate_search_value,
        default=search_defaults.search_y,
        metavar="N",
        help=f"Adaptive search window in Y {search_help_suffix}",
    )
    parser.add_argument(
        "--search-z",
        type=validate_search_value,
        default=search_defaults.search_z,
        metavar="N",
        help=f"Adaptive search window in Z {search_help_suffix}",
    )
    parser.add_argument(
        "--max-y-drift",
        type=validate_search_value,
        default=search_defaults.max_y_drift,
        metavar="N",
        help=f"Max association-ROI Y drift from projection ROI {search_help_suffix}",
    )
    parser.add_argument(
        "--max-z-drift",
        type=validate_search_value,
        default=search_defaults.max_z_drift,
        metavar="N",
        help=f"Max association-ROI Z drift from projection ROI {search_help_suffix}",
    )

    parser.add_argument(
        "--method",
        choices=ALPS_METHODS,
        default=DEFAULT_ALPS_METHOD,
        help=f"ALPS calculation method (default: {DEFAULT_ALPS_METHOD})",
    )

    parser.add_argument(
        "--fa-threshold",
        type=float,
        default=FA_THRESHOLD,
        metavar="THRESHOLD",
        help=f"FA threshold for filtering CSF voxels (default: {FA_THRESHOLD})",
    )


def execute(args: argparse.Namespace) -> int:
    """Run reanalysis for every ROI shape named on the command line."""
    from ..processing.reanalysis import ROIShape, run_reanalysis

    # Assemble the envelope from the (validated, defaulted) flags. The 1-4 guard
    # already fired during parse; this construction cannot raise. Inert unless
    # an Adaptive pass runs.
    search = AdaptiveSearchConfig(
        search_x=args.search_x,
        search_y=args.search_y,
        search_z=args.search_z,
        max_y_drift=args.max_y_drift,
        max_z_drift=args.max_z_drift,
    )

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
        return 1

    # Run reanalysis for each shape
    for roi_shape in roi_shapes:
        if len(roi_shapes) > 1:
            print(f"\n{'=' * 60}")
            print(f"Reanalysis: {roi_shape.name}")
            print(f"{'=' * 60}\n")

        run_reanalysis(
            output_dir=args.output_dir,
            roi_shape=roi_shape,
            roi_method=args.roi_method,
            alps_method=args.method,
            fa_threshold=args.fa_threshold,
            search=search,
        )

    return 0
