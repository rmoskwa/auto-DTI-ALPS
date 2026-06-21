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

from ..results_layout import ROI_NAMES

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

    # Keyed by the canonical ROI-mask names; the on-disk template files follow
    # the JHU-labels-{name}.nii.gz convention.
    roi_templates = {name: templates_dir / f"JHU-labels-{name}.nii.gz" for name in ROI_NAMES}

    # Check all templates exist
    for path in roi_templates.values():
        if not path.exists():
            return None

    return roi_templates
