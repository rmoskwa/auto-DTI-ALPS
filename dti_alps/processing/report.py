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

from . import results_layout


@dataclass
class ROIMetrics:
    """Quality metrics for a single ROI."""

    directional_alignment: float | None = None
    angular_dispersion: float | None = None
    fa_mean: float | None = None
    radial_asymmetry: float | None = None  # mean(λ2/λ3) within ROI


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

    # Use orientation tensor to find mean direction
    # This properly handles antipodal symmetry (V1 and -V1 represent same direction)
    # since v⊗v is identical for v and -v
    orientation_tensor = np.zeros((3, 3))
    for v in vectors:
        orientation_tensor += np.outer(v, v)
    orientation_tensor /= len(vectors)

    # Principal eigenvector (largest eigenvalue) is the mean orientation
    eigenvalues, eigenvectors = np.linalg.eigh(orientation_tensor)
    mean_direction = eigenvectors[:, -1]  # eigh returns eigenvalues in ascending order

    # Check for highly dispersed case (eigenvalues nearly equal)
    if eigenvalues[-1] < 1e-10:
        return 90.0  # Degenerate case - maximum dispersion

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


def calculate_radial_asymmetry(
    l2_data: np.ndarray,
    l3_data: np.ndarray,
    mask: np.ndarray,
) -> float | None:
    """
    Calculate mean radial asymmetry (λ2/λ3) within ROI.

    The λ2/λ3 ratio indicates radial diffusion asymmetry and potential
    crossing fiber contamination (Wright et al. 2023, Georgiopoulos et al. 2024).
    A ratio close to 1.0 indicates isotropic radial diffusion; higher values
    suggest the presence of crossing fibers.

    Parameters
    ----------
    l2_data : np.ndarray
        Second eigenvalue data (x, y, z)
    l3_data : np.ndarray
        Third eigenvalue data (x, y, z)
    mask : np.ndarray
        Binary ROI mask

    Returns
    -------
    float or None
        Mean λ2/λ3 ratio, or None if mask is empty
    """
    coords = np.where(mask > 0)
    if len(coords[0]) == 0:
        return None

    l2_values = l2_data[coords]
    l3_values = l3_data[coords]

    # Only compute ratio where λ3 > 0 to avoid division by zero
    valid = l3_values > 0
    if not np.any(valid):
        return None

    ratios = l2_values[valid] / l3_values[valid]
    return float(np.mean(ratios))


def discover_roi_shapes(output_dir: Path) -> list[str]:
    """
    Discover all unique ROI shapes in the output directory.

    Scans subject directories to find every ROI subdirectory (the bare
    ``rois/`` default and the ``rois_*`` variants) and extracts the unique
    shape tokens.

    Parameters
    ----------
    output_dir : Path
        Path to the batch output directory

    Returns
    -------
    list of str
        Unique ROI shape names (e.g., ['rois', 'squarev9', 'squarev9_refined'])
    """
    shapes = set()

    for item in output_dir.iterdir():
        if not item.is_dir():
            continue
        # Skip non-subject directories
        if item.name in ["logs", "qc"]:
            continue

        # Recognise every ROI directory, including the bare ``rois/`` default
        # (which does not carry the ``rois_`` prefix). parse_roi_dir returns None
        # for non-ROI dirs (registration, etc.).
        for roi_dir in item.iterdir():
            if not roi_dir.is_dir():
                continue
            shape = results_layout.parse_roi_dir(roi_dir.name)
            if shape is not None:
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
    # Use the contract's name builder so the bare ``rois/`` default resolves
    # correctly (``f"rois_{shape}"`` would look for ``rois_rois``).
    roi_dir_basename = results_layout.roi_dir_name(shape)

    for item in sorted(output_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name in ["logs", "qc"]:
            continue

        roi_dir = item / roi_dir_basename
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

    # Discover optional L2/L3 files (only present for ALPS-PAS or Both)
    l2_files = list(subject_dir.glob("*_L2.nii.gz"))
    l3_files = list(subject_dir.glob("*_L3.nii.gz"))
    l2_data = nib.load(str(l2_files[0])).get_fdata() if l2_files and l3_files else None
    l3_data = nib.load(str(l3_files[0])).get_fdata() if l2_files and l3_files else None

    # Get ROI directory (via the contract, so the bare ``rois/`` default resolves)
    roi_dir = subject_dir / results_layout.roi_dir_name(shape)
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

        radial_asym = None
        if l2_data is not None and l3_data is not None:
            radial_asym = calculate_radial_asymmetry(l2_data, l3_data, mask_data)

        metrics = ROIMetrics(
            directional_alignment=calculate_directional_alignment(v1_data, mask_data, fiber_type),
            angular_dispersion=calculate_angular_dispersion(v1_data, mask_data),
            fa_mean=calculate_fa_mean(fa_data, mask_data),
            radial_asymmetry=radial_asym,
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
        # Columns: Filename, "", [4 DA], "", [4 AD], "", [4 FA], "", [4 RA]
        header1 = [
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
            "",
            "",
            "Radial Asymmetry (\u03bb2/\u03bb3)",
            "",
            "",
            "",
        ]
        writer.writerow(header1)

        # Header row 2: Column headers
        roi_cols = ["l_proj", "l_assoc", "r_proj", "r_assoc"]
        header2 = ["Filename", ""]
        for _ in range(4):  # 4 metric groups
            header2.extend(roi_cols)
            header2.append("")
        # Remove trailing empty separator
        header2.pop()
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
                "",
                # Radial Asymmetry
                fmt(data.l_proj.radial_asymmetry),
                fmt(data.l_assoc.radial_asymmetry),
                fmt(data.r_proj.radial_asymmetry),
                fmt(data.r_assoc.radial_asymmetry),
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
