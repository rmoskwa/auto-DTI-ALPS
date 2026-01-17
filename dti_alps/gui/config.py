"""
Configuration constants and default values for DTI-ALPS GUI.
"""

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

# Default preprocessing parameters
DEFAULT_READOUT_TIME = 0.05  # seconds
DEFAULT_PE_DIRECTION = "AP"
DEFAULT_RPE_SCHEME = "none"

# Default ROI detection parameters (from DTIALPSDetector)
DEFAULT_FA_THRESH = 0.25
DEFAULT_ORIENT_THRESH = 0.7
DEFAULT_MIN_ZONE_WIDTH = 5
DEFAULT_ROI_RADIUS_MM = 3.0  # Fixed: was 4.0, should match detector default
DEFAULT_Z_TOLERANCE = 2

# Registration-based ROI placement parameters
DEFAULT_ROI_SPHERE_RADIUS = 2.0  # Sphere radius in mm for registration-based ROI placement

# Parameter ranges for validation
FA_THRESH_RANGE = (0.1, 0.5)
ORIENT_THRESH_RANGE = (0.5, 0.9)
MIN_ZONE_WIDTH_RANGE = (3, 15)
ROI_RADIUS_RANGE = (2.0, 8.0)
ROI_SPHERE_RADIUS_RANGE = (1.0, 6.0)  # Range for registration-based sphere radius
Z_TOLERANCE_RANGE = (0, 5)
READOUT_TIME_RANGE = (0.001, 1.0)

# File type filters for file dialogs
NIFTI_FILETYPES = [("NIfTI files", "*.nii *.nii.gz"), ("All files", "*.*")]

BVEC_FILETYPES = [("bvec files", "*.bvec *.bvecs"), ("Text files", "*.txt"), ("All files", "*.*")]

BVAL_FILETYPES = [("bval files", "*.bval *.bvals"), ("Text files", "*.txt"), ("All files", "*.*")]

JSON_FILETYPES = [("JSON files", "*.json"), ("All files", "*.*")]

# Pipeline stages
PIPELINE_STAGES = [
    ("data", "Data Input"),
    ("dwidenoise", "dwidenoise"),
    ("mrdegibbs", "mrdegibbs"),
    ("dwifslpreproc", "dwifslpreproc"),
    ("dwi2tensor", "dwi2tensor"),
    ("tensor2metric", "tensor2metric"),
    ("roi", "ROI Detection"),
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

# MRtrix3 tensor volume indices
# dwi2tensor outputs: D11, D22, D33, D12, D13, D23
TENSOR_DXX_INDEX = 0  # D11
TENSOR_DYY_INDEX = 1  # D22
TENSOR_DZZ_INDEX = 2  # D33

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
}

# Tooltips for UI elements
TOOLTIPS = {
    "dwi": "4D diffusion-weighted image (NIfTI format)",
    "bvecs": "Gradient directions file (FSL format, 3xN or Nx3)",
    "bvals": "b-values file (FSL format)",
    "pe_dir": "Phase encoding direction of the DWI acquisition",
    "readout_time": "Total readout time in seconds (from acquisition parameters)",
    "rpe_scheme": "Reverse phase encoding acquisition scheme",
    "reverse_pe": "b=0 volume(s) with opposite phase encoding for distortion correction",
    "json_sidecar": "JSON file with acquisition metadata (BIDS format)",
    "fa_thresh": "Minimum FA value for white matter classification",
    "orient_thresh": "Minimum eigenvector component for fiber orientation classification",
    "min_zone_width": "Minimum contiguous fiber zone width (voxels) for ROI placement",
    "roi_radius": "Spherical ROI radius in millimeters",
    "z_tolerance": "Maximum Z-slice difference allowed between bilateral ROIs",
}
