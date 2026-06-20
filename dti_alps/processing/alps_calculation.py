"""
DTI-ALPS index calculation functions.

This module contains the core ALPS calculation logic for both
ALPS-LAB (tensor diagonal components) and ALPS-PAS (eigenvector-sorted eigenvalues).

The science is pure: ``calculate_alps_lab`` and ``calculate_alps_pas`` take
pre-loaded NumPy arrays and return a results dict, so they can be exercised with
tiny synthetic arrays and no ``.nii.gz`` files on disk. All ``nib.load`` and all
knowledge of on-disk *format* (the MRtrix tensor-component index convention, the
eigenvector X-component slice) live in the loader functions below, which are the
single IO edge that both real callers -- the pipeline (via
``run_alps_calculation``) and reanalysis -- route through.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import nibabel as nib
import numpy as np

from .constants import TENSOR_DXX_INDEX, TENSOR_DYY_INDEX, TENSOR_DZZ_INDEX

if TYPE_CHECKING:
    from .state import PipelineState


# ---------------------------------------------------------------------------
# Loaders -- the single IO edge.
#
# These own ``nib.load`` and all on-disk *format* knowledge (the MRtrix tensor
# component index convention, the eigenvector X-component slice). They are thin
# IO shells and are intentionally not unit-tested; the science they feed is
# tested through the pure functions below. Both callers (the pipeline and
# reanalysis) load through these so the format knowledge cannot drift apart.
# ---------------------------------------------------------------------------


def load_fa_map(fa_path: str) -> np.ndarray:
    """Load the FA map used for CSF thresholding."""
    return nib.load(fa_path).get_fdata()


def load_roi_masks(
    roi_mask_paths: dict[str, str],
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, np.ndarray]:
    """Load registered ROI masks keyed by ROI name (e.g. ``left_proj``)."""
    log = log_callback or (lambda msg: None)
    masks = {}
    for roi_name, roi_path in roi_mask_paths.items():
        log(f"  Loading {roi_name}: {roi_path}")
        masks[roi_name] = nib.load(roi_path).get_fdata()
    return masks


def load_lab_components(
    tensor_path: str,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Load the diffusion tensor and slice out the (Dxx, Dyy, Dzz) diagonal
    components for the ALPS-LAB method.

    The MRtrix ``dwi2tensor`` output format is D11, D22, D33, D12, D13, D23; the
    diagonal-component indices are the *format* fact owned here, not by the math.
    """
    log = log_callback or (lambda msg: None)
    log(f"Loading tensor: {tensor_path}")
    tensor_data = nib.load(tensor_path).get_fdata()
    dxx = tensor_data[:, :, :, TENSOR_DXX_INDEX]
    dyy = tensor_data[:, :, :, TENSOR_DYY_INDEX]
    dzz = tensor_data[:, :, :, TENSOR_DZZ_INDEX]
    return dxx, dyy, dzz


