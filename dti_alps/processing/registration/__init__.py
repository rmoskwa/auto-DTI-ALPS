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

from .fsl import FSLRegistration
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
    # Common utilities
    "get_roi_template_paths",
]
