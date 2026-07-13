"""
Configuration constants and default values for DTI-ALPS GUI.
"""

from dataclasses import dataclass

# Domain constants live in the engine (processing/constants.py). They are
# re-exported here (not redefined) so the GUI's existing ``config.X`` references
# keep resolving and the GUI and engine cannot disagree about a value. The
# engine is the source of truth -- change a value in processing/constants.py,
# not here. (noqa: these names are intentional re-exports, not dead imports.)
from ..processing.constants import (  # noqa: F401
    DEFAULT_PE_DIRECTION,
    DEFAULT_READOUT_TIME,
    DEFAULT_RPE_SCHEME,
    FA_THRESHOLD,
    READOUT_TIME_RANGE,
    ROI_SPHERE_RADIUS_RANGE,
    TENSOR_DXX_INDEX,
    TENSOR_DYY_INDEX,
    TENSOR_DZZ_INDEX,
)

# Application info
APP_NAME = "DTI-ALPS Processing Tool"
APP_VERSION = "0.1.0"

# Window dimensions
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800
WINDOW_MIN_WIDTH = 900
WINDOW_MIN_HEIGHT = 600

# Phase encoding directions
PE_DIRECTIONS = ["AP", "PA", "LR", "RL", "SI", "IS"]

# RPE scheme options
RPE_SCHEMES = {
    "none": "No reverse PE (eddy correction only)",
    "pair": "Reverse PE b=0 volumes provided",
    "all": "All DWIs have opposing PE",
    "header": "PE info from image headers",
}

# ALPS calculation methods
ALPS_METHODS = ["ALPS-LAB", "ALPS-PAS", "Both"]
DEFAULT_ALPS_METHOD = "Both"

# ROI method options
ROI_METHOD_OPTIONS = ["Adaptive", "Standard", "Both"]
DEFAULT_ROI_METHOD = "Both"


# ROI shape catalog (PRD 0015) — the single ordered source for the *selectable*
# ROI shapes. One row per shape; the checkbox adapter reads token/label/default
# and the row order, the form builder reads token/geometry. This owns the closed
# input-selection vocabulary; the viewer's roi_display_name parses the open
# on-disk vocabulary separately. Exactly one row is default=True — it both
# pre-checks the box and is the "nothing selected" fallback in form_model.
@dataclass(frozen=True)
class RoiShape:
    """One selectable ROI shape: its token, GUI label, engine geometry, default."""

    token: str  # input-selection token, e.g. "sphere3", "squarev9"
    label: str  # GUI display text, e.g. "Sphere 3.0mm"
    geometry: dict  # engine contract value passed into BatchConfig.roi_shapes
    default: bool  # the one canonical default (pre-check + empty-selection fallback)


ROI_SHAPES = (
    RoiShape("sphere2", "Sphere 2.0mm", {"type": "sphere", "radius": 2.0}, False),
    RoiShape("sphere2p5", "Sphere 2.5mm", {"type": "sphere", "radius": 2.5}, False),
    RoiShape("sphere3", "Sphere 3.0mm", {"type": "sphere", "radius": 3.0}, True),
    RoiShape("squarev4", "Square 2x2", {"type": "squarev4"}, False),
    RoiShape("squarev9", "Square 3x3", {"type": "squarev9"}, False),
)

# File type filters for file dialogs
NIFTI_FILETYPES = [("NIfTI files", "*.nii *.nii.gz"), ("All files", "*.*")]

JSON_FILETYPES = [("JSON files", "*.json"), ("All files", "*.*")]

# Pipeline stages (standard mode)
PIPELINE_STAGES = [
    ("data", "Data Input"),
    ("dwidenoise", "dwidenoise"),
    ("mrdegibbs", "mrdegibbs"),
    ("dwifslpreproc", "dwifslpreproc"),
    ("dwi2tensor", "dwi2tensor"),
    ("tensor2metric", "tensor2metric"),
    ("registration", "Registration"),
    ("roi", "ROI Placement"),
    ("results", "Results"),
]

