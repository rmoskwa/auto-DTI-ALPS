"""
Quality report generation module for DTI-ALPS pipeline.

This module generates CSV quality reports containing ROI-level metrics
including directional alignment (V1), angular dispersion, and fractional
anisotropy for quality assessment of DTI-ALPS analysis.

Usage:
    python -m dti_alps --report /path/to/output

Output:
    For each ROI shape found (e.g., rois_sphere3, rois_squarev9_refined):
    - quality_report_{shape}.csv
"""

import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np


@dataclass
class ROIMetrics:
    """Quality metrics for a single ROI."""

    directional_alignment: float | None = None
    angular_dispersion: float | None = None
    fa_mean: float | None = None


@dataclass
class SubjectReportData:
    """Report data for a single subject."""

    subject_id: str
    l_proj: ROIMetrics = field(default_factory=ROIMetrics)
    l_assoc: ROIMetrics = field(default_factory=ROIMetrics)
    r_proj: ROIMetrics = field(default_factory=ROIMetrics)
    r_assoc: ROIMetrics = field(default_factory=ROIMetrics)


def calculate_directional_alignment(
    v1_data: np.ndarray,
    mask: np.ndarray,
    fiber_type: str,
) -> float | None:
    """
    Calculate directional alignment of V1 with the expected fiber direction.

    For Projection ROIs: Mean(abs(V1_z)) - should be close to 1.0 (vertical fibers)
    For Association ROIs: Mean(abs(V1_y)) - should be close to 1.0 (front-to-back fibers)

    Parameters
    ----------
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    mask : np.ndarray
        Binary ROI mask
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)

    Returns
    -------
    float or None
        Mean absolute component value, or None if mask is empty
    """
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return None

    alignments = []
    for i in range(len(coords[0])):
        x, y, z = coords[0][i], coords[1][i], coords[2][i]
        v1 = v1_data[x, y, z, :]

        if fiber_type == "proj":
            # Projection fibers: Z-component (superior-inferior)
            alignments.append(np.abs(v1[2]))
        else:
            # Association fibers: Y-component (anterior-posterior)
            alignments.append(np.abs(v1[1]))

    return float(np.mean(alignments))


def calculate_angular_dispersion(
    v1_data: np.ndarray,
    mask: np.ndarray,
) -> float | None:
    """
    Calculate angular dispersion (circular standard deviation) of V1 vectors.

    Uses the spherical concentration parameter to compute angular dispersion,
    which measures how much the fiber orientations deviate from the mean direction.

    High dispersion indicates fibers fanning out (diverging), which violates
    the DTI-ALPS assumption of parallel fiber bundles.

    Parameters
    ----------
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    mask : np.ndarray
        Binary ROI mask

    Returns
    -------
    float or None
        Angular dispersion in degrees, or None if mask is empty
    """
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return None

    # Collect all V1 vectors
    vectors = []
    for i in range(len(coords[0])):
        x, y, z = coords[0][i], coords[1][i], coords[2][i]
        v1 = v1_data[x, y, z, :]
        # Normalize the vector (should already be normalized, but ensure it)
        norm = np.linalg.norm(v1)
        if norm > 0:
            vectors.append(v1 / norm)

    if len(vectors) < 2:
        return 0.0  # No dispersion with single vector

    vectors = np.array(vectors)

    # Account for antipodal symmetry (V1 and -V1 represent same direction)
    # Use the principal direction (first vector) as reference
    ref_vector = vectors[0]
    for i in range(1, len(vectors)):
        if np.dot(vectors[i], ref_vector) < 0:
            vectors[i] = -vectors[i]

    # Calculate mean direction
    mean_vector = np.mean(vectors, axis=0)
    mean_norm = np.linalg.norm(mean_vector)

    if mean_norm < 1e-10:
        return 90.0  # Vectors cancel out - maximum dispersion

    mean_direction = mean_vector / mean_norm

    # Calculate angular deviation for each vector from mean
    angles = []
    for v in vectors:
        # Clamp dot product to [-1, 1] to avoid numerical issues with arccos
        dot = np.clip(np.dot(v, mean_direction), -1.0, 1.0)
        angle_rad = np.arccos(np.abs(dot))  # abs for antipodal symmetry
        angles.append(np.degrees(angle_rad))

    return float(np.std(angles))


