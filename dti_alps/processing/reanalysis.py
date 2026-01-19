"""
ROI reanalysis module for DTI-ALPS pipeline.

This module allows re-running ROI placement and ALPS calculation on
already-processed data with different ROI shapes, without repeating
the preprocessing and registration steps.

Usage:
    python -m dti_alps --reanalyze /path/to/output --sphere 3.0
    python -m dti_alps --reanalyze /path/to/output --squarev9
    python -m dti_alps --reanalyze /path/to/output --sphere 2.5 --refine
"""

import csv
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from .alps_calculation import calculate_alps_lab, calculate_alps_pas
from .registration.base import (
    calculate_roi_quality,
    create_sphere_mask,
    create_square_v9_mask,
    find_mask_centroid,
    get_roi_template_paths,
    refine_roi_placement,
)


@dataclass
class ROIShape:
    """ROI shape configuration."""

    shape_type: str  # "sphere" or "squarev9"
    sphere_radius: float | None = None  # Only for sphere type

    @property
    def name(self) -> str:
        """Get a short name for folder/file naming."""
        if self.shape_type == "sphere":
            # Format radius: 3.0 -> "sphere3", 2.5 -> "sphere2p5"
            r_str = str(self.sphere_radius).replace(".", "p").rstrip("p0")
            return f"sphere{r_str}"
        return "squarev9"


@dataclass
class ReanalysisResult:
    """Result of reanalysis for a single subject."""

    subject_id: str
    status: str  # "completed", "failed", "skipped"
    alps_lab_left: float | None = None
    alps_lab_right: float | None = None
    alps_lab_bilateral: float | None = None
    alps_pas_left: float | None = None
    alps_pas_right: float | None = None
    alps_pas_bilateral: float | None = None
    error_message: str | None = None


def discover_processed_subjects(output_dir: str) -> list[tuple[str, Path]]:
    """
    Discover subjects in the output directory that have completed registration.

    A subject is considered processed if it has:
    - A registration/ subdirectory with inverse warp file
    - FA, tensor, and eigenvector files

    Parameters
    ----------
    output_dir : str
        Path to the batch output directory

    Returns
    -------
    list of (subject_id, subject_dir)
        List of tuples with subject ID and path to subject directory
    """
    output_path = Path(output_dir)
    subjects = []

    for item in sorted(output_path.iterdir()):
        if not item.is_dir():
            continue

        # Skip non-subject directories
        if item.name in ["logs", "qc"]:
            continue

        # Check for required files
        reg_dir = item / "registration"
        if not reg_dir.exists():
            continue

        # Look for inverse warp file
        inverse_warp = None
        for f in reg_dir.glob("*_jhu2subject_warp_coef.nii.gz"):
            inverse_warp = f
            break

        if inverse_warp is None:
            continue

        # Check for FA file
        fa_files = list(item.glob("*_FA.nii.gz"))
        if not fa_files:
            continue

        subjects.append((item.name, item))

    return subjects


def _get_fsl_bin_dir() -> Path | None:
    """Get path to FSL bin directory."""
    import shutil

    fsldir = os.environ.get("FSLDIR")
    if fsldir and os.path.isdir(fsldir):
        bin_dir = Path(fsldir) / "bin"
        if bin_dir.exists():
            return bin_dir

    # Try to find applywarp in PATH
    applywarp = shutil.which("applywarp")
    if applywarp:
        return Path(applywarp).parent

    return None


