"""
ROI reanalysis module for DTI-ALPS pipeline.

This module allows re-running ROI placement and ALPS calculation on
already-processed data with different ROI shapes, without repeating
the preprocessing and registration steps.

Usage:
    python -m dti_alps --reanalyze /path/to/output --sphere 3.0
    python -m dti_alps --reanalyze /path/to/output --squarev9
    python -m dti_alps --reanalyze /path/to/output --sphere 2.5 --adaptive
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib

from . import results_layout
from .alps_calculation import (
    calculate_alps_lab,
    calculate_alps_pas,
    load_lab_components,
    load_pas_components,
    load_roi_masks,
)
from .constants import FA_THRESHOLD
from .native_placement import place_rois_in_native
from .registration.results import get_roi_template_paths
from .tool_runner import SubprocessToolRunner, ToolRunner


@dataclass
class ROIShape:
    """ROI shape configuration."""

    shape_type: str  # "sphere", "squarev9", or "squarev4"
    sphere_radius: float | None = None  # Only for sphere type

    @property
    def name(self) -> str:
        """The base on-disk token for folder/file naming.

        Delegates to ``results_layout.shape_token`` (the single geometry->token
        home), so the default 3.0 mm sphere collapses to ``rois`` exactly as the
        pipeline's writer does — the two cannot drift.
        """
        return results_layout.shape_token(self.shape_type, self.sphere_radius)


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
    enable_adaptive: bool,
    alps_method: str,
    fa_threshold: float,
    log_callback: Callable[[str], None] | None = None,
    runner: ToolRunner | None = None,
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
    enable_adaptive : bool
        Whether to enable adaptive ROI placement
    alps_method : str
        ALPS calculation method ("ALPS-LAB", "ALPS-PAS", or "Both")
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback for log messages
    runner : ToolRunner | None
        Seam for external command execution. Defaults to a real
        subprocess-backed runner; tests inject a fake.

    Returns
    -------
    ReanalysisResult
        Result of the reanalysis
    """
    log = log_callback or (lambda x: None)
    runner = runner or SubprocessToolRunner()
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

        # Find eigenvector files (for ALPS-PAS and adaptive placement)
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

        # Load FA data for ALPS calculation
        fa_data = nib.load(fa_path).get_fdata()

        # Create output directory for new ROIs
        # Include _adaptive suffix if adaptive placement is enabled
        roi_suffix = f"{roi_shape.name}_adaptive" if enable_adaptive else roi_shape.name
        roi_dir = subject_dir / results_layout.roi_dir_name(roi_suffix)
        roi_dir.mkdir(parents=True, exist_ok=True)

        log(f"  Creating {roi_suffix} ROIs...")

        # Transform templates and build the masks through the shared shell
        # (same body the pipeline uses; a raised ROIPlacementError is caught by
        # the surrounding try/except and reported as a failed result).
        roi_mask_paths, _ = place_rois_in_native(
            runner=runner,
            applywarp_cmd=str(fsl_bin / "applywarp"),
            fa_path=str(fa_path),
            inverse_warp=inverse_warp,
            roi_templates=roi_templates,
            reg_dir=reg_dir,
            roi_dir=roi_dir,
            prefix=subject_id,
            shape_type=roi_shape.shape_type,
            sphere_radius=roi_shape.sphere_radius,
            adaptive=enable_adaptive,
            v1_path=str(v1_path) if v1_path else None,
            l2_path=str(l2_path) if l2_path else None,
            l3_path=str(l3_path) if l3_path else None,
            log=log,
        )

        # Load ROI masks for ALPS calculation (shared loader -- same IO edge
        # the pipeline uses, so the two paths cannot drift apart)
        masks = load_roi_masks(roi_mask_paths)

        # Calculate ALPS
        log(f"  Calculating ALPS ({alps_method})...")

        if alps_method in ["ALPS-LAB", "Both"]:
            dxx, dyy, dzz = load_lab_components(str(tensor_path))
            lab_results = calculate_alps_lab(
                dxx=dxx,
                dyy=dyy,
                dzz=dzz,
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
                l2, l3, v2_x, v3_x = load_pas_components(
                    str(l2_path), str(l3_path), str(v2_path), str(v3_path)
                )
                pas_results = calculate_alps_pas(
                    l2=l2,
                    l3=l3,
                    v2_x=v2_x,
                    v3_x=v3_x,
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

    except Exception as e:
        result.status = "failed"
        result.error_message = str(e)

    return result


def run_reanalysis(
    output_dir: str,
    roi_shape: ROIShape,
    enable_adaptive: bool = False,
    alps_method: str = "Both",
    fa_threshold: float = FA_THRESHOLD,
    log_callback: Callable[[str], None] | None = None,
    runner: ToolRunner | None = None,
) -> list[ReanalysisResult]:
    """
    Run reanalysis on all processed subjects in output directory.

    Parameters
    ----------
    output_dir : str
        Path to the batch output directory
    roi_shape : ROIShape
        ROI shape configuration
    enable_adaptive : bool
        Whether to enable adaptive ROI placement
    alps_method : str
        ALPS calculation method ("ALPS-LAB", "ALPS-PAS", or "Both")
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback for log messages
    runner : ToolRunner | None
        Seam for external command execution, created once here and threaded into
        each subject. Defaults to a real subprocess-backed runner; tests inject
        a fake.

    Returns
    -------
    list of ReanalysisResult
        Results for each subject
    """
    log = log_callback or print
    runner = runner or SubprocessToolRunner()

    log(f"Discovering processed subjects in {output_dir}...")
    subjects = discover_processed_subjects(output_dir)

    if not subjects:
        log("No processed subjects found.")
        return []

    log(f"Found {len(subjects)} processed subjects")
    log(f"ROI shape: {roi_shape.name}")
    log(f"Adaptive placement: {'enabled' if enable_adaptive else 'disabled'}")
    log(f"ALPS method: {alps_method}")
    log("")

    results = []
    for i, (subject_id, subject_dir) in enumerate(subjects):
        log(f"[{i + 1}/{len(subjects)}] Processing {subject_id}...")

        result = reanalyze_subject(
            subject_id=subject_id,
            subject_dir=subject_dir,
            roi_shape=roi_shape,
            enable_adaptive=enable_adaptive,
            alps_method=alps_method,
            fa_threshold=fa_threshold,
            log_callback=log,
            runner=runner,
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
    # Include _adaptive suffix if adaptive placement is enabled
    roi_suffix = f"{roi_shape.name}_adaptive" if enable_adaptive else roi_shape.name
    csv_filename = results_layout.alps_csv_name(roi_suffix)
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
    """Write reanalysis results through the results-on-disk contract.

    Each ``ReanalysisResult`` maps onto an ``AlpsRow`` (its ``bilateral`` field
    becomes the table's ``combined``, ``error_message`` becomes ``error``); the
    column schema and ``.6f`` formatting live in ``results_layout.write_alps_csv``.
    """
    table = results_layout.AlpsTable(
        method=alps_method,
        rows={
            r.subject_id: results_layout.AlpsRow(
                subject_id=r.subject_id,
                status=r.status,
                error=r.error_message or "",
                lab_left=r.alps_lab_left,
                lab_right=r.alps_lab_right,
                lab_combined=r.alps_lab_bilateral,
                pas_left=r.alps_pas_left,
                pas_right=r.alps_pas_right,
                pas_combined=r.alps_pas_bilateral,
            )
            for r in results
        },
    )
    results_layout.write_alps_csv(csv_path, table)
