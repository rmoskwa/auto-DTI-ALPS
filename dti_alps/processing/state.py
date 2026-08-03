"""
Data classes for DTI-ALPS pipeline state management.

This module contains all the dataclasses used to track pipeline state,
batch configuration, and processing results.
"""

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import results_layout
from .constants import (
    DEFAULT_ALPS_METHOD,
    DEFAULT_PE_DIRECTION,
    DEFAULT_READOUT_TIME,
    DEFAULT_ROI_METHOD,
    DEFAULT_RPE_SCHEME,
    FA_THRESHOLD,
    AdaptiveSearchConfig,
)

if TYPE_CHECKING:
    from .discovery import SubjectFiles


@dataclass
class OutputConfig:
    """
    Configuration for which output files to keep after pipeline execution.

    By default, all outputs are kept. Users can disable specific outputs
    to save disk space.
    """

    # Preprocessing outputs
    denoised_dwi: bool = True  # DWI after dwidenoise
    degibbs_dwi: bool = True  # DWI after mrdegibbs
    preprocessed_dwi: bool = True  # DWI after dwifslpreproc
    preprocessed_bvecs: bool = True  # Corrected bvecs/bvals

    # DTI outputs
    tensor: bool = True  # Diffusion tensor image
    fa_map: bool = True  # Fractional anisotropy map
    eigenvector_maps: bool = True  # V1, V2, V3, L2, L3 eigenvector/eigenvalue maps

    # Registration outputs
    b0_image: bool = True  # Averaged b0 image
    brain_mask: bool = True  # Brain mask from dwi2mask
    fa_brain: bool = True  # Skull-stripped FA
    affine_matrix: bool = True  # FLIRT affine matrix
    warp_coefficients: bool = True  # FNIRT warp coefficients
    inverse_warp: bool = True  # Inverse warp for ROI transformation

    # ROI outputs
    roi_masks: bool = True  # Spherical ROI masks

    # Log file
    log_file: bool = True  # Processing log

    def to_dict(self) -> dict[str, bool]:
        """Convert to dictionary for serialization."""
        return {
            "denoised_dwi": self.denoised_dwi,
            "degibbs_dwi": self.degibbs_dwi,
            "preprocessed_dwi": self.preprocessed_dwi,
            "preprocessed_bvecs": self.preprocessed_bvecs,
            "tensor": self.tensor,
            "fa_map": self.fa_map,
            "eigenvector_maps": self.eigenvector_maps,
            "b0_image": self.b0_image,
            "brain_mask": self.brain_mask,
            "fa_brain": self.fa_brain,
            "affine_matrix": self.affine_matrix,
            "warp_coefficients": self.warp_coefficients,
            "inverse_warp": self.inverse_warp,
            "roi_masks": self.roi_masks,
            "log_file": self.log_file,
        }

    @classmethod
    def from_dict(cls, data: dict[str, bool]) -> "OutputConfig":
        """Create from dictionary."""
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})


