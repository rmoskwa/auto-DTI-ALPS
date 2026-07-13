"""
Shared result types and template helper for the registration step.

The registration step's result contract (`RegistrationResult`,
`ROIPlacementResult`) that the pipeline reads, plus the tool-agnostic
`get_roi_template_paths` helper used by both the FSL backend and reanalysis.
None of these is FSL-specific.
"""

from dataclasses import dataclass
from pathlib import Path

from ..results_layout import ROI_NAMES


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
        All ROI results indexed by shape name (e.g., "sphere3_adaptive").
        Each entry contains: {"roi_mask_paths": {...}, "roi_centers": {...}}
    """

    success: bool
    roi_mask_paths: dict[str, str]
    roi_centers: dict[str, tuple[int, int, int]]
    error_message: str | None = None
    all_roi_results: dict[str, dict] | None = None


# =============================================================================
# Tool-agnostic helper functions
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
