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
"""

# State classes
# ALPS calculation
from .alps_calculation import calculate_alps_lab, calculate_alps_pas, run_alps_calculation

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
]
