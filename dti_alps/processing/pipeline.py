"""
Pipeline execution for DTI-ALPS processing.

This module contains the PipelineRunner class that orchestrates
the execution of individual pipeline stages.
"""

import os
import select
import subprocess
import time
from collections.abc import Callable
from typing import Any

from . import commands, registration
from .alps_calculation import run_alps_calculation

# Re-export classes for backward compatibility
from .batch import BatchRunner
from .state import BatchConfig, BatchState, OutputConfig, PipelineState, SubjectResult
from .workers import BatchWorker, PipelineWorker

__all__ = [
    "PipelineState",
    "BatchConfig",
    "OutputConfig",
    "SubjectResult",
    "BatchState",
    "PipelineRunner",
    "PipelineWorker",
    "BatchRunner",
    "BatchWorker",
]


class PipelineRunner:
    """
    Orchestrates the execution of the DTI-ALPS processing pipeline.

    Stages:
    1. Denoising with dwidenoise (optional)
    2. Gibbs ringing removal with mrdegibbs (optional)
    3. Preprocessing with dwifslpreproc
    4. DTI tensor fitting with dwi2tensor + tensor2metric (FA, V1)
    5. Registration - FA to JHU template (BET2, FLIRT, FNIRT, INVWARP)
    6. ROI Placement - Transform templates to native space, create spherical ROIs
    7. ALPS index calculation
    """

    def __init__(
        self, state: PipelineState, progress_callback: Callable[[str, Any], None] | None = None
    ):
        """
        Initialize the pipeline runner.

        Parameters
        ----------
        state : PipelineState
            Pipeline configuration and state
        progress_callback : callable, optional
            Callback function for progress updates: callback(message_type, data)
            message_type can be: "stage", "progress", "log", "error"
        """
        self.state = state
        self.progress_callback = progress_callback or (lambda t, d: None)
        self.cancelled = False

    def _log(self, message: str) -> None:
        """Send log message via callback."""
        self.progress_callback("log", message)

    def _update_stage(self, stage: str, status: str) -> None:
        """Update stage status via callback."""
        self.progress_callback("stage", (stage, status))

    def _run_command(self, cmd: list[str], stage_name: str) -> bool:
        """
        Execute a command and stream output with non-blocking I/O.

        Parameters
        ----------
        cmd : list of str
            Command and arguments
        stage_name : str
            Name of the current stage for logging

        Returns
        -------
        bool
            True if command succeeded, False otherwise
        """
        self._log(f"Running: {' '.join(cmd)}")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0,  # Unbuffered
            )

            last_heartbeat = time.time()
            heartbeat_interval = 30  # seconds

            while True:
                # Check if cancelled
                if self.cancelled:
                    process.terminate()
                    process.wait(timeout=5)
                    self._log("Pipeline cancelled by user")
                    return False

                # Non-blocking check for output using select
                ready, _, _ = select.select([process.stdout], [], [], 1.0)

                if ready:
                    line = process.stdout.readline()
                    if line:
                        line = line.rstrip()
                        if line:
                            self._log(line)
                        last_heartbeat = time.time()
                    elif process.poll() is not None:
                        # Process finished and no more output
                        break
                else:
                    # No output available, check if process is still running
                    if process.poll() is not None:
                        # Process finished
                        break

                    # Send heartbeat message periodically
                    current_time = time.time()
                    if current_time - last_heartbeat > heartbeat_interval:
                        elapsed = int(current_time - last_heartbeat)
                        self._log(
                            f"  [{stage_name}] Still processing... ({elapsed}s since last output)"
                        )
                        last_heartbeat = current_time

            # Read any remaining output
            remaining = process.stdout.read()
            if remaining:
                for line in remaining.strip().split("\n"):
                    if line:
                        self._log(line)

            if process.returncode != 0:
                self._log(f"ERROR: {stage_name} failed with exit code {process.returncode}")
                return False

            return True

        except FileNotFoundError:
            self._log(f"ERROR: Command not found: {cmd[0]}")
            self._log("Please ensure MRtrix3 is installed and in your PATH")
            return False
        except Exception as e:
            self._log(f"ERROR: Unexpected error in {stage_name}: {str(e)}")
            return False

    def run_denoising(self) -> bool:
        """
        Run dwidenoise for thermal noise removal.

        Returns
        -------
        bool
            True if successful
        """
        if not self.state.run_denoising:
            self._log("Denoising disabled, skipping...")
            return True

        self._update_stage("denoise", "running")
        self._log("Starting denoising with dwidenoise...")

        cmd = commands.build_dwidenoise_cmd(self.state)
        success = self._run_command(cmd, "dwidenoise")

        if success:
            self._log("Denoising completed successfully")
            self._update_stage("denoise", "complete")
        else:
            self._update_stage("denoise", "failed")

        return success

    def run_degibbs(self) -> bool:
        """
        Run mrdegibbs for Gibbs ringing removal.

        Returns
        -------
        bool
            True if successful
        """
        if not self.state.run_degibbs:
            self._log("Gibbs ringing removal disabled, skipping...")
            return True

        self._update_stage("degibbs", "running")
        self._log("Starting Gibbs ringing removal with mrdegibbs...")

        cmd = commands.build_mrdegibbs_cmd(self.state)
        success = self._run_command(cmd, "mrdegibbs")

        if success:
            self._log("Gibbs ringing removal completed successfully")
            self._update_stage("degibbs", "complete")
        else:
            self._update_stage("degibbs", "failed")

        return success

    def run_preprocessing(self) -> bool:
        """
        Run dwifslpreproc for DWI preprocessing.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("preproc", "running")
        self._log("Starting preprocessing with dwifslpreproc...")

        cmd = commands.build_dwifslpreproc_cmd(self.state)
        success = self._run_command(cmd, "dwifslpreproc")

        if success:
            self._log("Preprocessing completed successfully")
            self._update_stage("preproc", "complete")
        else:
            self._update_stage("preproc", "failed")

        return success

    def run_dti_fitting(self) -> bool:
        """
        Run DTI tensor fitting using dwi2tensor and tensor2metric.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("dti", "running")

        # Step 1: Fit tensor
        self._log("Fitting diffusion tensor with dwi2tensor...")
        cmd = commands.build_dwi2tensor_cmd(self.state)
        if not self._run_command(cmd, "dwi2tensor"):
            self._update_stage("dti", "failed")
            return False

        if self.cancelled:
            return False

        # Step 2: Extract FA and V1
        self._log("Extracting FA and V1 with tensor2metric...")
        cmd = commands.build_tensor2metric_cmd(self.state)
        if not self._run_command(cmd, "tensor2metric"):
            self._update_stage("dti", "failed")
            return False

        if self.cancelled:
            return False

        # Step 3: If ALPS-PAS or Both method, extract L2, L3, V2, V3
        if self.state.alps_method in ("ALPS-PAS", "Both"):
            self._log("Extracting L2, L3, V2, V3 for ALPS-PAS method...")
            alps_pas_cmds = commands.build_tensor2metric_alps_pas_cmds(self.state)
            for cmd in alps_pas_cmds:
                if self.cancelled:
                    return False
                if not self._run_command(cmd, "tensor2metric (ALPS-PAS)"):
                    self._update_stage("dti", "failed")
                    return False

        self._log("DTI fitting completed successfully")
        self._update_stage("dti", "complete")
        return True

    def run_registration(self) -> bool:
        """
        Run FA-to-template registration to create inverse warp.

        Uses the registration backend specified in state.registration_backend
        (defaults to 'fsl'). Creates the inverse warp needed for ROI placement.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("registration", "running")

        # Get registration backend
        backend_name = self.state.registration_backend
        self._log(f"Starting FA-to-template registration using {backend_name} backend...")

        try:
            backend = registration.get_backend(backend_name)
        except ValueError as e:
            self._log(f"ERROR: {e}")
            self._update_stage("registration", "failed")
            return False

        # Check if required tools are available
        available, missing = backend.check_available()
        if not available:
            self._log(f"ERROR: Missing {backend_name} tools: {', '.join(missing)}")
            if backend_name == "fsl":
                self._log("Please ensure FSL is installed and FSLDIR is set")
            self._update_stage("registration", "failed")
            return False

        # Run registration (creates inverse warp)
        result = backend.register(
            state=self.state,
            log_callback=self._log,
        )

        if result.success:
            self._update_stage("registration", "complete")
        else:
            self._log(f"ERROR: Registration failed: {result.error_message}")
            self._update_stage("registration", "failed")

        return result.success

    def run_roi_placement(self) -> bool:
        """
        Transform ROI templates to native space and create spherical ROIs.

        Requires that registration has been run first (inverse_warp_path must exist).

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("roi", "running")

        # Get registration backend
        backend_name = self.state.registration_backend
        self._log("Starting ROI placement...")

        try:
            backend = registration.get_backend(backend_name)
        except ValueError as e:
            self._log(f"ERROR: {e}")
            self._update_stage("roi", "failed")
            return False

        # Run ROI placement
        result = backend.place_rois(
            state=self.state,
            log_callback=self._log,
        )

        if result.success:
            # Update state with results
            self.state.roi_mask_paths = result.roi_mask_paths
            self.state.roi_centers = result.roi_centers
            self._update_stage("roi", "complete")
        else:
            self._log(f"ERROR: ROI placement failed: {result.error_message}")
            self._update_stage("roi", "failed")

        return result.success

    def run_alps_calculation(self) -> bool:
        """
        Calculate DTI-ALPS index from tensor and registered ROI masks.

        Supports three options:
        - ALPS-LAB: Uses tensor diagonal components (Dxx, Dyy, Dzz)
        - ALPS-PAS: Uses eigenvalues (L2, L3) sorted by eigenvector X-alignment
        - Both: Calculates both ALPS-LAB and ALPS-PAS

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("results", "running")

        try:
            results = run_alps_calculation(
                state=self.state,
                log_callback=self._log,
            )

            if results is None:
                self._update_stage("results", "failed")
                return False

            self.state.alps_results = results
            self._update_stage("results", "complete")
            return True

        except Exception as e:
            self._log(f"ERROR: ALPS calculation failed: {str(e)}")
            self._update_stage("results", "failed")
            return False

    def run_full_pipeline(self) -> bool:
        """
        Run the complete DTI-ALPS pipeline.

        Returns
        -------
        bool
            True if all stages completed successfully
        """
        self.cancelled = False

        # Ensure output directory exists
        os.makedirs(self.state.output_dir, exist_ok=True)

        # Set up output paths
        self.state.setup_output_paths()

        # Stage 1: Denoising (optional)
        if self.state.run_denoising:
            if not self.run_denoising():
                return False
            if self.cancelled:
                return False

        # Stage 2: Gibbs ringing removal (optional)
        if self.state.run_degibbs:
            if not self.run_degibbs():
                return False
            if self.cancelled:
                return False

        # Stage 3: Preprocessing
        if not self.run_preprocessing():
            return False
        if self.cancelled:
            return False

        # Stage 4: DTI fitting
        if not self.run_dti_fitting():
            return False
        if self.cancelled:
            return False

        # Stage 5: Registration (FA to JHU template)
        if not self.run_registration():
            return False
        if self.cancelled:
            return False

        # Stage 6: ROI Placement (transform templates to native space)
        if not self.run_roi_placement():
            return False
        if self.cancelled:
            return False

        # Stage 7: ALPS calculation
        if not self.run_alps_calculation():
            return False

        # Cleanup unwanted output files based on output_config
        self._cleanup_unwanted_outputs()

        self._log("Pipeline completed successfully!")
        return True

    def _cleanup_unwanted_outputs(self) -> None:
        """
        Remove output files that the user chose not to keep.

        Uses the output_config from state to determine which files to delete.
        """
        output_config = self.state.output_config
        files_to_delete: list[str] = []

        # Collect files to delete based on output_config settings
        # Preprocessing outputs
        if not output_config.denoised_dwi and self.state.denoised_dwi_path:
            files_to_delete.append(self.state.denoised_dwi_path)

        if not output_config.degibbs_dwi and self.state.degibbs_dwi_path:
            files_to_delete.append(self.state.degibbs_dwi_path)

        if not output_config.preprocessed_dwi and self.state.preprocessed_dwi_path:
            files_to_delete.append(self.state.preprocessed_dwi_path)

        if not output_config.preprocessed_bvecs:
            # Delete bvecs and bvals files
            bvecs_path = self.state.get_output_path("bvecs_preproc")
            bvals_path = self.state.get_output_path("bvals_preproc")
            files_to_delete.extend([bvecs_path, bvals_path])

        # DTI outputs
        if not output_config.tensor and self.state.tensor_path:
            files_to_delete.append(self.state.tensor_path)

        if not output_config.fa_map and self.state.fa_path:
            files_to_delete.append(self.state.fa_path)

        if not output_config.eigenvector_maps:
            # Delete V1, V2, V3, L1, L2, L3 eigenvector/eigenvalue maps
            if self.state.v1_path:
                files_to_delete.append(self.state.v1_path)
            if self.state.v2_path:
                files_to_delete.append(self.state.v2_path)
            if self.state.v3_path:
                files_to_delete.append(self.state.v3_path)
            if self.state.l1_path:
                files_to_delete.append(self.state.l1_path)
            if self.state.l2_path:
                files_to_delete.append(self.state.l2_path)
            if self.state.l3_path:
                files_to_delete.append(self.state.l3_path)

        # Registration outputs
        if not output_config.fa_brain and self.state.fa_brain_path:
            files_to_delete.append(self.state.fa_brain_path)

        if not output_config.affine_matrix and self.state.affine_mat_path:
            files_to_delete.append(self.state.affine_mat_path)

        if not output_config.warp_coefficients and self.state.warp_coef_path:
            files_to_delete.append(self.state.warp_coef_path)

        if not output_config.inverse_warp and self.state.inverse_warp_path:
            files_to_delete.append(self.state.inverse_warp_path)

        # ROI masks
        if not output_config.roi_masks and self.state.roi_mask_paths:
            for roi_path in self.state.roi_mask_paths.values():
                files_to_delete.append(roi_path)

        # Delete the files
        deleted_count = 0
        for file_path in files_to_delete:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except OSError as e:
                    self._log(f"Warning: Could not delete {file_path}: {e}")

        if deleted_count > 0:
            self._log(f"Cleaned up {deleted_count} unwanted output file(s)")

        # Try to remove empty registration directory if all registration files deleted
        reg_dir = os.path.join(self.state.output_dir, "registration")
        if os.path.exists(reg_dir):
            try:
                if not os.listdir(reg_dir):
                    os.rmdir(reg_dir)
            except OSError:
                pass  # Directory not empty or other error, ignore

    def cancel(self) -> None:
        """Request pipeline cancellation."""
        self.cancelled = True
