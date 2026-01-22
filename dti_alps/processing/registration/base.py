"""
Abstract base class and common utilities for registration backends.

This module defines the RegistrationBackend interface that all registration
implementations must follow, plus tool-agnostic helper functions for ROI
processing that are shared across backends.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..state import PipelineState


@dataclass
class RegistrationResult:
    """
    Result of a registration operation (registration step only).

    Attributes
    ----------
    success : bool
        Whether registration completed successfully
    inverse_warp_path : str
        Path to inverse warp field for transforming templates to native space
    error_message : str, optional
        Error description if registration failed
    """

    success: bool
    inverse_warp_path: str | None = None
    error_message: str | None = None


@dataclass
class ROIPlacementResult:
    """
    Result of ROI placement operation.

    Attributes
    ----------
    success : bool
        Whether ROI placement completed successfully
    roi_mask_paths : dict[str, str]
        Paths to final ROI masks in native space (for first/primary shape)
    roi_centers : dict[str, tuple[int, int, int]]
        Centroid coordinates for each ROI (for first/primary shape)
    error_message : str, optional
        Error description if placement failed
    all_roi_results : dict[str, dict]
        All ROI results indexed by shape name (e.g., "sphere3_refined").
        Each entry contains: {"roi_mask_paths": {...}, "roi_centers": {...}}
    """

    success: bool
    roi_mask_paths: dict[str, str]
    roi_centers: dict[str, tuple[int, int, int]]
    error_message: str | None = None
    all_roi_results: dict[str, dict] | None = None


class RegistrationBackend(ABC):
    """
    Abstract base class for registration backends.

    Implementations provide tool-specific registration (FSL, ANTs, etc.)
    while sharing common ROI processing utilities.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the backend name (e.g., 'fsl', 'ants')."""
        pass

    @abstractmethod
    def check_available(self) -> tuple[bool, list[str]]:
        """
        Check if required tools are installed.

        Returns
        -------
        tuple of (bool, list[str])
            (all_available, list of missing tools/commands)
        """
        pass

    @abstractmethod
    def get_template_path(self) -> Path | None:
        """
        Get path to the FA template for registration.

        Returns
        -------
        Path or None
            Path to template file, or None if not found
        """
        pass

    @abstractmethod
    def register(
        self,
        state: "PipelineState",
        log_callback: Callable[[str], None] | None = None,
    ) -> RegistrationResult:
        """
        Register subject FA to template and create inverse warp.

        This step performs:
        1. Skull stripping (if needed)
        2. Linear registration to template
        3. Non-linear registration refinement
        4. Inverse warp creation for template-to-native transformation

        Parameters
        ----------
        state : PipelineState
            Pipeline state with FA path and registration options
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        RegistrationResult
            Result containing inverse warp path
        """
        pass

    @abstractmethod
    def place_rois(
        self,
        state: "PipelineState",
        log_callback: Callable[[str], None] | None = None,
    ) -> "ROIPlacementResult":
        """
        Transform ROI templates to native space and create spherical ROIs.

        This step performs:
        1. Apply inverse warp to ROI templates
        2. Find centroid of each transformed mask
        3. Optionally refine placement using fiber orientation
        4. Create spherical ROIs at final centroids

        Requires that register() has been run first (inverse_warp_path must exist).

        Parameters
        ----------
        state : PipelineState
            Pipeline state with inverse_warp_path and ROI parameters
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        ROIPlacementResult
            Result containing ROI paths and centroids
        """
        pass


# =============================================================================
# Tool-agnostic helper functions (shared by all backends)
# =============================================================================


def get_roi_template_paths() -> dict[str, Path] | None:
    """
    Get paths to pre-defined ROI templates shipped with the package.

    Returns
    -------
    dict or None
        Dictionary mapping ROI names to template paths, or None if any missing.
        Keys: 'left_proj', 'left_assoc', 'right_proj', 'right_assoc'
    """
    # Look relative to this module (processing/registration -> dti_alps -> templates)
    module_dir = Path(__file__).parent.parent.parent.parent
    templates_dir = module_dir / "templates"

    roi_templates = {
        "left_proj": templates_dir / "JHU-labels-left_proj.nii.gz",
        "left_assoc": templates_dir / "JHU-labels-left_assoc.nii.gz",
        "right_proj": templates_dir / "JHU-labels-right_proj.nii.gz",
        "right_assoc": templates_dir / "JHU-labels-right_assoc.nii.gz",
    }

    # Check all templates exist
    for path in roi_templates.values():
        if not path.exists():
            return None

    return roi_templates


def create_sphere_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[float, float, float],
    radius_mm: float,
    voxel_size: tuple[float, float, float],
) -> np.ndarray:
    """
    Create a spherical binary mask centered at given voxel coordinates.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of float
        Center of sphere in voxel coordinates
    radius_mm : float
        Radius of sphere in millimeters
    voxel_size : tuple of float
        Voxel dimensions in millimeters (x, y, z)

    Returns
    -------
    np.ndarray
        Binary mask with sphere
    """
    x, y, z = np.ogrid[: shape[0], : shape[1], : shape[2]]

    # Calculate squared distance from center in mm
    dist_sq = (
        ((x - center_voxel[0]) * voxel_size[0]) ** 2
        + ((y - center_voxel[1]) * voxel_size[1]) ** 2
        + ((z - center_voxel[2]) * voxel_size[2]) ** 2
    )

    return dist_sq <= radius_mm**2


def create_square_v9_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[int, int, int],
) -> np.ndarray:
    """
    Create a 3x3 square binary mask (9 voxels) in the axial plane.

    The mask is a 3x3 block centered at the given voxel coordinates,
    all in the same axial (Z) slice. This provides a simple, reproducible
    ROI shape that is less sensitive to voxel size variations.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of int
        Center of square in voxel coordinates (x, y, z)

    Returns
    -------
    np.ndarray
        Binary mask with 3x3 square (9 voxels)
    """
    mask = np.zeros(shape, dtype=bool)

    cx, cy, cz = center_voxel

    # Create 3x3 block in axial plane (±1 in X and Y, same Z)
    for dx in range(-1, 2):
        for dy in range(-1, 2):
            x, y, z = cx + dx, cy + dy, cz
            # Ensure within bounds
            if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
                mask[x, y, z] = True

    return mask


def create_square_v4_mask(
    shape: tuple[int, int, int],
    center_voxel: tuple[int, int, int],
    v1_data: np.ndarray | None = None,
    fiber_type: str = "proj",
) -> np.ndarray:
    """
    Create a 2x2 square binary mask (4 voxels) in the axial plane.

    The centroid is placed at one corner of the 2x2 square. There are 4 possible
    configurations for the 2x2 square with the centroid as a corner. The optimal
    configuration is selected by maximizing the average V1(z) for projection ROIs
    or V1(y) for association ROIs, helping the square encapsulate the track region
    and avoid boundaries where crossing fibers might exist.

    If v1_data is not provided, defaults to centroid at bottom-left corner.

    Parameters
    ----------
    shape : tuple of int
        Shape of the output array (x, y, z)
    center_voxel : tuple of int
        Corner voxel coordinates (x, y, z) - one corner of the 2x2 square
    v1_data : np.ndarray, optional
        Primary eigenvector data (x, y, z, 3) for optimal configuration selection
    fiber_type : str
        Either 'proj' (maximize V1_z) or 'assoc' (maximize V1_y)

    Returns
    -------
    np.ndarray
        Binary mask with 2x2 square (4 voxels)
    """
    cx, cy, cz = center_voxel

    # Define all 4 possible 2x2 configurations with centroid as a corner
    # Each configuration is a list of (dx, dy) offsets from centroid
    configurations = [
        # Centroid at bottom-left: (cx, cy), (cx+1, cy), (cx, cy+1), (cx+1, cy+1)
        [(0, 0), (1, 0), (0, 1), (1, 1)],
        # Centroid at bottom-right: (cx-1, cy), (cx, cy), (cx-1, cy+1), (cx, cy+1)
        [(-1, 0), (0, 0), (-1, 1), (0, 1)],
        # Centroid at top-left: (cx, cy-1), (cx+1, cy-1), (cx, cy), (cx+1, cy)
        [(0, -1), (1, -1), (0, 0), (1, 0)],
        # Centroid at top-right: (cx-1, cy-1), (cx, cy-1), (cx-1, cy), (cx, cy)
        [(-1, -1), (0, -1), (-1, 0), (0, 0)],
    ]

    def _get_voxels_for_config(offsets: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
        """Get valid voxel coordinates for a configuration."""
        voxels = []
        for dx, dy in offsets:
            x, y, z = cx + dx, cy + dy, cz
            if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
                voxels.append((x, y, z))
        return voxels

    def _calculate_v1_metric(voxels: list[tuple[int, int, int]]) -> float:
        """Calculate average V1 component for the given voxels."""
        if not voxels or v1_data is None:
            return 0.0

        component_idx = 2 if fiber_type == "proj" else 1  # Z for proj, Y for assoc
        total = 0.0
        for x, y, z in voxels:
            total += abs(v1_data[x, y, z, component_idx])
        return total / len(voxels)

    # Select optimal configuration
    if v1_data is not None:
        best_config = None
        best_metric = -1.0

        for config in configurations:
            voxels = _get_voxels_for_config(config)
            if len(voxels) < 4:
                continue  # Skip configs that go out of bounds
            metric = _calculate_v1_metric(voxels)
            if metric > best_metric:
                best_metric = metric
                best_config = config

        # Fallback to first config if none are valid
        if best_config is None:
            best_config = configurations[0]
    else:
        # Default to first configuration (centroid at bottom-left)
        best_config = configurations[0]

    # Create mask with selected configuration
    mask = np.zeros(shape, dtype=bool)
    for dx, dy in best_config:
        x, y, z = cx + dx, cy + dy, cz
        if 0 <= x < shape[0] and 0 <= y < shape[1] and 0 <= z < shape[2]:
            mask[x, y, z] = True

    return mask


def find_mask_centroid(mask_data: np.ndarray) -> tuple[int, int, int] | None:
    """
    Find the centroid of non-zero voxels in a mask, rounded to nearest integer.

    Parameters
    ----------
    mask_data : np.ndarray
        Binary mask array

    Returns
    -------
    tuple of int or None
        Centroid coordinates (x, y, z) rounded to nearest integer,
        or None if mask is empty
    """
    coords = np.where(mask_data > 0)
    if len(coords[0]) == 0:
        return None

    centroid = (
        int(round(coords[0].mean())),
        int(round(coords[1].mean())),
        int(round(coords[2].mean())),
    )
    return centroid


def calculate_roi_quality(
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    mask: np.ndarray,
    fiber_type: str,
) -> tuple[float, float, float, float]:
    """
    Calculate ROI quality based on fiber purity, direction strength, and FA.

    The quality score rewards ROIs that:
    1. Have high fiber purity (% of voxels with correct dominant direction)
    2. Have strong directional alignment (mean magnitude of target V1 component)
    3. Have high FA values (strong fiber signal)

    Parameters
    ----------
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    mask : np.ndarray
        Binary ROI mask
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)

    Returns
    -------
    tuple of (purity, direction_strength, mean_fa, combined_score)
        purity: fraction of voxels with correct fiber orientation
        direction_strength: mean magnitude of target V1 component
        mean_fa: mean FA value in ROI
        combined_score: purity * direction_strength * mean_fa
    """
    coords = np.where(mask)
    if len(coords[0]) == 0:
        return 0.0, 0.0, 0.0, 0.0

    n_correct = 0
    direction_strengths = []
    fa_values = []

    for i in range(len(coords[0])):
        x, y, z = coords[0][i], coords[1][i], coords[2][i]
        v1 = v1_data[x, y, z, :]
        fa = fa_data[x, y, z]
        abs_v1 = np.abs(v1)

        fa_values.append(fa)

        if fiber_type == "proj":
            # Projection fibers: Z-dominant (superior-inferior)
            is_correct = abs_v1[2] > abs_v1[1] and abs_v1[2] > abs_v1[0]
            direction_strength = abs_v1[2]
        else:
            # Association fibers: Y-dominant (anterior-posterior)
            is_correct = abs_v1[1] > abs_v1[2] and abs_v1[1] > abs_v1[0]
            direction_strength = abs_v1[1]

        if is_correct:
            n_correct += 1
        direction_strengths.append(direction_strength)

    purity = n_correct / len(coords[0])
    mean_direction_strength = np.mean(direction_strengths)
    mean_fa = np.mean(fa_values)

    # Combined score rewards ROIs with high purity, strong direction, and high FA
    combined_score = purity * mean_direction_strength * mean_fa

    return purity, mean_direction_strength, mean_fa, combined_score


def refine_roi_placement(
    original_centroid: tuple[int, int, int],
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    shape: tuple[int, int, int],
    voxel_size: tuple[float, float, float],
    fiber_type: str,
    radius_mm: float = 3.0,
    search_x: int = 3,
    search_y: int = 1,
    search_z: int = 2,
    reference_centroid: tuple[int, int, int] | None = None,
    max_y_drift: int = 1,
    max_z_drift: int = 1,
    shape_type: str = "sphere",
) -> tuple[tuple[int, int, int], float, float]:
    """
    Refine ROI placement by searching nearby positions for better fiber purity.

    Starting from the template-based centroid, search a small neighborhood
    to find the position that maximizes the combined quality score
    (purity * direction * FA).

    For DTI-ALPS, projection and association ROIs must remain spatially aligned
    to capture the same underlying X-direction diffusion. When a reference_centroid
    is provided (typically from the paired ROI), the Y and Z coordinate drift is
    constrained to ensure both ROIs sample the same diffusion pathway.

    Parameters
    ----------
    original_centroid : tuple of int
        Initial centroid from template registration (x, y, z)
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    shape : tuple of int
        Shape of the image volume
    voxel_size : tuple of float
        Voxel dimensions in mm
    fiber_type : str
        Either 'proj' (Z-dominant) or 'assoc' (Y-dominant)
    radius_mm : float
        Sphere radius in millimeters (used for sphere shape)
    search_x : int
        Search range in X direction (voxels), default 3
    search_y : int
        Search range in Y direction (voxels), default 1
    search_z : int
        Search range in Z direction (voxels), default 2
    reference_centroid : tuple of int, optional
        Centroid of the paired ROI (e.g., projection ROI when refining association).
        If provided, the Y and Z coordinate drift from this reference is constrained.
    max_y_drift : int
        Maximum allowed Y-coordinate difference from reference_centroid (voxels).
        Only used when reference_centroid is provided. Default 1.
    max_z_drift : int
        Maximum allowed Z-coordinate difference from reference_centroid (voxels).
        Only used when reference_centroid is provided. Default 1.
    shape_type : str
        ROI shape type: "sphere", "squarev9", or "squarev4". Default "sphere".

    Returns
    -------
    tuple of (best_center, best_purity, best_score)
        best_center: optimal centroid position
        best_purity: fiber purity at optimal position
        best_score: combined quality score at optimal position
    """
    best_center = original_centroid
    best_score = -1.0
    best_purity = 0.0

    for dx in range(-search_x, search_x + 1):
        for dy in range(-search_y, search_y + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    original_centroid[0] + dx,
                    original_centroid[1] + dy,
                    original_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # If reference centroid provided, constrain Y and Z drift
                if reference_centroid is not None:
                    y_drift = abs(test_center[1] - reference_centroid[1])
                    if y_drift > max_y_drift:
                        continue
                    z_drift = abs(test_center[2] - reference_centroid[2])
                    if z_drift > max_z_drift:
                        continue

                # Create mask at test position using appropriate shape
                if shape_type == "sphere":
                    mask = create_sphere_mask(shape, test_center, radius_mm, voxel_size)
                elif shape_type == "squarev4":
                    mask = create_square_v4_mask(shape, test_center, v1_data, fiber_type)
                else:  # squarev9
                    mask = create_square_v9_mask(shape, test_center)

                # Calculate quality metrics
                purity, _, _, score = calculate_roi_quality(v1_data, fa_data, mask, fiber_type)

                if score > best_score:
                    best_score = score
                    best_center = test_center
                    best_purity = purity

    return best_center, best_purity, best_score


def refine_roi_pair_placement(
    proj_centroid: tuple[int, int, int],
    assoc_centroid: tuple[int, int, int],
    v1_data: np.ndarray,
    fa_data: np.ndarray,
    shape: tuple[int, int, int],
    voxel_size: tuple[float, float, float],
    radius_mm: float = 3.0,
    search_x: int = 3,
    search_y: int = 1,
    search_z: int = 2,
    max_y_drift: int = 1,
    max_z_drift: int = 1,
    shape_type: str = "sphere",
) -> tuple[tuple[int, int, int], tuple[int, int, int], float, float, float]:
    """
    Jointly refine projection and association ROI placement as a pair.

    Instead of optimizing projection ROI first and then constraining association ROI
    to it, this function searches all valid (proj, assoc) pairs simultaneously and
    selects the pair that maximizes the combined quality score.

    This approach prevents suboptimal results where a locally-optimal projection ROI
    position severely limits the quality of the paired association ROI.

    Parameters
    ----------
    proj_centroid : tuple of int
        Initial projection ROI centroid from template registration (x, y, z)
    assoc_centroid : tuple of int
        Initial association ROI centroid from template registration (x, y, z)
    v1_data : np.ndarray
        Primary eigenvector data (x, y, z, 3)
    fa_data : np.ndarray
        Fractional anisotropy data (x, y, z)
    shape : tuple of int
        Shape of the image volume
    voxel_size : tuple of float
        Voxel dimensions in mm
    radius_mm : float
        Sphere radius in millimeters (used for sphere shape)
    search_x : int
        Search range in X direction (voxels), default 3
    search_y : int
        Search range in Y direction (voxels), default 1
    search_z : int
        Search range in Z direction (voxels), default 2
    max_y_drift : int
        Maximum allowed Y-coordinate difference between proj and assoc ROIs (voxels).
        Default 1.
    max_z_drift : int
        Maximum allowed Z-coordinate difference between proj and assoc ROIs (voxels).
        Default 1.
    shape_type : str
        ROI shape type: "sphere", "squarev9", or "squarev4". Default "sphere".

    Returns
    -------
    tuple of (best_proj_center, best_assoc_center, best_proj_purity, best_assoc_purity, best_combined_score)
        best_proj_center: optimal projection ROI centroid position
        best_assoc_center: optimal association ROI centroid position
        best_proj_purity: fiber purity at optimal projection position
        best_assoc_purity: fiber purity at optimal association position
        best_combined_score: combined quality score (geometric mean of individual scores)
    """
    best_proj_center = proj_centroid
    best_assoc_center = assoc_centroid
    best_combined_score = -1.0
    best_proj_purity = 0.0
    best_assoc_purity = 0.0

    # Precompute quality scores for all projection ROI candidate positions
    # This avoids redundant mask creation and quality calculation in the pair search
    proj_scores: dict[tuple[int, int, int], tuple[float, float]] = {}
    for dx in range(-search_x, search_x + 1):
        for dy in range(-search_y, search_y + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    proj_centroid[0] + dx,
                    proj_centroid[1] + dy,
                    proj_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # Create mask and calculate quality
                if shape_type == "sphere":
                    mask = create_sphere_mask(shape, test_center, radius_mm, voxel_size)
                elif shape_type == "squarev4":
                    mask = create_square_v4_mask(shape, test_center, v1_data, "proj")
                else:  # squarev9
                    mask = create_square_v9_mask(shape, test_center)

                purity, _, _, score = calculate_roi_quality(v1_data, fa_data, mask, "proj")
                if score > 0:
                    proj_scores[test_center] = (purity, score)

    # Precompute quality scores for all association ROI candidate positions
    assoc_scores: dict[tuple[int, int, int], tuple[float, float]] = {}
    for dx in range(-search_x, search_x + 1):
        for dy in range(-search_y, search_y + 1):
            for dz in range(-search_z, search_z + 1):
                test_center = (
                    assoc_centroid[0] + dx,
                    assoc_centroid[1] + dy,
                    assoc_centroid[2] + dz,
                )

                # Ensure center is within bounds
                if not (
                    0 <= test_center[0] < shape[0]
                    and 0 <= test_center[1] < shape[1]
                    and 0 <= test_center[2] < shape[2]
                ):
                    continue

                # Create mask and calculate quality
                if shape_type == "sphere":
                    mask = create_sphere_mask(shape, test_center, radius_mm, voxel_size)
                elif shape_type == "squarev4":
                    mask = create_square_v4_mask(shape, test_center, v1_data, "assoc")
                else:  # squarev9
                    mask = create_square_v9_mask(shape, test_center)

                purity, _, _, score = calculate_roi_quality(v1_data, fa_data, mask, "assoc")
                if score > 0:
                    assoc_scores[test_center] = (purity, score)

    # Search all valid (proj, assoc) pairs using precomputed scores
    for test_proj, (proj_purity, proj_score) in proj_scores.items():
        for test_assoc, (assoc_purity, assoc_score) in assoc_scores.items():
            # Check Y and Z drift constraint between proj and assoc
            y_drift = abs(test_assoc[1] - test_proj[1])
            z_drift = abs(test_assoc[2] - test_proj[2])
            if y_drift > max_y_drift or z_drift > max_z_drift:
                continue

            # Combined score: geometric mean of individual scores
            # This ensures both ROIs must have good quality
            combined_score = np.sqrt(proj_score * assoc_score)

            if combined_score > best_combined_score:
                best_combined_score = combined_score
                best_proj_center = test_proj
                best_assoc_center = test_assoc
                best_proj_purity = proj_purity
                best_assoc_purity = assoc_purity

    return (
        best_proj_center,
        best_assoc_center,
        best_proj_purity,
        best_assoc_purity,
        best_combined_score,
    )