def calculate_fa_mean(
    fa_data: np.ndarray,
    mask: np.ndarray,
) -> float | None:
    """
    Calculate mean fractional anisotropy within ROI.

    Parameters
    ----------
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    mask : np.ndarray
        Binary ROI mask

    Returns
    -------
    float or None
        Mean FA value, or None if mask is empty
    """
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return None

    fa_values = fa_data[coords]
    return float(np.mean(fa_values))


def discover_roi_shapes(output_dir: Path) -> list[str]:
    """
    Discover all unique ROI shapes in the output directory.

    Scans subject directories to find all rois_* subdirectories
    and extracts the unique shape names.

    Parameters
    ----------
    output_dir : Path
        Path to the batch output directory

    Returns
    -------
    list of str
        Unique ROI shape names (e.g., ['sphere3', 'sphere3_refined', 'squarev9'])
    """
    shapes = set()

    for item in output_dir.iterdir():
        if not item.is_dir():
            continue
        # Skip non-subject directories
        if item.name in ["logs", "qc"]:
            continue

        # Look for rois_* directories
        for roi_dir in item.glob("rois_*"):
            if roi_dir.is_dir():
                # Extract shape name (everything after 'rois_')
                shape = roi_dir.name[5:]  # Remove 'rois_' prefix
                shapes.add(shape)

    return sorted(shapes)


def discover_subjects_for_shape(
    output_dir: Path,
    shape: str,
) -> list[tuple[str, Path]]:
    """
    Discover subjects that have ROIs for a specific shape.

    Parameters
    ----------
    output_dir : Path
        Path to the batch output directory
    shape : str
        ROI shape name (e.g., 'sphere3', 'squarev9_refined')

    Returns
    -------
    list of (subject_id, subject_dir)
        List of tuples with subject ID and path
    """
    subjects = []
    roi_dir_name = f"rois_{shape}"

    for item in sorted(output_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name in ["logs", "qc"]:
            continue

        roi_dir = item / roi_dir_name
        if roi_dir.exists():
            subjects.append((item.name, item))

    return subjects


def calculate_subject_metrics(
    subject_id: str,
    subject_dir: Path,
    shape: str,
) -> SubjectReportData | None:
    """
    Calculate quality metrics for all ROIs of a subject.

    Parameters
    ----------
    subject_id : str
        Subject identifier
    subject_dir : Path
        Path to subject output directory
    shape : str
        ROI shape name

    Returns
    -------
    SubjectReportData or None
        Report data with metrics, or None if required files are missing
    """
    # Find required files
    fa_files = list(subject_dir.glob("*_FA.nii.gz"))
    v1_files = list(subject_dir.glob("*_V1.nii.gz"))

    if not fa_files or not v1_files:
        return None

    fa_path = fa_files[0]
    v1_path = v1_files[0]

    # Load data
    fa_data = nib.load(str(fa_path)).get_fdata()
    v1_data = nib.load(str(v1_path)).get_fdata()

    # Get ROI directory
    roi_dir = subject_dir / f"rois_{shape}"
    if not roi_dir.exists():
        return None

    result = SubjectReportData(subject_id=subject_id)

    # ROI name mapping
    roi_mapping = {
        "l_proj": ("left_proj", "proj"),
        "l_assoc": ("left_assoc", "assoc"),
        "r_proj": ("right_proj", "proj"),
        "r_assoc": ("right_assoc", "assoc"),
    }

    for report_key, (roi_name, fiber_type) in roi_mapping.items():
        # Find ROI mask file
        roi_files = list(roi_dir.glob(f"*_{roi_name}.nii.gz"))
        if not roi_files:
            continue

        mask_data = nib.load(str(roi_files[0])).get_fdata()

        metrics = ROIMetrics(
            directional_alignment=calculate_directional_alignment(v1_data, mask_data, fiber_type),
            angular_dispersion=calculate_angular_dispersion(v1_data, mask_data),
            fa_mean=calculate_fa_mean(fa_data, mask_data),
        )

        setattr(result, report_key, metrics)

    return result


def write_report_csv(
    output_path: Path,
    subjects_data: list[SubjectReportData],
) -> None:
    """
    Write quality report to CSV file.

    The CSV format follows the structure in report_format.xlsx:
    - Row 1: Category headers (Directional Alignment, Angular Dispersion, FA)
    - Row 2: Column headers (Filename, l_proj, l_assoc, r_proj, r_assoc for each)
    - Data rows: One per subject

    Parameters
    ----------
    output_path : Path
        Path for output CSV file
    subjects_data : list of SubjectReportData
        Data for all subjects
    """
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)

        # Header row 1: Category headers
        header1 = [
            "",
            "",
            "",
            "Directional Alignment (V1)",
            "",
            "",
            "",
            "",
            "Angular Dispersion (V1)",
            "",
            "",
            "",
            "",
            "Fractional Anisotropy",
            "",
            "",
        ]
        writer.writerow(header1)

        # Header row 2: Column headers
        header2 = [
            "Filename",
            "",
            "l_proj",
            "l_assoc",
            "r_proj",
            "r_assoc",
            "",
            "l_proj",
            "l_assoc",
            "r_proj",
            "r_assoc",
            "",
            "l_proj",
            "l_assoc",
            "r_proj",
            "r_assoc",
        ]
        writer.writerow(header2)

        # Data rows
        for data in subjects_data:

            def fmt(val: float | None) -> str:
                return f"{val:.6f}" if val is not None else ""

            row = [
                data.subject_id,
                "",
                # Directional Alignment
                fmt(data.l_proj.directional_alignment),
                fmt(data.l_assoc.directional_alignment),
                fmt(data.r_proj.directional_alignment),
                fmt(data.r_assoc.directional_alignment),
                "",
                # Angular Dispersion
                fmt(data.l_proj.angular_dispersion),
                fmt(data.l_assoc.angular_dispersion),
                fmt(data.r_proj.angular_dispersion),
                fmt(data.r_assoc.angular_dispersion),
                "",
                # Fractional Anisotropy
                fmt(data.l_proj.fa_mean),
                fmt(data.l_assoc.fa_mean),
                fmt(data.r_proj.fa_mean),
                fmt(data.r_assoc.fa_mean),
            ]
            writer.writerow(row)


