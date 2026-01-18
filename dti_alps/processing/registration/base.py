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
        Paths to final ROI masks in native space
    roi_centers : dict[str, tuple[int, int, int]]
        Centroid coordinates for each ROI
    error_message : str, optional
        Error description if placement failed
    """

    success: bool
    roi_mask_paths: dict[str, str]
    roi_centers: dict[str, tuple[int, int, int]]
    error_message: str | None = None


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
    search_xy: int = 2,
    search_z: int = 1,
) -> tuple[tuple[int, int, int], float, float]:
    """
    Refine ROI placement by searching nearby positions for better fiber purity.

    Starting from the template-based centroid, search a small neighborhood
    (±search_xy voxels in X/Y, ±search_z voxels in Z) to find the position
    that maximizes the combined quality score (purity * direction * FA).

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
        Sphere radius in millimeters
    search_xy : int
        Search range in X and Y directions (voxels)
    search_z : int
        Search range in Z direction (voxels)

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

    for dx in range(-search_xy, search_xy + 1):
        for dy in range(-search_xy, search_xy + 1):
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

                # Create sphere at test position
                sphere = create_sphere_mask(shape, test_center, radius_mm, voxel_size)

                # Calculate quality metrics
                purity, _, _, score = calculate_roi_quality(v1_data, fa_data, sphere, fiber_type)

                if score > best_score:
                    best_score = score
                    best_center = test_center
                    best_purity = purity

    return best_center, best_purity, best_score