@dataclass
class PipelineState:
    """
    Holds all input parameters and intermediate results for the DTI-ALPS pipeline.
    """

    # Stage 1: Input files
    dwi_path: str | None = None
    bvecs_path: str | None = None
    bvals_path: str | None = None
    reverse_pe_path: str | None = None
    json_sidecar_path: str | None = None

    # Stage 2: Denoising parameters
    run_denoising: bool = True
    dwidenoise_options: dict[str, Any] = field(default_factory=dict)

    # Stage 3: Gibbs ringing removal parameters
    run_degibbs: bool = True
    mrdegibbs_options: dict[str, Any] = field(default_factory=dict)

    # Stage 4: Preprocessing parameters (core)
    pe_direction: str = DEFAULT_PE_DIRECTION
    readout_time: float = DEFAULT_READOUT_TIME
    rpe_scheme: str = DEFAULT_RPE_SCHEME

    # Stage 4: dwifslpreproc CLI options dict
    # Keys are option names (e.g., "-eddy_mask"), values are option values or True for flags
    dwifslpreproc_options: dict[str, Any] = field(default_factory=dict)

    # Legacy preprocessing fields (for backward compatibility)
    eddy_mask_path: str | None = None
    eddy_slspec_path: str | None = None

    # synB0-DISCO alternative preprocessing (user runs synB0 externally)
    use_synb0: bool = False  # Use synB0-DISCO outputs instead of dwifslpreproc
    synb0_output_dir: str | None = None  # Path to synB0 OUTPUTS directory
    synb0_eddy_options: dict[str, Any] = field(default_factory=dict)  # Eddy options for synB0 mode

    # Stage 3: dwi2tensor CLI options dict
    dwi2tensor_options: dict[str, Any] = field(default_factory=dict)

    # Legacy DTI fitting parameters
    dti_mask_path: str | None = None

    # Stage 6: tensor2metric CLI options dict
    tensor2metric_options: dict[str, Any] = field(default_factory=dict)

    # Stage 7: Registration parameters (FSL FLIRT/FNIRT)
    flirt_options: dict[str, Any] = field(default_factory=dict)
    fnirt_options: dict[str, Any] = field(default_factory=dict)

    # Stage 8: ROI placement parameters
    # ROI shapes to create - list of dicts with 'type' and optional 'radius'
    # e.g., [{'type': 'sphere', 'radius': 3.0}, {'type': 'squarev9'}]
    roi_shapes: list[dict[str, Any]] = field(
        default_factory=lambda: [{"type": "sphere", "radius": 3.0}]
    )
    # FA threshold for filtering CSF voxels from ROIs
    fa_threshold: float = FA_THRESHOLD
    # ALPS calculation method (ALPS-LAB or ALPS-PAS)
    alps_method: str = DEFAULT_ALPS_METHOD
    # ROI placement mode: "Adaptive", "Standard", or "Both"
    adaptive_roi_placement: str = DEFAULT_ROI_METHOD
    # Adaptive search envelope (per-run tuning of the joint pair search)
    adaptive_search: AdaptiveSearchConfig = field(default_factory=AdaptiveSearchConfig)

    # Output settings
    output_dir: str = ""
    output_prefix: str = "subject"
    output_config: OutputConfig = field(default_factory=OutputConfig)

    # Staging settings (copy to local storage for performance)
    staging_enabled: bool = False
    staging_dir: str | None = None  # Custom staging base dir; None = system temp

    # Intermediate outputs (set during processing)
    denoised_dwi_path: str | None = None
    degibbs_dwi_path: str | None = None
    preprocessed_dwi_path: str | None = None
    tensor_path: str | None = None
    fa_path: str | None = None
    v1_path: str | None = None
    # Eigenvalue and eigenvector maps
    l1_path: str | None = None
    l2_path: str | None = None
    l3_path: str | None = None
    v2_path: str | None = None
    v3_path: str | None = None

    # Registration intermediate outputs
    b0_path: str | None = None  # Averaged b0 image for brain extraction
    brain_mask_path: str | None = None  # Brain mask from dwi2mask
    fa_brain_path: str | None = None  # Skull-stripped FA
    affine_mat_path: str | None = None  # FLIRT affine matrix
    warp_coef_path: str | None = None  # FNIRT warp coefficients
    inverse_warp_path: str | None = None  # Inverse warp for ROI transformation

    # ROI masks in native space (set by ROI placement step)
    # Keys: 'left_proj', 'left_assoc', 'right_proj', 'right_assoc'
    roi_mask_paths: dict[str, str] = field(default_factory=dict)

    # All ROI results indexed by shape name (e.g., "rois_sphere3_adaptive")
    # Each entry contains: {"roi_mask_paths": {...}, "roi_centers": {...}}
    all_roi_results: dict[str, dict] | None = None

    # Results
    roi_centers: dict[str, tuple] | None = None
    alps_results: dict[str, float] | None = None

    # Per-shape ALPS results indexed by shape name (e.g., "sphere3_adaptive")
    # Each entry is a dict with ALPS calculation results for that shape
    alps_results_by_shape: dict[str, dict] | None = None

    def get_output_path(self, suffix: str) -> str:
        """Generate output file path with prefix and suffix."""
        return os.path.join(self.output_dir, f"{self.output_prefix}_{suffix}")

    def setup_output_paths(self) -> None:
        """Set up all intermediate output file paths."""
        self.denoised_dwi_path = self.get_output_path("dwi_denoised.nii.gz")
        self.degibbs_dwi_path = self.get_output_path("dwi_degibbs.nii.gz")
        self.preprocessed_dwi_path = self.get_output_path("dwi_preproc.nii.gz")
        self.tensor_path = self.get_output_path("tensor.nii.gz")
        self.fa_path = self.get_output_path("FA.nii.gz")
        self.v1_path = self.get_output_path("V1.nii.gz")
        # Eigenvalue and eigenvector maps
        self.l1_path = self.get_output_path("L1.nii.gz")
        self.l2_path = self.get_output_path("L2.nii.gz")
        self.l3_path = self.get_output_path("L3.nii.gz")
        self.v2_path = self.get_output_path("V2.nii.gz")
        self.v3_path = self.get_output_path("V3.nii.gz")
        # Registration outputs (in registration subdirectory)
        reg_dir = os.path.join(self.output_dir, results_layout.REGISTRATION_DIR)
        self.b0_path = os.path.join(reg_dir, f"{self.output_prefix}_b0_avg.nii.gz")
        self.brain_mask_path = os.path.join(
            reg_dir, results_layout.brain_mask_name(self.output_prefix)
        )
        self.fa_brain_path = os.path.join(reg_dir, f"{self.output_prefix}_FA_brain.nii.gz")
        self.affine_mat_path = os.path.join(reg_dir, f"{self.output_prefix}_subject2jhu_affine.mat")
        self.warp_coef_path = os.path.join(
            reg_dir, f"{self.output_prefix}_subject2jhu_warp_coef.nii.gz"
        )
        self.inverse_warp_path = os.path.join(
            reg_dir, f"{self.output_prefix}_jhu2subject_warp_coef.nii.gz"
        )


