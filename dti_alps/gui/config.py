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

# Registration-based ROI placement parameters
DEFAULT_ROI_SPHERE_RADIUS = 2.0  # Sphere radius in mm for ROI placement
FA_THRESHOLD = 0.2  # Minimum FA value for ROI voxels (filters out CSF)

# ALPS calculation methods
ALPS_METHODS = ["ALPS-LAB", "ALPS-PAS", "Both"]
DEFAULT_ALPS_METHOD = "ALPS-LAB"

# Parameter ranges for validation
ROI_SPHERE_RADIUS_RANGE = (1.0, 6.0)  # Range for ROI sphere radius
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
    ("registration", "Registration"),
    ("roi", "ROI Placement"),
    ("results", "Results"),
]

# Output file options - what files the pipeline can save
# Format: (output_key, display_name, description, default_enabled)
OUTPUT_FILE_OPTIONS = [
    # Preprocessing outputs
    ("denoised_dwi", "Denoised DWI", "DWI after thermal noise removal (dwidenoise)", True),
    ("degibbs_dwi", "Degibbs DWI", "DWI after Gibbs ringing removal (mrdegibbs)", True),
    ("preprocessed_dwi", "Preprocessed DWI", "Final preprocessed DWI (dwifslpreproc)", True),
    (
        "preprocessed_bvecs",
        "Preprocessed bvecs/bvals",
        "Corrected gradient directions and b-values",
        True,
    ),
    # DTI outputs
    ("tensor", "Diffusion Tensor", "Fitted diffusion tensor image", True),
    ("fa_map", "FA Map", "Fractional anisotropy map", True),
    (
        "eigenvector_maps",
        "Eigenvector/eigenvalue maps",
        "V1, V2, V3, L1, L2, L3 maps",
        True,
    ),
    # Registration outputs
    ("fa_brain", "Skull-stripped FA", "FA image after BET2 skull stripping", True),
    ("affine_matrix", "Affine Matrix", "FLIRT linear transformation matrix", True),
    ("warp_coefficients", "Warp Coefficients", "FNIRT non-linear warp coefficients", True),
    ("inverse_warp", "Inverse Warp", "Inverse warp for ROI transformation", True),
    # ROI outputs
    ("roi_masks", "ROI Masks", "Spherical ROI masks in native space", True),
    # Log file
    ("log_file", "Processing Log", "Detailed log of pipeline execution", True),
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
# BET2 options
BET2_OPTIONS = [
    ("-f", "string", "Fractional intensity threshold (0->1, smaller=larger brain)", "0.3"),
    ("-g", "string", "Vertical gradient (-1->1, positive=larger at bottom)", None),
]

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
    "processing": "#5C4D9A",  # Purple for processing state
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
    "roi_sphere_radius": "Spherical ROI radius in millimeters for template-based placement",
}
