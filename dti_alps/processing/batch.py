"""
Batch processing for DTI-ALPS pipeline.

This module handles processing multiple subjects sequentially,
tracking results, and writing output files.
"""

import csv
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..gui import config
from .state import BatchState, PipelineState, SubjectResult

if TYPE_CHECKING:
    from .discovery import SubjectFiles


class BatchRunner:
    """
    Orchestrates batch processing of multiple subjects.

    Processes subjects sequentially, maintaining progress state
    and handling partial failures gracefully.
    """

    def __init__(
        self,
        batch_state: BatchState,
        progress_callback: Callable[[str, Any], None] | None = None,
    ):
        """
        Initialize the batch runner.

        Parameters
        ----------
        batch_state : BatchState
            Batch configuration and subject list
        progress_callback : callable, optional
            Callback function for progress updates
        """
        self.batch_state = batch_state
        self.progress_callback = progress_callback or (lambda t, d: None)
        self.cancelled = False

    def _notify(self, msg_type: str, data: Any) -> None:
        """Send notification via callback."""
        self.progress_callback(msg_type, data)

    def _create_subject_pipeline_state(self, subject_files: "SubjectFiles") -> PipelineState:
        """
        Convert SubjectFiles + BatchConfig into PipelineState for single-subject processing.

        Parameters
        ----------
        subject_files : SubjectFiles
            Files for this subject

        Returns
        -------
        PipelineState
            Configured state for single-subject pipeline
        """
        from .discovery import (
            extract_phase_encoding_direction,
            extract_readout_time,
            parse_json_sidecar,
        )

        batch_config = self.batch_state.config

        # Parse JSON sidecar if available
        json_data = {}
        if subject_files.json_sidecar_path:
            json_data = parse_json_sidecar(subject_files.json_sidecar_path)

        # Determine readout time: from config, or auto-extract from JSON/NIfTI
        readout_time = batch_config.readout_time
        if readout_time is None:
            readout_time = extract_readout_time(json_data, subject_files.dwi_path)

        if readout_time is None:
            readout_time = config.DEFAULT_READOUT_TIME  # fallback default
            self._notify(
                "log",
                f"  Warning: Could not extract readout time for {subject_files.subject_id}, "
                f"using default {readout_time}s",
            )

        # Determine PE direction: from config, or auto-extract from JSON
        pe_direction = batch_config.pe_direction
        if batch_config.auto_pe_direction and json_data:
            extracted_pe = extract_phase_encoding_direction(json_data)
            if extracted_pe:
                pe_direction = extracted_pe
                self._notify(
                    "log", f"  Auto-detected PE direction: {pe_direction} (from JSON sidecar)"
                )

        # Create per-subject output directory
        subject_output_dir = os.path.join(batch_config.output_dir, subject_files.subject_id)

        state = PipelineState(
            # Input files
            dwi_path=subject_files.dwi_path,
            bvecs_path=subject_files.bvec_path,
            bvals_path=subject_files.bval_path,
            json_sidecar_path=subject_files.json_sidecar_path,
            reverse_pe_path=subject_files.reverse_pe_path,
            # Denoising parameters
            run_denoising=batch_config.run_denoising,
            dwidenoise_options=dict(batch_config.dwidenoise_options),
            # Gibbs ringing removal parameters
            run_degibbs=batch_config.run_degibbs,
            mrdegibbs_options=dict(batch_config.mrdegibbs_options),
            # Preprocessing parameters
            pe_direction=pe_direction,
            readout_time=readout_time,
            rpe_scheme=batch_config.rpe_scheme,
            # CLI options dicts
            dwifslpreproc_options=dict(batch_config.dwifslpreproc_options),
            dwi2tensor_options=dict(batch_config.dwi2tensor_options),
            tensor2metric_options=dict(batch_config.tensor2metric_options),
            # Legacy preprocessing options (for backward compatibility)
            eddy_options=batch_config.eddy_options,
            topup_options=batch_config.topup_options,
            generate_qc=batch_config.generate_qc,
            keep_intermediates=batch_config.keep_intermediates,
            # ROI placement parameters
            roi_sphere_radius=batch_config.roi_sphere_radius,
            fa_threshold=batch_config.fa_threshold,
            alps_method=batch_config.alps_method,
            registration_backend=batch_config.registration_backend,
            # Output settings
            output_dir=subject_output_dir,
            output_prefix=subject_files.subject_id,
        )

        return state

    def run_batch(self) -> bool:
        """
        Run batch processing for all subjects.

        Returns
        -------
        bool
            True if all subjects succeeded, False if any failed
        """
        # Import here to avoid circular imports
        from .pipeline import PipelineRunner

        total = self.batch_state.total_subjects
        self._notify("batch_start", total)
        self._notify("log", f"Starting batch processing for {total} subjects")

        for i, subject_files in enumerate(self.batch_state.subjects):
            if self.cancelled:
                self._mark_remaining_skipped(i)
                break

            self.batch_state.current_subject_index = i
            result = self._process_single_subject(subject_files, i, PipelineRunner)
            self.batch_state.results.append(result)

            self._notify("subject_complete", (i, result))

        # Write CSV output
        self._write_csv_results()

        self._notify("batch_complete", self.batch_state)
        return self.batch_state.success_count == self.batch_state.total_subjects

    def _process_single_subject(
        self,
        subject_files: "SubjectFiles",
        index: int,
        pipeline_runner_class: type,
    ) -> SubjectResult:
        """
        Process a single subject with error handling.

        Parameters
        ----------
        subject_files : SubjectFiles
            Files for this subject
        index : int
            Index in subject list
        pipeline_runner_class : type
            The PipelineRunner class to use for processing

        Returns
        -------
        SubjectResult
            Result object with status and values
        """
        start_time = time.time()

        result = SubjectResult(
            subject_id=subject_files.subject_id,
            folder_path=subject_files.folder_path,
            status="running",
        )

        self._notify("subject_start", (index, subject_files.subject_id))
        self._notify(
            "log",
            f"Processing subject {index + 1}/{self.batch_state.total_subjects}: "
            f"{subject_files.subject_id}",
        )

        try:
            # Create single-subject state
            state = self._create_subject_pipeline_state(subject_files)

            # Create progress callback that forwards to batch callback
            def subject_progress(msg_type: str, data: Any):
                self._notify(msg_type, data)
                # Check cancellation
                if self.cancelled:
                    pass  # Will be caught by runner

            # Create and run single-subject pipeline
            runner = pipeline_runner_class(state, progress_callback=subject_progress)

            # Link cancellation
            if self.cancelled:
                runner.cancelled = True

            success = runner.run_full_pipeline()

            if success and state.alps_results:
                result.status = "completed"
                result.alps_method = state.alps_results.get("method")

                # Store ALPS-LAB results (if available)
                result.alps_lab_left = state.alps_results.get("LAB_ALPS_left")
                result.alps_lab_right = state.alps_results.get("LAB_ALPS_right")
                result.alps_lab_bilateral = state.alps_results.get("LAB_ALPS_bilateral")

                # Store ALPS-PAS results (if available)
                result.alps_pas_left = state.alps_results.get("PAS_ALPS_left")
                result.alps_pas_right = state.alps_results.get("PAS_ALPS_right")
                result.alps_pas_bilateral = state.alps_results.get("PAS_ALPS_bilateral")

                # Store detailed diffusivity values (from LAB method)
                result.dxx_proj_left = state.alps_results.get("LAB_Dxx_proj_left")
                result.dxx_proj_right = state.alps_results.get("LAB_Dxx_proj_right")
                result.dyy_proj_left = state.alps_results.get("LAB_Dyy_proj_left")
                result.dyy_proj_right = state.alps_results.get("LAB_Dyy_proj_right")
                result.dxx_assoc_left = state.alps_results.get("LAB_Dxx_assoc_left")
                result.dxx_assoc_right = state.alps_results.get("LAB_Dxx_assoc_right")
                result.dzz_assoc_left = state.alps_results.get("LAB_Dzz_assoc_left")
                result.dzz_assoc_right = state.alps_results.get("LAB_Dzz_assoc_right")

                # Log appropriate results based on method
                method = result.alps_method
                if method == "ALPS-LAB" and result.alps_lab_bilateral is not None:
                    self._notify(
                        "log",
                        f"  ALPS-LAB: L={result.alps_lab_left:.4f}, "
                        f"R={result.alps_lab_right:.4f}, Bi={result.alps_lab_bilateral:.4f}",
                    )
                elif method == "ALPS-PAS" and result.alps_pas_bilateral is not None:
                    self._notify(
                        "log",
                        f"  ALPS-PAS: L={result.alps_pas_left:.4f}, "
                        f"R={result.alps_pas_right:.4f}, Bi={result.alps_pas_bilateral:.4f}",
                    )
                elif method == "Both":
                    if result.alps_lab_bilateral is not None:
                        self._notify(
                            "log",
                            f"  ALPS-LAB: L={result.alps_lab_left:.4f}, "
                            f"R={result.alps_lab_right:.4f}, Bi={result.alps_lab_bilateral:.4f}",
                        )
                    if result.alps_pas_bilateral is not None:
                        self._notify(
                            "log",
                            f"  ALPS-PAS: L={result.alps_pas_left:.4f}, "
                            f"R={result.alps_pas_right:.4f}, Bi={result.alps_pas_bilateral:.4f}",
                        )
            else:
                result.status = "failed"
                result.error_message = "Pipeline execution failed"
                self._notify("log", "  FAILED: Pipeline execution failed")

        except Exception as e:
            result.status = "failed"
            result.error_message = str(e)
            self._notify("log", f"  FAILED: {e}")

        result.processing_time = time.time() - start_time
        return result

    def _mark_remaining_skipped(self, start_index: int) -> None:
        """Mark remaining subjects as skipped due to cancellation."""
        for i in range(start_index, len(self.batch_state.subjects)):
            subject_files = self.batch_state.subjects[i]
            result = SubjectResult(
                subject_id=subject_files.subject_id,
                folder_path=subject_files.folder_path,
                status="skipped",
                error_message="Batch cancelled by user",
            )
            self.batch_state.results.append(result)

    def _write_csv_results(self) -> None:
        """Write batch results to CSV file with method-specific column names."""
        csv_path = os.path.join(self.batch_state.config.output_dir, "alps_results.csv")
        alps_method = self.batch_state.config.alps_method

        try:
            os.makedirs(self.batch_state.config.output_dir, exist_ok=True)

            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)

                # Build header based on method
                if alps_method == "ALPS-LAB":
                    header = [
                        "Filename",
                        "Left Hemisphere ALPS-LAB",
                        "Right Hemisphere ALPS-LAB",
                        "Combined ALPS-LAB",
                        "Status",
                        "Error",
                    ]
                elif alps_method == "ALPS-PAS":
                    header = [
                        "Filename",
                        "Left Hemisphere ALPS-PAS",
                        "Right Hemisphere ALPS-PAS",
                        "Combined ALPS-PAS",
                        "Status",
                        "Error",
                    ]
                else:  # Both
                    header = [
                        "Filename",
                        "Left Hemisphere ALPS-LAB",
                        "Right Hemisphere ALPS-LAB",
                        "Combined ALPS-LAB",
                        "Left Hemisphere ALPS-PAS",
                        "Right Hemisphere ALPS-PAS",
                        "Combined ALPS-PAS",
                        "Status",
                        "Error",
                    ]

                writer.writerow(header)

                for result in self.batch_state.results:
                    if alps_method == "ALPS-LAB":
                        row = [
                            result.subject_id,
                            f"{result.alps_lab_left:.6f}"
                            if result.alps_lab_left is not None
                            else "",
                            f"{result.alps_lab_right:.6f}"
                            if result.alps_lab_right is not None
                            else "",
                            f"{result.alps_lab_bilateral:.6f}"
                            if result.alps_lab_bilateral is not None
                            else "",
                            result.status,
                            result.error_message or "",
                        ]
                    elif alps_method == "ALPS-PAS":
                        row = [
                            result.subject_id,
                            f"{result.alps_pas_left:.6f}"
                            if result.alps_pas_left is not None
                            else "",
                            f"{result.alps_pas_right:.6f}"
                            if result.alps_pas_right is not None
                            else "",
                            f"{result.alps_pas_bilateral:.6f}"
                            if result.alps_pas_bilateral is not None
                            else "",
                            result.status,
                            result.error_message or "",
                        ]
                    else:  # Both
                        row = [
                            result.subject_id,
                            f"{result.alps_lab_left:.6f}"
                            if result.alps_lab_left is not None
                            else "",
                            f"{result.alps_lab_right:.6f}"
                            if result.alps_lab_right is not None
                            else "",
                            f"{result.alps_lab_bilateral:.6f}"
                            if result.alps_lab_bilateral is not None
                            else "",
                            f"{result.alps_pas_left:.6f}"
                            if result.alps_pas_left is not None
                            else "",
                            f"{result.alps_pas_right:.6f}"
                            if result.alps_pas_right is not None
                            else "",
                            f"{result.alps_pas_bilateral:.6f}"
                            if result.alps_pas_bilateral is not None
                            else "",
                            result.status,
                            result.error_message or "",
                        ]

                    writer.writerow(row)

            self._notify("log", f"Results saved to {csv_path}")

        except OSError as e:
            self._notify("log", f"ERROR: Failed to write CSV: {e}")

    def cancel(self) -> None:
        """Request batch cancellation."""
        self.cancelled = True