def reanalyze_subject(
    subject_id: str,
    subject_dir: Path,
    roi_shape: ROIShape,
    enable_refinement: bool,
    alps_method: str,
    fa_threshold: float,
    log_callback: Callable[[str], None] | None = None,
) -> ReanalysisResult:
    """
    Reanalyze a single subject with new ROI shape.

    Parameters
    ----------
    subject_id : str
        Subject identifier
    subject_dir : Path
        Path to subject output directory
    roi_shape : ROIShape
        ROI shape configuration
    enable_refinement : bool
        Whether to enable ROI refinement
    alps_method : str
        ALPS calculation method ("ALPS-LAB", "ALPS-PAS", or "Both")
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback for log messages

    Returns
    -------
    ReanalysisResult
        Result of the reanalysis
    """
    log = log_callback or (lambda x: None)
    result = ReanalysisResult(subject_id=subject_id, status="running")

    try:
        # Find required files
        reg_dir = subject_dir / "registration"

        # Find inverse warp
        inverse_warp = None
        for f in reg_dir.glob("*_jhu2subject_warp_coef.nii.gz"):
            inverse_warp = f
            break

        if inverse_warp is None:
            result.status = "failed"
            result.error_message = "Inverse warp not found"
            return result

        # Find FA file
        fa_path = None
        for f in subject_dir.glob("*_FA.nii.gz"):
            fa_path = f
            break

        if fa_path is None:
            result.status = "failed"
            result.error_message = "FA file not found"
            return result

        # Find tensor file
        tensor_path = None
        for f in subject_dir.glob("*_tensor.nii.gz"):
            tensor_path = f
            break

        if tensor_path is None:
            result.status = "failed"
            result.error_message = "Tensor file not found"
            return result

        # Find eigenvector files (for ALPS-PAS and refinement)
        v1_path = v2_path = v3_path = l2_path = l3_path = None
        for f in subject_dir.glob("*_V1.nii.gz"):
            v1_path = f
        for f in subject_dir.glob("*_V2.nii.gz"):
            v2_path = f
        for f in subject_dir.glob("*_V3.nii.gz"):
            v3_path = f
        for f in subject_dir.glob("*_L2.nii.gz"):
            l2_path = f
        for f in subject_dir.glob("*_L3.nii.gz"):
            l3_path = f

        # Get FSL bin directory
        fsl_bin = _get_fsl_bin_dir()
        if fsl_bin is None:
            result.status = "failed"
            result.error_message = "FSL not found"
            return result

        # Get ROI templates
        roi_templates = get_roi_template_paths()
        if roi_templates is None:
            result.status = "failed"
            result.error_message = "ROI templates not found"
            return result

        # Load FA for reference shape and voxel size
        fa_img = nib.load(fa_path)
        fa_data = fa_img.get_fdata()
        ref_shape = fa_data.shape[:3]
        voxel_size = fa_img.header.get_zooms()[:3]

        # Load V1 for refinement if enabled
        v1_data = None
        if enable_refinement and v1_path:
            v1_data = nib.load(v1_path).get_fdata()

        # Create output directory for new ROIs
        # Include _refined suffix if refinement is enabled
        roi_suffix = f"{roi_shape.name}_refined" if enable_refinement else roi_shape.name
        roi_dir_name = f"rois_{roi_suffix}"
        roi_dir = subject_dir / roi_dir_name
        roi_dir.mkdir(parents=True, exist_ok=True)

        log(f"  Creating {roi_suffix} ROIs...")

        # Transform and create ROIs
        roi_mask_paths = {}
        template_centroids = {}

        # First pass: Transform all ROI templates
        for roi_name, roi_template in roi_templates.items():
            roi_transformed = reg_dir / f"{subject_id}_{roi_name}_transformed.nii.gz"

            # Transform if not already exists
            if not roi_transformed.exists():
                applywarp_cmd = [
                    str(fsl_bin / "applywarp"),
                    f"--ref={fa_path}",
                    f"--in={roi_template}",
                    f"--warp={inverse_warp}",
                    f"--out={roi_transformed}",
                    "--interp=nn",
                ]
                subprocess.run(applywarp_cmd, capture_output=True, check=True)

            # Find centroid
            transformed_data = nib.load(str(roi_transformed)).get_fdata()
            centroid = find_mask_centroid(transformed_data)
            if centroid is None:
                result.status = "failed"
                result.error_message = f"No voxels in transformed {roi_name}"
                return result

            template_centroids[roi_name] = centroid

        # Second pass: Process projection ROIs first (for refinement constraint)
        roi_centroids = {}
        for roi_name in ["left_proj", "right_proj"]:
            centroid = template_centroids[roi_name]

            if enable_refinement and v1_data is not None:
                # Calculate sphere for purity calculation
                if roi_shape.shape_type == "sphere":
                    orig_sphere = create_sphere_mask(
                        ref_shape, centroid, roi_shape.sphere_radius, voxel_size
                    )
                else:
                    orig_sphere = create_square_v9_mask(ref_shape, centroid)

                orig_purity, _, _, _ = calculate_roi_quality(v1_data, fa_data, orig_sphere, "proj")

                # Refine placement
                refined_centroid, refined_purity, _ = refine_roi_placement(
                    centroid,
                    v1_data,
                    fa_data,
                    ref_shape,
                    voxel_size,
                    "proj",
                    radius_mm=roi_shape.sphere_radius or 3.0,
                    search_x=3,
                    search_y=2,
                    search_z=1,
                )

                if refined_centroid != centroid:
                    log(
                        f"    {roi_name}: purity {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%"
                    )
                centroid = refined_centroid

            roi_centroids[roi_name] = centroid

            # Create ROI mask
            if roi_shape.shape_type == "sphere":
                mask = create_sphere_mask(ref_shape, centroid, roi_shape.sphere_radius, voxel_size)
            else:
                mask = create_square_v9_mask(ref_shape, centroid)

            # Save ROI
            roi_path = roi_dir / f"{subject_id}_{roi_name}.nii.gz"
            roi_img = nib.Nifti1Image(mask.astype(np.float32), fa_img.affine, fa_img.header)
            nib.save(roi_img, str(roi_path))
            roi_mask_paths[roi_name] = str(roi_path)

        # Third pass: Process association ROIs with Y-constraint
        for roi_name, proj_name in [("left_assoc", "left_proj"), ("right_assoc", "right_proj")]:
            centroid = template_centroids[roi_name]
            proj_centroid = roi_centroids[proj_name]

            if enable_refinement and v1_data is not None:
                if roi_shape.shape_type == "sphere":
                    orig_sphere = create_sphere_mask(
                        ref_shape, centroid, roi_shape.sphere_radius, voxel_size
                    )
                else:
                    orig_sphere = create_square_v9_mask(ref_shape, centroid)

                orig_purity, _, _, _ = calculate_roi_quality(v1_data, fa_data, orig_sphere, "assoc")

                # Refine with Y-constraint
                refined_centroid, refined_purity, _ = refine_roi_placement(
                    centroid,
                    v1_data,
                    fa_data,
                    ref_shape,
                    voxel_size,
                    "assoc",
                    radius_mm=roi_shape.sphere_radius or 3.0,
                    search_x=3,
                    search_y=2,
                    search_z=1,
                    reference_centroid=proj_centroid,
                    max_y_drift=1,
                )

                if refined_centroid != centroid:
                    log(
                        f"    {roi_name}: purity {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%"
                    )
                centroid = refined_centroid

            roi_centroids[roi_name] = centroid

            # Create ROI mask
            if roi_shape.shape_type == "sphere":
                mask = create_sphere_mask(ref_shape, centroid, roi_shape.sphere_radius, voxel_size)
            else:
                mask = create_square_v9_mask(ref_shape, centroid)

            # Save ROI
            roi_path = roi_dir / f"{subject_id}_{roi_name}.nii.gz"
            roi_img = nib.Nifti1Image(mask.astype(np.float32), fa_img.affine, fa_img.header)
            nib.save(roi_img, str(roi_path))
            roi_mask_paths[roi_name] = str(roi_path)

        # Load ROI masks for ALPS calculation
        masks = {}
        for roi_name, roi_path in roi_mask_paths.items():
            masks[roi_name] = nib.load(roi_path).get_fdata()

        # Calculate ALPS
        log(f"  Calculating ALPS ({alps_method})...")

        if alps_method in ["ALPS-LAB", "Both"]:
            lab_results = calculate_alps_lab(
                tensor_path=str(tensor_path),
                fa_data=fa_data,
                masks=masks,
                fa_threshold=fa_threshold,
            )
            if lab_results:
                result.alps_lab_left = lab_results.get("ALPS_left")
                result.alps_lab_right = lab_results.get("ALPS_right")
                result.alps_lab_bilateral = lab_results.get("ALPS_bilateral")

        if alps_method in ["ALPS-PAS", "Both"]:
            if all([l2_path, l3_path, v2_path, v3_path]):
                pas_results = calculate_alps_pas(
                    l2_path=str(l2_path),
                    l3_path=str(l3_path),
                    v2_path=str(v2_path),
                    v3_path=str(v3_path),
                    fa_data=fa_data,
                    masks=masks,
                    fa_threshold=fa_threshold,
                )
                if pas_results:
                    result.alps_pas_left = pas_results.get("ALPS_left")
                    result.alps_pas_right = pas_results.get("ALPS_right")
                    result.alps_pas_bilateral = pas_results.get("ALPS_bilateral")
            else:
                log("    WARNING: Eigenvector files not found, skipping ALPS-PAS")

        result.status = "completed"
        log(
            f"  Done: ALPS-LAB={result.alps_lab_bilateral:.4f}" if result.alps_lab_bilateral else ""
        )

    except subprocess.CalledProcessError as e:
        result.status = "failed"
        result.error_message = f"FSL command failed: {e}"
    except Exception as e:
        result.status = "failed"
        result.error_message = str(e)

    return result


