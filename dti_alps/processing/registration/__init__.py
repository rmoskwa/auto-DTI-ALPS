"""
Registration backends for DTI-ALPS pipeline.

This package provides pluggable registration backends for transforming
ROI templates from standard space to subject native space.

Supported backends:
- fsl: FSL-based registration using FLIRT/FNIRT (default)
- ants: ANTs-based registration (planned for future)

Example usage:
    from dti_alps.processing.registration import get_backend

    # Get FSL backend (default)
    backend = get_backend("fsl")

    # Check if tools are available
    available, missing = backend.check_available()

    # Run registration
    result = backend.register(state, log_callback=print)
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from .base import (
    RegistrationBackend,
    RegistrationResult,
    ROIPlacementResult,
    calculate_roi_quality,
    create_sphere_mask,
    find_mask_centroid,
    get_roi_template_paths,
    refine_roi_placement,
)
from .fsl import (
    FSLRegistration,
    check_fsl_registration_available,
    get_fsl_bin_dir,
    get_fsldir,
    get_jhu_template_path,
)

if TYPE_CHECKING:
    from ..state import PipelineState

__all__ = [
    # Abstract interface
    "RegistrationBackend",
    "RegistrationResult",
    "ROIPlacementResult",
    # Factory function
    "get_backend",
    # FSL implementation
    "FSLRegistration",
    # Backward compatibility functions
    "check_fsl_registration_available",
    "get_fsldir",
    "get_fsl_bin_dir",
    "get_jhu_template_path",
    "register_fa_to_template",
    # Common utilities
    "get_roi_template_paths",
    "create_sphere_mask",
    "find_mask_centroid",
    "calculate_roi_quality",
    "refine_roi_placement",
]


# Registry of available backends
_BACKENDS: dict[str, type[RegistrationBackend]] = {
    "fsl": FSLRegistration,
}


def get_backend(name: str = "fsl") -> RegistrationBackend:
    """
    Get a registration backend by name.

    Parameters
    ----------
    name : str
        Backend name ('fsl', 'ants' in future)

    Returns
    -------
    RegistrationBackend
        Instance of the requested backend

    Raises
    ------
    ValueError
        If backend name is not recognized
    """
    if name not in _BACKENDS:
        available = ", ".join(_BACKENDS.keys())
        raise ValueError(f"Unknown registration backend: {name}. Available: {available}")

    return _BACKENDS[name]()


def register_backend(name: str, backend_class: type[RegistrationBackend]) -> None:
    """
    Register a new backend implementation.

    Parameters
    ----------
    name : str
        Name for the backend
    backend_class : type
        Class implementing RegistrationBackend
    """
    _BACKENDS[name] = backend_class


# =============================================================================
# Backward compatibility: register_fa_to_template function
# =============================================================================


def register_fa_to_template(
    state: "PipelineState",
    log_callback: Callable[[str], None] | None = None,
) -> bool:
    """
    Register subject FA to JHU template and transform ROI masks to native space.

    This function provides backward compatibility with the original API.
    It runs both registration and ROI placement in sequence.

    Parameters
    ----------
    state : PipelineState
        Pipeline state with fa_path set
    log_callback : callable, optional
        Function to call with log messages

    Returns
    -------
    bool
        True if registration and ROI placement succeeded
    """
    # Get backend name from state, defaulting to FSL
    backend_name = getattr(state, "registration_backend", "fsl")

    # Get backend instance
    backend = get_backend(backend_name)

    # Run registration
    reg_result = backend.register(state, log_callback)
    if not reg_result.success:
        return False

    # Run ROI placement
    roi_result = backend.place_rois(state, log_callback)

    # Update state with results (for backward compatibility)
    if roi_result.success:
        state.roi_mask_paths = roi_result.roi_mask_paths
        state.roi_centers = roi_result.roi_centers

    return roi_result.success
