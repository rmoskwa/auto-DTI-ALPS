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

from ..tool_runner import ToolRunner
from .base import (
    RegistrationBackend,
    RegistrationResult,
    ROIPlacementResult,
    get_roi_template_paths,
)
from .fsl import (
    FSLRegistration,
    check_fsl_registration_available,
    get_fsl_bin_dir,
    get_fsldir,
    get_jhu_template_path,
)

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
    # Common utilities
    "get_roi_template_paths",
]


# Registry of available backends
_BACKENDS: dict[str, type[RegistrationBackend]] = {
    "fsl": FSLRegistration,
}


def get_backend(name: str = "fsl", runner: ToolRunner | None = None) -> RegistrationBackend:
    """
    Get a registration backend by name.

    Parameters
    ----------
    name : str
        Backend name ('fsl', 'ants' in future)
    runner : ToolRunner | None
        Seam for external command execution, threaded into the backend so a
        single fake injected at the top-level entry captures every command the
        backend issues. Defaults to a real subprocess-backed runner (the backend
        constructs one when given None).

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

    return _BACKENDS[name](runner=runner)


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