def run_reanalysis(
    output_dir: str,
    roi_shape: ROIShape,
    enable_refinement: bool = False,
    alps_method: str = "Both",
    fa_threshold: float = 0.2,
    log_callback: Callable[[str], None] | None = None,
) -> list[ReanalysisResult]:
    """
    Run reanalysis on all processed subjects in output directory.

    Parameters
    ----------
    output_dir : str
        Path to the batch output directory
    roi_shape : ROIShape
        ROI shape configuration
    enable_refinement : bool
        Whether to enable ROI refinement
    alps_method : str
        ALPS calculation method ("ALPS-LAB", "ALPS-PAS", or "Both")
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback for log messages

    Returns
    -------
    list of ReanalysisResult
        Results for each subject
    """
    log = log_callback or print

    log(f"Discovering processed subjects in {output_dir}...")
    subjects = discover_processed_subjects(output_dir)

    if not subjects:
        log("No processed subjects found.")
        return []

    log(f"Found {len(subjects)} processed subjects")
    log(f"ROI shape: {roi_shape.name}")
    log(f"Refinement: {'enabled' if enable_refinement else 'disabled'}")
    log(f"ALPS method: {alps_method}")
    log("")

    results = []
    for i, (subject_id, subject_dir) in enumerate(subjects):
        log(f"[{i + 1}/{len(subjects)}] Processing {subject_id}...")

        result = reanalyze_subject(
            subject_id=subject_id,
            subject_dir=subject_dir,
            roi_shape=roi_shape,
            enable_refinement=enable_refinement,
            alps_method=alps_method,
            fa_threshold=fa_threshold,
            log_callback=log,
        )
        results.append(result)

        if result.status == "completed":
            if result.alps_lab_bilateral is not None:
                log(f"    ALPS-LAB: {result.alps_lab_bilateral:.4f}")
            if result.alps_pas_bilateral is not None:
                log(f"    ALPS-PAS: {result.alps_pas_bilateral:.4f}")
        else:
            log(f"    FAILED: {result.error_message}")

    # Write CSV results
    # Include _refined suffix if refinement is enabled
    roi_suffix = f"{roi_shape.name}_refined" if enable_refinement else roi_shape.name
    csv_filename = f"alps_results_{roi_suffix}.csv"
    csv_path = os.path.join(output_dir, csv_filename)

    log(f"\nWriting results to {csv_path}...")
    _write_reanalysis_csv(csv_path, results, alps_method)

    # Summary
    completed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")
    log(f"\nReanalysis complete: {completed} succeeded, {failed} failed")

    return results