# Pipeline stages (synB0-DISCO mode) - replaces dwifslpreproc with synB0 outputs + eddy
# User runs synB0-DISCO externally and provides the output directory
SYNB0_PIPELINE_STAGES = [
    ("data", "Data Input"),
    ("dwidenoise", "dwidenoise"),
    ("mrdegibbs", "mrdegibbs"),
    ("synb0", "synB0-DISCO"),  # User provides output directory
    ("eddy", "Eddy"),  # Runs eddy with synB0 topup outputs
    ("dwi2tensor", "dwi2tensor"),
    ("tensor2metric", "tensor2metric"),
    ("registration", "Registration"),
    ("roi", "ROI Placement"),
    ("results", "Results"),
]

# CLI option definitions for dwidenoise
# Format: (option_name, option_type, description, default_value)
# option_type: "file", "dir", "string", "int", "flag", "prefix", "choice"
DWIDENOISE_OPTIONS = [
    ("-mask", "file", "Processing mask (recommended)", None),
    ("-extent", "string", "Patch size (e.g., 5,5,5)", None),
    ("-noise", "output", "Output noise level map", None),
    ("-datatype", "choice", "Precision for computation", None),
    ("-estimator", "choice", "Noise estimator algorithm", None),
    ("-nthreads", "int", "Thread count", None),
]

# Choices for dwidenoise options
DWIDENOISE_DATATYPE_CHOICES = ["float32", "float64"]
DWIDENOISE_ESTIMATOR_CHOICES = ["Exp1", "Exp2"]

# CLI option definitions for mrdegibbs
MRDEGIBBS_OPTIONS = [
    ("-axes", "string", "Slice axes (default: 0,1)", None),
    ("-nshifts", "int", "Subpixel shifts (default: 20)", None),
    ("-minW", "int", "Left TV window border (default: 1)", None),
    ("-maxW", "int", "Right TV window border (default: 3)", None),
    ("-nthreads", "int", "Thread count", None),
]

# CLI option definitions for dwifslpreproc
# Format: (option_name, option_type, description, default_value)
# option_type: "file", "dir", "string", "int", "flag", "prefix"
DWIFSLPREPROC_OPTIONS = [
    ("-eddy_mask", "file", "Processing mask for eddy", None),
    ("-eddy_slspec", "file", "Slice specification file", None),
    ("-eddy_options", "string", "Extra eddy flags (e.g., --repol)", None),
    ("-topup_options", "string", "Extra topup flags", None),
    ("-topup_files", "prefix", "Pre-computed topup prefix", None),
    ("-align_seepi", "flag", "Align SE-EPI to DWI", False),
    ("-eddyqc_text", "dir", "Text QC output directory", None),
    ("-eddyqc_all", "dir", "Full QC output directory", None),
    ("-json_import", "file", "Import JSON metadata", None),
    ("-nocleanup", "flag", "Keep intermediate files", False),
    ("-nthreads", "int", "Thread count", None),
    ("-scratch", "dir", "Scratch directory", None),
]

# CLI option definitions for dwi2tensor
DWI2TENSOR_OPTIONS = [
    ("-ols", "flag", "Use ordinary least-squares (instead of IWLS)", False),
    ("-iter", "int", "IWLS iterations (default: 2)", None),
    ("-mask", "file", "Processing mask", None),
    ("-b0", "output", "Output b0 image", None),
    ("-dkt", "output", "Output kurtosis tensor", None),
    ("-predicted_signal", "output", "Output predicted signal", None),
    ("-nthreads", "int", "Thread count", None),
]

# CLI option definitions for tensor2metric (FA and V1 always computed)
TENSOR2METRIC_OPTIONS = [
    ("-mask", "file", "Processing mask", None),
    ("-adc", "output", "Mean diffusivity output", None),
    ("-ad", "output", "Axial diffusivity output", None),
    ("-rd", "output", "Radial diffusivity output", None),
    ("-cl", "output", "Linearity metric output", None),
    ("-cp", "output", "Planarity metric output", None),
    ("-cs", "output", "Sphericity metric output", None),
    ("-modulate", "choice", "Modulation (none/FA/eigval)", None),
    ("-num", "int", "Eigenvalue number for vector output", None),
    ("-nthreads", "int", "Thread count", None),
]

