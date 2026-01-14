"""
Command-line interface for DTI-ALPS automatic ROI detection.
"""

import argparse

from .detector import DTIALPSDetector
from .visualization import load_human_rois, visualize_results


def main():
    """Main CLI entry point for DTI-ALPS ROI detection."""
    parser = argparse.ArgumentParser(
        description="Automatic DTI-ALPS ROI Placement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example usage:
  python -m dti_alps case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz
  python -m dti_alps case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz --output-dir results
  python -m dti_alps case1/DTIdata_FA.nii.gz case1/DTIdata_V1.nii.gz --compare-human case1
        """,
    )

    parser.add_argument("fa_path", help="Path to FA NIfTI file")
    parser.add_argument("v1_path", help="Path to V1 (principal eigenvector) NIfTI file")
    parser.add_argument("--output-dir", "-o", help="Directory to save ROI masks")
    parser.add_argument("--output-prefix", default="auto", help="Prefix for output files")
    parser.add_argument("--compare-human", help="Directory with human ROI masks for comparison")
    parser.add_argument("--visualization", "-v", help="Path to save visualization image")
    parser.add_argument(
        "--fa-thresh", type=float, default=0.25, help="FA threshold (default: 0.25)"
    )
    parser.add_argument(
        "--orient-thresh", type=float, default=0.7, help="Orientation threshold (default: 0.7)"
    )
    parser.add_argument("--min-width", type=int, default=5, help="Minimum zone width (default: 5)")
    parser.add_argument(
        "--roi-radius",
        type=float,
        default=3.0,
        help="Spherical ROI radius in millimeters (default: 3.0)",
    )
    parser.add_argument(
        "--z-tolerance",
        type=int,
        default=2,
        help="Z-alignment tolerance in voxels for head tilt (default: 2)",
    )

    args = parser.parse_args()

    # Create detector
    detector = DTIALPSDetector(
        fa_thresh=args.fa_thresh,
        orient_thresh=args.orient_thresh,
        min_zone_width=args.min_width,
        roi_radius_mm=args.roi_radius,
        z_tolerance=args.z_tolerance,
    )

    # Load data and run detection
    detector.load_data(args.fa_path, args.v1_path)
    detector.find_candidates()
    detector.select_optimal_rois()

    # Compute ALPS statistics
    detector.compute_alps_index()

    # Save ROI masks if requested
    if args.output_dir:
        detector.save_roi_masks(args.output_dir, args.output_prefix)

    # Load human ROIs for comparison if provided
    human_rois = None
    if args.compare_human:
        human_rois = load_human_rois(args.compare_human)
        if human_rois:
            print("\nHuman ROI centers (for comparison):")
            for name, center in human_rois.items():
                print(f"  {name}: {center}")

    # Create visualization
    if args.visualization or args.compare_human:
        viz_path = args.visualization or "dti_alps_visualization.png"
        visualize_results(detector, viz_path, human_rois)


if __name__ == "__main__":
    main()
