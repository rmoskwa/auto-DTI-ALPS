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
from .state import BatchConfig, BatchState, PipelineState, SubjectResult
from .workers import BatchWorker, PipelineWorker

__all__ = [
    "PipelineState",
    "BatchConfig",
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
    5. Registration - FA to JHU template, ROI masks to native space
    6. ALPS index calculation
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
        Run FA-to-template registration to transform ROI masks to native space.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("registration", "running")
        self._log("Starting FA-to-template registration and ROI transformation...")

        # Check FSL registration tools
        fsl_ok, missing = registration.check_fsl_registration_available()
        if not fsl_ok:
            self._log(f"ERROR: Missing FSL tools: {', '.join(missing)}")
            self._log("Please ensure FSL is installed and FSLDIR is set")
            self._update_stage("registration", "failed")
            return False

        # Run registration
        success = registration.register_fa_to_template(
            state=self.state,
            log_callback=self._log,
        )

        if success:
            self._update_stage("registration", "complete")
        else:
            self._update_stage("registration", "failed")

        return success

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

        # Stage 5: Registration (FA to JHU template, ROIs to native space)
        if not self.run_registration():
            return False
        if self.cancelled:
            return False

        # Stage 6: ALPS calculation
        if not self.run_alps_calculation():
            return False

        self._log("Pipeline completed successfully!")
        return True

    def cancel(self) -> None:
        """Request pipeline cancellation."""
        self.cancelled = True