def _write_reanalysis_csv(
    csv_path: str,
    results: list[ReanalysisResult],
    alps_method: str,
) -> None:
    """Write reanalysis results to CSV file."""
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)

        # Build header based on method
        if alps_method == "ALPS-LAB":
            header = [
                "Filename",
                "Left Hemisphere ALPS-LAB",
                "Right Hemisphere ALPS-LAB",
                "Combined ALPS-LAB",
                "Status",
                "Error",
            ]
        elif alps_method == "ALPS-PAS":
            header = [
                "Filename",
                "Left Hemisphere ALPS-PAS",
                "Right Hemisphere ALPS-PAS",
                "Combined ALPS-PAS",
                "Status",
                "Error",
            ]
        else:  # Both
            header = [
                "Filename",
                "Left Hemisphere ALPS-LAB",
                "Right Hemisphere ALPS-LAB",
                "Combined ALPS-LAB",
                "Left Hemisphere ALPS-PAS",
                "Right Hemisphere ALPS-PAS",
                "Combined ALPS-PAS",
                "Status",
                "Error",
            ]

        writer.writerow(header)

        for result in results:
            if alps_method == "ALPS-LAB":
                row = [
                    result.subject_id,
                    f"{result.alps_lab_left:.6f}" if result.alps_lab_left is not None else "",
                    f"{result.alps_lab_right:.6f}" if result.alps_lab_right is not None else "",
                    f"{result.alps_lab_bilateral:.6f}"
                    if result.alps_lab_bilateral is not None
                    else "",
                    result.status,
                    result.error_message or "",
                ]
            elif alps_method == "ALPS-PAS":
                row = [
                    result.subject_id,
                    f"{result.alps_pas_left:.6f}" if result.alps_pas_left is not None else "",
                    f"{result.alps_pas_right:.6f}" if result.alps_pas_right is not None else "",
                    f"{result.alps_pas_bilateral:.6f}"
                    if result.alps_pas_bilateral is not None
                    else "",
                    result.status,
                    result.error_message or "",
                ]
            else:  # Both
                row = [
                    result.subject_id,
                    f"{result.alps_lab_left:.6f}" if result.alps_lab_left is not None else "",
                    f"{result.alps_lab_right:.6f}" if result.alps_lab_right is not None else "",
                    f"{result.alps_lab_bilateral:.6f}"
                    if result.alps_lab_bilateral is not None
                    else "",
                    f"{result.alps_pas_left:.6f}" if result.alps_pas_left is not None else "",
                    f"{result.alps_pas_right:.6f}" if result.alps_pas_right is not None else "",
                    f"{result.alps_pas_bilateral:.6f}"
                    if result.alps_pas_bilateral is not None
                    else "",
                    result.status,
                    result.error_message or "",
                ]

            writer.writerow(row)