def load_pas_components(
    l2_path: str,
    l3_path: str,
    v2_path: str,
    v3_path: str,
    log_callback: Callable[[str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Load eigenvalues L2/L3 and the X-components of eigenvectors V2/V3 for the
    ALPS-PAS method.

    The X-component slice (``v[:, :, :, 0]``) is *format* and owned here; the
    eigenvector-sort that uses these arrays is *science* and stays inside
    ``calculate_alps_pas``.
    """
    log = log_callback or (lambda msg: None)
    log(f"Loading L2: {l2_path}")
    l2_data = nib.load(l2_path).get_fdata()

    log(f"Loading L3: {l3_path}")
    l3_data = nib.load(l3_path).get_fdata()

    log(f"Loading V2: {v2_path}")
    v2_x = nib.load(v2_path).get_fdata()[:, :, :, 0]

    log(f"Loading V3: {v3_path}")
    v3_x = nib.load(v3_path).get_fdata()[:, :, :, 0]

    return l2_data, l3_data, v2_x, v3_x


# ---------------------------------------------------------------------------
# Pure ALPS calculations -- arrays in, results dict out. No IO, no format
# knowledge. These are the tested seam.
# ---------------------------------------------------------------------------


def calculate_alps_lab(
    dxx: np.ndarray,
    dyy: np.ndarray,
    dzz: np.ndarray,
    fa_data: np.ndarray,
    masks: dict[str, np.ndarray],
    fa_threshold: float,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, float] | None:
    """
    Calculate ALPS index using ALPS-LAB method (tensor diagonal components).

    The ALPS-LAB method uses the tensor diagonal components (Dxx, Dyy, Dzz)
    to calculate diffusivity in the perivascular (X) and perpendicular directions.

    Parameters
    ----------
    dxx, dyy, dzz : ndarray
        Same-shape 3-D tensor diagonal component maps (Dxx, Dyy, Dzz)
    fa_data : ndarray
        FA map data for thresholding
    masks : dict
        Dictionary of ROI masks with keys: 'left_proj', 'left_assoc',
        'right_proj', 'right_assoc'
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback function for logging messages

    Returns
    -------
    dict or None
        Results dictionary containing diffusivity values and ALPS indices,
        or None if calculation failed
    """
    log = log_callback or (lambda msg: None)

    # Calculate mean diffusivities in each ROI
    results = {}

    for side in ["left", "right"]:
        proj_mask = masks[f"{side}_proj"]
        assoc_mask = masks[f"{side}_assoc"]

        # Get voxel indices with FA threshold applied
        proj_idx_raw = np.where(proj_mask > 0)
        assoc_idx_raw = np.where(assoc_mask > 0)

        # Apply FA > threshold filter to exclude CSF voxels
        proj_fa_mask = fa_data[proj_idx_raw] > fa_threshold
        assoc_fa_mask = fa_data[assoc_idx_raw] > fa_threshold

        proj_idx = tuple(arr[proj_fa_mask] for arr in proj_idx_raw)
        assoc_idx = tuple(arr[assoc_fa_mask] for arr in assoc_idx_raw)

        # Log ROI sizes before and after FA filtering
        proj_voxels_raw = len(proj_idx_raw[0])
        assoc_voxels_raw = len(assoc_idx_raw[0])
        proj_voxels = len(proj_idx[0])
        assoc_voxels = len(assoc_idx[0])
        log(
            f"  {side.capitalize()} projection ROI: "
            f"{proj_voxels}/{proj_voxels_raw} voxels (after FA filter)"
        )
        log(
            f"  {side.capitalize()} association ROI: "
            f"{assoc_voxels}/{assoc_voxels_raw} voxels (after FA filter)"
        )

        # Warn if too many voxels were filtered out
        if proj_voxels == 0:
            log(f"  WARNING: No voxels in {side} projection ROI after FA filtering!")
        if assoc_voxels == 0:
            log(f"  WARNING: No voxels in {side} association ROI after FA filtering!")

        # Projection ROI: Dxx (perivascular) and Dyy (perpendicular)
        results[f"Dxx_proj_{side}"] = np.mean(dxx[proj_idx])
        results[f"Dyy_proj_{side}"] = np.mean(dyy[proj_idx])

        # Association ROI: Dxx (perivascular) and Dzz (perpendicular)
        results[f"Dxx_assoc_{side}"] = np.mean(dxx[assoc_idx])
        results[f"Dzz_assoc_{side}"] = np.mean(dzz[assoc_idx])

    # Calculate ALPS index for each hemisphere
    for side in ["left", "right"]:
        dxx_proj = results[f"Dxx_proj_{side}"]
        dxx_assoc = results[f"Dxx_assoc_{side}"]
        dyy_proj = results[f"Dyy_proj_{side}"]
        dzz_assoc = results[f"Dzz_assoc_{side}"]

        numerator = (dxx_proj + dxx_assoc) / 2
        denominator = (dyy_proj + dzz_assoc) / 2

        if denominator > 0:
            alps_index = numerator / denominator
        else:
            alps_index = float("nan")

        results[f"ALPS_{side}"] = alps_index
        log(f"  {side.capitalize()} ALPS index: {alps_index:.4f}")

    # Calculate bilateral average
    alps_left = results["ALPS_left"]
    alps_right = results["ALPS_right"]
    if not (np.isnan(alps_left) or np.isnan(alps_right)):
        results["ALPS_bilateral"] = (alps_left + alps_right) / 2
        log(f"  Bilateral ALPS index: {results['ALPS_bilateral']:.4f}")

    return results


def calculate_alps_pas(
    l2: np.ndarray,
    l3: np.ndarray,
    v2_x: np.ndarray,
    v3_x: np.ndarray,
    fa_data: np.ndarray,
    masks: dict[str, np.ndarray],
    fa_threshold: float,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, float] | None:
    """
    Calculate ALPS index using ALPS-PAS method (eigenvector-sorted eigenvalues).

    The ALPS-PAS method uses eigenvalues L2 and L3, sorted based on which
    eigenvector has greater X-component alignment. This accounts for fiber
    orientation more accurately than using fixed tensor diagonal components.

    Parameters
    ----------
    l2, l3 : ndarray
        Same-shape 3-D second/third eigenvalue maps (L2, L3)
    v2_x, v3_x : ndarray
        Same-shape 3-D X-components of the V2/V3 eigenvectors
    fa_data : ndarray
        FA map data for thresholding
    masks : dict
        Dictionary of ROI masks with keys: 'left_proj', 'left_assoc',
        'right_proj', 'right_assoc'
    fa_threshold : float
        FA threshold for filtering CSF voxels
    log_callback : callable, optional
        Callback function for logging messages

    Returns
    -------
    dict or None
        Results dictionary containing diffusivity values and ALPS indices,
        or None if calculation failed
    """
    log = log_callback or (lambda msg: None)

    # Sort eigenvalues by X-alignment: the eigenvalue whose eigenvector
    # has greater |X-component| is assigned to diff_X (perivascular direction)
    mask_v2_more_x = np.abs(v2_x) > np.abs(v3_x)
    diff_X = np.where(mask_v2_more_x, l2, l3)
    diff_perp = np.where(mask_v2_more_x, l3, l2)

    # Calculate mean diffusivities in each ROI
    results = {}

    for side in ["left", "right"]:
        proj_mask = masks[f"{side}_proj"]
        assoc_mask = masks[f"{side}_assoc"]

        # Get voxel indices with FA threshold applied
        proj_idx_raw = np.where(proj_mask > 0)
        assoc_idx_raw = np.where(assoc_mask > 0)

        # Apply FA > threshold filter to exclude CSF voxels
        proj_fa_mask = fa_data[proj_idx_raw] > fa_threshold
        assoc_fa_mask = fa_data[assoc_idx_raw] > fa_threshold

        proj_idx = tuple(arr[proj_fa_mask] for arr in proj_idx_raw)
        assoc_idx = tuple(arr[assoc_fa_mask] for arr in assoc_idx_raw)

        # Log ROI sizes before and after FA filtering
        proj_voxels_raw = len(proj_idx_raw[0])
        assoc_voxels_raw = len(assoc_idx_raw[0])
        proj_voxels = len(proj_idx[0])
        assoc_voxels = len(assoc_idx[0])
        log(
            f"  {side.capitalize()} projection ROI: "
            f"{proj_voxels}/{proj_voxels_raw} voxels (after FA filter)"
        )
        log(
            f"  {side.capitalize()} association ROI: "
            f"{assoc_voxels}/{assoc_voxels_raw} voxels (after FA filter)"
        )

        # Warn if too many voxels were filtered out
        if proj_voxels == 0:
            log(f"  WARNING: No voxels in {side} projection ROI after FA filtering!")
        if assoc_voxels == 0:
            log(f"  WARNING: No voxels in {side} association ROI after FA filtering!")

        # Projection ROI: diff_X (X-aligned) and diff_perp (perpendicular)
        results[f"Dxx_proj_{side}"] = np.mean(diff_X[proj_idx])
        results[f"Dyy_proj_{side}"] = np.mean(diff_perp[proj_idx])

        # Association ROI: diff_X (X-aligned) and diff_perp (perpendicular)
        results[f"Dxx_assoc_{side}"] = np.mean(diff_X[assoc_idx])
        results[f"Dzz_assoc_{side}"] = np.mean(diff_perp[assoc_idx])

    # Calculate ALPS index for each hemisphere
    # ALPS = mean(diff_X_proj, diff_X_assoc) / mean(diff_perp_proj, diff_perp_assoc)
    for side in ["left", "right"]:
        diff_x_proj = results[f"Dxx_proj_{side}"]
        diff_x_assoc = results[f"Dxx_assoc_{side}"]
        diff_perp_proj = results[f"Dyy_proj_{side}"]
        diff_perp_assoc = results[f"Dzz_assoc_{side}"]

        numerator = (diff_x_proj + diff_x_assoc) / 2
        denominator = (diff_perp_proj + diff_perp_assoc) / 2

        if denominator > 0:
            alps_index = numerator / denominator
        else:
            alps_index = float("nan")

        results[f"ALPS_{side}"] = alps_index
        log(f"  {side.capitalize()} ALPS index: {alps_index:.4f}")

    # Calculate bilateral average
    alps_left = results["ALPS_left"]
    alps_right = results["ALPS_right"]
    if not (np.isnan(alps_left) or np.isnan(alps_right)):
        results["ALPS_bilateral"] = (alps_left + alps_right) / 2
        log(f"  Bilateral ALPS index: {results['ALPS_bilateral']:.4f}")

    return results


def run_alps_calculation(
    state: "PipelineState",
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """
    Calculate DTI-ALPS index from tensor and registered ROI masks.

    Supports three options:
    - ALPS-LAB: Uses tensor diagonal components (Dxx, Dyy, Dzz)
    - ALPS-PAS: Uses eigenvalues (L2, L3) sorted by eigenvector X-alignment
    - Both: Calculates both ALPS-LAB and ALPS-PAS

    This is the IO/orchestration shell for the pipeline path: it loads through
    the shared loaders and hands arrays to the pure ``calculate_*`` functions.

    Parameters
    ----------
    state : PipelineState
        Pipeline state containing paths and configuration
    log_callback : callable, optional
        Callback function for logging messages

    Returns
    -------
    dict or None
        Results dictionary with method and calculated values, or None if failed
    """
    log = log_callback or (lambda msg: None)

    log(f"Calculating DTI-ALPS index using {state.alps_method} method...")

    # Verify ROI masks are available
    if not state.roi_mask_paths:
        log("ERROR: ROI masks not available. Run registration first.")
        return None

    # Load FA map for thresholding (to filter out CSF voxels)
    log(f"Loading FA map: {state.fa_path}")
    fa_data = load_fa_map(state.fa_path)
    log(f"  Applying FA threshold > {state.fa_threshold} to filter CSF voxels")

    # Load registered ROI masks
    log("Loading registered ROI masks...")
    masks = load_roi_masks(state.roi_mask_paths, log_callback=log)

    # Calculate based on method selection
    results: dict[str, Any] = {"method": state.alps_method}

    if state.alps_method == "ALPS-LAB":
        log("Calculating ALPS-LAB...")
        dxx, dyy, dzz = load_lab_components(state.tensor_path, log_callback=log)
        lab_results = calculate_alps_lab(
            dxx=dxx,
            dyy=dyy,
            dzz=dzz,
            fa_data=fa_data,
            masks=masks,
            fa_threshold=state.fa_threshold,
            log_callback=log,
        )
        if lab_results is None:
            return None
        # Store with LAB prefix
        for key, value in lab_results.items():
            results[f"LAB_{key}"] = value

    elif state.alps_method == "ALPS-PAS":
        log("Calculating ALPS-PAS...")
        l2, l3, v2_x, v3_x = load_pas_components(
            state.l2_path, state.l3_path, state.v2_path, state.v3_path, log_callback=log
        )
        pas_results = calculate_alps_pas(
            l2=l2,
            l3=l3,
            v2_x=v2_x,
            v3_x=v3_x,
            fa_data=fa_data,
            masks=masks,
            fa_threshold=state.fa_threshold,
            log_callback=log,
        )
        if pas_results is None:
            return None
        # Store with PAS prefix
        for key, value in pas_results.items():
            results[f"PAS_{key}"] = value

    elif state.alps_method == "Both":
        # Calculate both methods
        log("Calculating ALPS-LAB...")
        dxx, dyy, dzz = load_lab_components(state.tensor_path, log_callback=log)
        lab_results = calculate_alps_lab(
            dxx=dxx,
            dyy=dyy,
            dzz=dzz,
            fa_data=fa_data,
            masks=masks,
            fa_threshold=state.fa_threshold,
            log_callback=log,
        )
        if lab_results is None:
            return None
        for key, value in lab_results.items():
            results[f"LAB_{key}"] = value

        log("Calculating ALPS-PAS...")
        l2, l3, v2_x, v3_x = load_pas_components(
            state.l2_path, state.l3_path, state.v2_path, state.v3_path, log_callback=log
        )
        pas_results = calculate_alps_pas(
            l2=l2,
            l3=l3,
            v2_x=v2_x,
            v3_x=v3_x,
            fa_data=fa_data,
            masks=masks,
            fa_threshold=state.fa_threshold,
            log_callback=log,
        )
        if pas_results is None:
            return None
        for key, value in pas_results.items():
            results[f"PAS_{key}"] = value

    log("ALPS calculation completed successfully")
    return results