def generate_reports(
    output_dir: str,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Path]:
    """
    Generate quality reports for all ROI shapes found in output directory.

    Parameters
    ----------
    output_dir : str
        Path to the batch output directory
    log_callback : callable, optional
        Callback for log messages

    Returns
    -------
    dict of {shape: report_path}
        Mapping of ROI shape names to generated report file paths
    """
    log = log_callback or print
    output_path = Path(output_dir)

    if not output_path.exists():
        log(f"ERROR: Output directory does not exist: {output_dir}")
        return {}

    # Discover all ROI shapes
    log("Discovering ROI shapes...")
    shapes = discover_roi_shapes(output_path)

    if not shapes:
        log("No ROI shapes found in output directory.")
        return {}

    log(f"Found {len(shapes)} ROI shape(s): {', '.join(shapes)}")

    reports = {}

    for shape in shapes:
        log(f"\nGenerating report for '{shape}'...")

        # Discover subjects with this shape
        subjects = discover_subjects_for_shape(output_path, shape)
        log(f"  Found {len(subjects)} subject(s)")

        if not subjects:
            continue

        # Calculate metrics for each subject
        subjects_data = []
        for subject_id, subject_dir in subjects:
            log(f"  Processing {subject_id}...")
            data = calculate_subject_metrics(subject_id, subject_dir, shape)
            if data:
                subjects_data.append(data)
            else:
                log("    WARNING: Missing required files, skipping")

        if not subjects_data:
            log(f"  No valid subjects for shape '{shape}'")
            continue

        # Write report
        report_path = output_path / f"quality_report_{shape}.csv"
        write_report_csv(report_path, subjects_data)
        reports[shape] = report_path
        log(f"  Written: {report_path}")

    log(f"\nGenerated {len(reports)} report(s)")
    return reports


def run_report(
    output_dir: str,
    log_callback: Callable[[str], None] | None = None,
) -> None:
    """
    Entry point for report generation from CLI.

    Parameters
    ----------
    output_dir : str
        Path to the batch output directory
    log_callback : callable, optional
        Callback for log messages
    """
    log = log_callback or print

    log("=" * 60)
    log("DTI-ALPS Quality Report Generator")
    log("=" * 60)
    log(f"Output directory: {output_dir}")
    log("")

    reports = generate_reports(output_dir, log_callback)

    if reports:
        log("\n" + "=" * 60)
        log("Summary")
        log("=" * 60)
        for shape, path in reports.items():
            log(f"  {shape}: {path.name}")
    else:
        log("\nNo reports generated.")
