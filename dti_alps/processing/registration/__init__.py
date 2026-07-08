"""
FSL-based registration for the DTI-ALPS pipeline.

Transforms ROI templates from standard (JHU) space to subject native space via
FLIRT/FNIRT. The pipeline constructs `FSLRegistration` directly, threading in a
`ToolRunner` so a single fake injected at the top captures every FSL command.

Example usage:
    from dti_alps.processing.registration import FSLRegistration

    backend = FSLRegistration()

    # Check if tools are available
    available, missing = backend.check_available()

    # Run registration
    result = backend.register(state, log_callback=print)
"""

from .fsl import (
    FSLRegistration,
    check_fsl_registration_available,
    get_fsl_bin_dir,
    get_fsldir,
    get_jhu_template_path,
)
from .results import (
    RegistrationResult,
    ROIPlacementResult,
    get_roi_template_paths,
)

__all__ = [
    # Result contract
    "RegistrationResult",
    "ROIPlacementResult",
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