# Choices for tensor2metric -modulate option
TENSOR2METRIC_MODULATE_CHOICES = ["none", "FA", "eigval"]

# Registration parameters (FSL FLIRT/FNIRT)
# Brain extraction info (dwi2mask is used automatically, no user-configurable options)
# dwi2mask creates a brain mask from the preprocessed DWI data, which is then applied to FA
DWI2MASK_INFO = {
    "description": "Brain mask extraction using MRtrix3's dwi2mask",
    "input": "Preprocessed DWI data with bvecs/bvals",
    "output": "Binary brain mask",
    "note": "More reliable than BET2 for diffusion data",
}

# FLIRT options
FLIRT_OPTIONS = [
    ("-dof", "choice", "Degrees of freedom for transformation", "12"),
    ("-cost", "choice", "Cost function for registration", "corratio"),
    ("-searchrx", "string", "Search range in x-rotation (min max degrees)", "-30 30"),
    ("-searchry", "string", "Search range in y-rotation (min max degrees)", "-30 30"),
    ("-searchrz", "string", "Search range in z-rotation (min max degrees)", "-30 30"),
    ("-interp", "choice", "Interpolation method", None),
]

# Choices for FLIRT options
FLIRT_DOF_CHOICES = ["6", "7", "9", "12"]
FLIRT_COST_CHOICES = ["corratio", "mutualinfo", "normcorr", "normmi", "leastsq"]
FLIRT_INTERP_CHOICES = ["trilinear", "nearestneighbour", "sinc", "spline"]

# FNIRT options
FNIRT_OPTIONS = [
    ("--intmod", "choice", "Intensity modulation model", "none"),
    ("--jacrange", "string", "Jacobian range (min,max) to constrain warps", "0.2,5"),
    ("--lambda", "string", "Regularization weights (comma-separated)", "300,150,100,50"),
    ("--subsamp", "string", "Subsampling levels (comma-separated)", "4,2,2,1"),
    ("--miter", "string", "Max iterations per level (comma-separated)", "5,5,3,3"),
    ("--warpres", "string", "Warp resolution in mm (x,y,z)", "10,10,10"),
]

# Choices for FNIRT options
FNIRT_INTMOD_CHOICES = ["none", "global_linear", "local_linear"]

# synB0-DISCO eddy options (user runs synB0 externally, we run eddy with their topup outputs)
SYNB0_EDDY_OPTIONS = [
    ("repol", "flag", "Replace outlier slices (recommended)", True),
    ("cnr_maps", "flag", "Output CNR maps", False),
    ("residuals", "flag", "Output residuals", False),
    ("slm", "choice", "Second level model for eddy", None),
    ("niter", "int", "Number of iterations (default: 5)", None),
    ("fwhm", "string", "FWHM for conditioning (comma-separated)", None),
    ("s2v_niter", "int", "Slice-to-volume iterations (default: 5)", None),
    ("mporder", "int", "Motion model order (default: 0)", None),
    ("verbose", "flag", "Verbose output", False),
]

# synB0 eddy slm choices
SYNB0_EDDY_SLM_CHOICES = ["none", "linear", "quadratic"]

# Color scheme
COLORS = {
    "proj_left": "#0066CC",  # Blue
    "proj_right": "#00CCCC",  # Cyan
    "assoc_left": "#CC0000",  # Red
    "assoc_right": "#FF6600",  # Orange
    "background": "#F0F0F0",
    "sidebar": "#E0E0E0",
    "progress_bg": "#FFFFFF",
    "success": "#28A745",
    "error": "#DC3545",
    "warning": "#FFC107",
    "processing": "#5C4D9A",  # Purple for processing state
}
