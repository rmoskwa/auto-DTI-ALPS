"""
Processing pipeline components for DTI-ALPS.

This package provides modular components for the DTI-ALPS processing pipeline:

- state: Data classes for pipeline state management (PipelineState, BatchConfig, etc.)
- pipeline: Pipeline orchestration (PipelineRunner)
- batch: Batch processing (BatchRunner)
- workers: Background threading workers (PipelineWorker, BatchWorker)
- alps_calculation: ALPS index calculation functions
- commands: MRtrix3 command builders
- registration: FSL-based registration
- discovery: Subject file discovery
- validators: Input validation
- b0_extraction: B0 extraction and brain mask utilities
"""

# State classes
# ALPS calculation
from .alps_calculation import calculate_alps_lab, calculate_alps_pas, run_alps_calculation

# B0 extraction and brain masking
from .b0_extraction import (
    B0ExtractionResult,
    apply_mask_to_image,
    create_brain_mask_from_dwi,
    extract_and_average_b0,
    parse_bvals,
    validate_b0_exists,
)

# Batch processing
from .batch import BatchRunner

# Pipeline execution
from .pipeline import PipelineRunner
from .state import BatchConfig, BatchState, PipelineState, SubjectResult

# Background workers
from .workers import BatchWorker, PipelineWorker

__all__ = [
    # State classes
    "PipelineState",
    "BatchConfig",
    "SubjectResult",
    "BatchState",
    # Pipeline execution
    "PipelineRunner",
    # Batch processing
    "BatchRunner",
    # Workers
    "PipelineWorker",
    "BatchWorker",
    # ALPS calculation
    "calculate_alps_lab",
    "calculate_alps_pas",
    "run_alps_calculation",
    # B0 extraction and brain masking
    "B0ExtractionResult",
    "parse_bvals",
    "validate_b0_exists",
    "extract_and_average_b0",
    "create_brain_mask_from_dwi",
    "apply_mask_to_image",
]