@dataclass
class BatchConfig:
    """
    Common parameters shared across all subjects in a batch.

    These parameters are applied uniformly to all subjects unless
    auto-extraction is enabled (e.g., readout_time from JSON sidecars).
    """

    # Denoising parameters
    run_denoising: bool = True
    dwidenoise_options: dict[str, Any] = field(default_factory=dict)

    # Gibbs ringing removal parameters
    run_degibbs: bool = True
    mrdegibbs_options: dict[str, Any] = field(default_factory=dict)

    # Preprocessing parameters
    pe_direction: str = DEFAULT_PE_DIRECTION
    auto_pe_direction: bool = True  # Auto-extract PE direction from JSON if available
    readout_time: float | None = None  # None = auto-extract from JSON/NIfTI
    rpe_scheme: str = DEFAULT_RPE_SCHEME

    # CLI options dicts for each stage
    dwifslpreproc_options: dict[str, Any] = field(default_factory=dict)
    dwi2tensor_options: dict[str, Any] = field(default_factory=dict)
    tensor2metric_options: dict[str, Any] = field(default_factory=dict)

    # synB0-DISCO alternative preprocessing (user runs synB0 externally)
    use_synb0: bool = False  # Use synB0-DISCO outputs instead of dwifslpreproc
    synb0_output_dir: str | None = None  # Path to synB0 OUTPUTS directory (shared for batch)
    synb0_eddy_options: dict[str, Any] = field(default_factory=dict)  # Eddy options for synB0 mode

    # Registration parameters (FSL FLIRT/FNIRT)
    flirt_options: dict[str, Any] = field(default_factory=dict)
    fnirt_options: dict[str, Any] = field(default_factory=dict)

    # ROI placement parameters
    # ROI shapes to create - list of dicts with 'type' and optional 'radius'
    roi_shapes: list[dict[str, Any]] = field(
        default_factory=lambda: [{"type": "sphere", "radius": 3.0}]
    )
    fa_threshold: float = FA_THRESHOLD  # FA threshold for filtering CSF voxels
    alps_method: str = DEFAULT_ALPS_METHOD  # ALPS-LAB, ALPS-PAS, or Both
    adaptive_roi_placement: str = DEFAULT_ROI_METHOD  # "Adaptive", "Standard", or "Both"
    # Adaptive search envelope (per-run tuning of the joint pair search)
    adaptive_search: AdaptiveSearchConfig = field(default_factory=AdaptiveSearchConfig)

    # Output settings
    output_dir: str = ""
    output_config: OutputConfig = field(default_factory=OutputConfig)

    # Staging settings
    staging_enabled: bool = False
    staging_dir: str | None = None


@dataclass
class SubjectResult:
    """
    Results for a single subject in batch processing.

    Tracks both successful results and failure information.
    """

    subject_id: str
    folder_path: str
    status: str = "pending"  # "pending", "running", "completed", "failed", "skipped"
    alps_method: str | None = None  # ALPS method used (ALPS-LAB, ALPS-PAS, or Both)
    error_message: str | None = None
    processing_time: float = 0.0

    # ALPS-LAB results (for primary/first shape - backward compatibility)
    alps_lab_left: float | None = None
    alps_lab_right: float | None = None
    alps_lab_bilateral: float | None = None

    # ALPS-PAS results (for primary/first shape - backward compatibility)
    alps_pas_left: float | None = None
    alps_pas_right: float | None = None
    alps_pas_bilateral: float | None = None

    # Per-shape ALPS results indexed by shape name (e.g., "sphere3_adaptive")
    # Each entry is a dict with: alps_lab_left, alps_lab_right, alps_lab_bilateral,
    # alps_pas_left, alps_pas_right, alps_pas_bilateral
    alps_results_by_shape: dict[str, dict] = field(default_factory=dict)

    # Detailed diffusivity values for ALPS-LAB (optional, populated on success)
    dxx_proj_left: float | None = None
    dxx_proj_right: float | None = None
    dyy_proj_left: float | None = None
    dyy_proj_right: float | None = None
    dxx_assoc_left: float | None = None
    dxx_assoc_right: float | None = None
    dzz_assoc_left: float | None = None
    dzz_assoc_right: float | None = None


@dataclass
class BatchState:
    """
    State for batch processing of multiple subjects.

    Holds configuration, subject list, and accumulated results.
    """

    config: BatchConfig
    subjects: list["SubjectFiles"] = field(default_factory=list)
    results: list[SubjectResult] = field(default_factory=list)
    current_subject_index: int = 0

    @property
    def total_subjects(self) -> int:
        """Get total number of subjects in batch."""
        return len(self.subjects)

    @property
    def completed_count(self) -> int:
        """Get number of subjects that have been processed (success or fail)."""
        return sum(1 for r in self.results if r.status in ("completed", "failed", "skipped"))

    @property
    def success_count(self) -> int:
        """Get number of successfully processed subjects."""
        return sum(1 for r in self.results if r.status == "completed")

    @property
    def failed_count(self) -> int:
        """Get number of failed subjects."""
        return sum(1 for r in self.results if r.status == "failed")
