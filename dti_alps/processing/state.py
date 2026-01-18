"""
Data classes for DTI-ALPS pipeline state management.

This module contains all the dataclasses used to track pipeline state,
batch configuration, and processing results.
"""

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..gui import config

if TYPE_CHECKING:
    from .discovery import SubjectFiles


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
    pe_direction: str = config.DEFAULT_PE_DIRECTION
    readout_time: float = config.DEFAULT_READOUT_TIME
    rpe_scheme: str = config.DEFAULT_RPE_SCHEME

    # Stage 4: dwifslpreproc CLI options dict
    # Keys are option names (e.g., "-eddy_mask"), values are option values or True for flags
    dwifslpreproc_options: dict[str, Any] = field(default_factory=dict)

    # Legacy preprocessing fields (for backward compatibility)
    eddy_mask_path: str | None = None
    eddy_slspec_path: str | None = None
    eddy_options: str = ""
    topup_options: str = ""
    generate_qc: bool = False
    keep_intermediates: bool = False

    # Stage 3: dwi2tensor CLI options dict
    dwi2tensor_options: dict[str, Any] = field(default_factory=dict)

    # Legacy DTI fitting parameters
    dti_mask_path: str | None = None

    # Stage 6: tensor2metric CLI options dict
    tensor2metric_options: dict[str, Any] = field(default_factory=dict)

    # Stage 7: ROI placement parameters
    # ROI sphere radius for template-based ROI placement (mm)
    roi_sphere_radius: float = 3.0
    # FA threshold for filtering CSF voxels from ROIs
    fa_threshold: float = config.FA_THRESHOLD
    # ALPS calculation method (ALPS-LAB or ALPS-PAS)
    alps_method: str = "ALPS-LAB"
    # Enable ROI refinement to optimize fiber purity (search ±2 X/Y, ±1 Z voxels)
    refine_roi_placement: bool = True
    # Registration backend to use for FA-to-template registration ('fsl', 'ants' in future)
    registration_backend: str = "fsl"

    # Output settings
    output_dir: str = ""
    output_prefix: str = "subject"

    # Intermediate outputs (set during processing)
    denoised_dwi_path: str | None = None
    degibbs_dwi_path: str | None = None
    preprocessed_dwi_path: str | None = None
    tensor_path: str | None = None
    fa_path: str | None = None
    v1_path: str | None = None
    # ALPS-PAS specific outputs (eigenvalue and eigenvector maps)
    l2_path: str | None = None
    l3_path: str | None = None
    v2_path: str | None = None
    v3_path: str | None = None

    # ROI masks in native space (set by registration step)
    # Keys: 'left_proj', 'left_assoc', 'right_proj', 'right_assoc'
    roi_mask_paths: dict[str, str] = field(default_factory=dict)

    # Results
    roi_centers: dict[str, tuple] | None = None
    alps_results: dict[str, float] | None = None

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
        # ALPS-PAS specific outputs
        self.l2_path = self.get_output_path("L2.nii.gz")
        self.l3_path = self.get_output_path("L3.nii.gz")
        self.v2_path = self.get_output_path("V2.nii.gz")
        self.v3_path = self.get_output_path("V3.nii.gz")


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
    pe_direction: str = config.DEFAULT_PE_DIRECTION
    auto_pe_direction: bool = True  # Auto-extract PE direction from JSON if available
    readout_time: float | None = None  # None = auto-extract from JSON/NIfTI
    rpe_scheme: str = config.DEFAULT_RPE_SCHEME

    # CLI options dicts for each stage
    dwifslpreproc_options: dict[str, Any] = field(default_factory=dict)
    dwi2tensor_options: dict[str, Any] = field(default_factory=dict)
    tensor2metric_options: dict[str, Any] = field(default_factory=dict)

    # Legacy preprocessing options (for backward compatibility)
    eddy_options: str = ""
    topup_options: str = ""
    generate_qc: bool = False
    keep_intermediates: bool = False

    # ROI placement parameters
    roi_sphere_radius: float = 2.0  # Sphere radius in mm for template-based ROI placement
    fa_threshold: float = config.FA_THRESHOLD  # FA threshold for filtering CSF voxels
    alps_method: str = "ALPS-LAB"  # ALPS calculation method (ALPS-LAB or ALPS-PAS)
    registration_backend: str = "fsl"  # Registration backend ('fsl', 'ants' in future)

    # Output settings
    output_dir: str = ""


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

    # ALPS-LAB results
    alps_lab_left: float | None = None
    alps_lab_right: float | None = None
    alps_lab_bilateral: float | None = None

    # ALPS-PAS results (populated when method is ALPS-PAS or Both)
    alps_pas_left: float | None = None
    alps_pas_right: float | None = None
    alps_pas_bilateral: float | None = None

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
