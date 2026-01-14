"""
Pipeline state management and execution for DTI-ALPS processing.
"""

import os
import subprocess
import threading
import queue
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Callable, Any
from pathlib import Path

from . import commands
from .. import config


@dataclass
class PipelineState:
    """
    Holds all input parameters and intermediate results for the DTI-ALPS pipeline.
    """
    # Stage 1: Input files
    dwi_path: Optional[str] = None
    bvecs_path: Optional[str] = None
    bvals_path: Optional[str] = None
    reverse_pe_path: Optional[str] = None
    json_sidecar_path: Optional[str] = None

    # Stage 2: Preprocessing parameters
    pe_direction: str = config.DEFAULT_PE_DIRECTION
    readout_time: float = config.DEFAULT_READOUT_TIME
    rpe_scheme: str = config.DEFAULT_RPE_SCHEME
    eddy_mask_path: Optional[str] = None
    eddy_slspec_path: Optional[str] = None
    eddy_options: str = ""
    topup_options: str = ""
    generate_qc: bool = False
    keep_intermediates: bool = False

    # Stage 3: DTI fitting parameters
    dti_mask_path: Optional[str] = None

    # Stage 4: ROI detection parameters
    fa_thresh: float = config.DEFAULT_FA_THRESH
    orient_thresh: float = config.DEFAULT_ORIENT_THRESH
    min_zone_width: int = config.DEFAULT_MIN_ZONE_WIDTH
    roi_radius_mm: float = config.DEFAULT_ROI_RADIUS_MM
    z_tolerance: int = config.DEFAULT_Z_TOLERANCE

    # Output settings
    output_dir: str = ""
    output_prefix: str = "subject"

    # Intermediate outputs (set during processing)
    preprocessed_dwi_path: Optional[str] = None
    tensor_path: Optional[str] = None
    fa_path: Optional[str] = None
    v1_path: Optional[str] = None

    # Results
    roi_centers: Optional[Dict[str, tuple]] = None
    alps_results: Optional[Dict[str, float]] = None

    def get_output_path(self, suffix: str) -> str:
        """Generate output file path with prefix and suffix."""
        return os.path.join(self.output_dir, f"{self.output_prefix}_{suffix}")

    def setup_output_paths(self) -> None:
        """Set up all intermediate output file paths."""
        self.preprocessed_dwi_path = self.get_output_path("dwi_preproc.nii.gz")
        self.tensor_path = self.get_output_path("tensor.nii.gz")
        self.fa_path = self.get_output_path("FA.nii.gz")
        self.v1_path = self.get_output_path("V1.nii.gz")


class PipelineRunner:
    """
    Orchestrates the execution of the DTI-ALPS processing pipeline.

    Stages:
    1. Preprocessing with dwifslpreproc
    2. DTI tensor fitting with dwi2tensor
    3. Metric extraction with tensor2metric (FA, V1)
    4. ROI detection with DTIALPSDetector
    5. ALPS index calculation
    """

    def __init__(self, state: PipelineState,
                 progress_callback: Optional[Callable[[str, Any], None]] = None):
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

    def _run_command(self, cmd: List[str], stage_name: str) -> bool:
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
        import select
        import time

        self._log(f"Running: {' '.join(cmd)}")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=0  # Unbuffered
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
                        self._log(f"  [{stage_name}] Still processing... ({elapsed}s since last output)")
                        last_heartbeat = current_time

            # Read any remaining output
            remaining = process.stdout.read()
            if remaining:
                for line in remaining.strip().split('\n'):
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

        self._log("DTI fitting completed successfully")
        self._update_stage("dti", "complete")
        return True

    def run_roi_detection(self) -> bool:
        """
        Run ROI detection using DTIALPSDetector.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("roi", "running")
        self._log("Starting automatic ROI detection...")

        try:
            # Import here to avoid circular imports
            import sys
            parent_dir = Path(__file__).parent.parent.parent
            if str(parent_dir) not in sys.path:
                sys.path.insert(0, str(parent_dir))
            from auto_dti_alps import DTIALPSDetector

            # Create detector with current parameters
            detector = DTIALPSDetector(
                fa_thresh=self.state.fa_thresh,
                orient_thresh=self.state.orient_thresh,
                min_zone_width=self.state.min_zone_width,
                roi_radius_mm=self.state.roi_radius_mm,
                z_tolerance=self.state.z_tolerance
            )

            # Load FA and V1 data
            self._log(f"Loading FA: {self.state.fa_path}")
            self._log(f"Loading V1: {self.state.v1_path}")
            detector.load_data(self.state.fa_path, self.state.v1_path)

            # Find candidates
            self._log("Searching for ROI candidates...")
            detector.find_candidates()

            # Select optimal ROIs
            self._log("Selecting optimal bilateral ROIs...")
            self.state.roi_centers = detector.select_optimal_rois()

            # Save ROI masks
            roi_dir = os.path.join(self.state.output_dir, "rois")
            os.makedirs(roi_dir, exist_ok=True)
            detector.save_roi_masks(roi_dir, self.state.output_prefix)

            self._log("ROI detection completed successfully")
            self._update_stage("roi", "complete")

            # Store detector for ALPS calculation
            self._detector = detector
            return True

        except Exception as e:
            self._log(f"ERROR: ROI detection failed: {str(e)}")
            self._update_stage("roi", "failed")
            return False

    def run_alps_calculation(self) -> bool:
        """
        Calculate DTI-ALPS index from tensor and ROIs.

        Returns
        -------
        bool
            True if successful
        """
        self._update_stage("results", "running")
        self._log("Calculating DTI-ALPS index...")

        try:
            import nibabel as nib
            import numpy as np

            # Load tensor image
            self._log(f"Loading tensor: {self.state.tensor_path}")
            tensor_img = nib.load(self.state.tensor_path)
            tensor_data = tensor_img.get_fdata()

            # Extract directional diffusivities
            # MRtrix dwi2tensor output format: D11, D22, D33, D12, D13, D23
            dxx = tensor_data[:, :, :, config.TENSOR_DXX_INDEX]
            dyy = tensor_data[:, :, :, config.TENSOR_DYY_INDEX]
            dzz = tensor_data[:, :, :, config.TENSOR_DZZ_INDEX]

            # Get ROI masks
            masks = self._detector.create_roi_masks()

            # Calculate mean diffusivities in each ROI
            results = {}

            for side in ['left', 'right']:
                proj_mask = masks[f'proj_{side}']
                assoc_mask = masks[f'assoc_{side}']

                proj_idx = np.where(proj_mask > 0)
                assoc_idx = np.where(assoc_mask > 0)

                # Projection ROI: Dxx (perivascular) and Dyy (perpendicular)
                results[f'Dxx_proj_{side}'] = np.mean(dxx[proj_idx])
                results[f'Dyy_proj_{side}'] = np.mean(dyy[proj_idx])

                # Association ROI: Dxx (perivascular) and Dzz (perpendicular)
                results[f'Dxx_assoc_{side}'] = np.mean(dxx[assoc_idx])
                results[f'Dzz_assoc_{side}'] = np.mean(dzz[assoc_idx])

            # Calculate ALPS index for each hemisphere
            for side in ['left', 'right']:
                dxx_proj = results[f'Dxx_proj_{side}']
                dxx_assoc = results[f'Dxx_assoc_{side}']
                dyy_proj = results[f'Dyy_proj_{side}']
                dzz_assoc = results[f'Dzz_assoc_{side}']

                numerator = (dxx_proj + dxx_assoc) / 2
                denominator = (dyy_proj + dzz_assoc) / 2

                if denominator > 0:
                    alps_index = numerator / denominator
                else:
                    alps_index = float('nan')

                results[f'ALPS_{side}'] = alps_index
                self._log(f"  {side.capitalize()} ALPS index: {alps_index:.4f}")

            # Calculate bilateral average
            alps_left = results['ALPS_left']
            alps_right = results['ALPS_right']
            if not (np.isnan(alps_left) or np.isnan(alps_right)):
                results['ALPS_bilateral'] = (alps_left + alps_right) / 2
                self._log(f"  Bilateral ALPS index: {results['ALPS_bilateral']:.4f}")

            self.state.alps_results = results
            self._log("ALPS calculation completed successfully")
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

        # Stage 1: Preprocessing
        if not self.run_preprocessing():
            return False
        if self.cancelled:
            return False

        # Stage 2: DTI fitting
        if not self.run_dti_fitting():
            return False
        if self.cancelled:
            return False

        # Stage 3: ROI detection
        if not self.run_roi_detection():
            return False
        if self.cancelled:
            return False

        # Stage 4: ALPS calculation
        if not self.run_alps_calculation():
            return False

        self._log("Pipeline completed successfully!")
        return True

    def cancel(self) -> None:
        """Request pipeline cancellation."""
        self.cancelled = True


class PipelineWorker(threading.Thread):
    """
    Background thread for running the processing pipeline.

    Communicates with GUI via queue for thread-safe updates.
    """

    def __init__(self, runner: PipelineRunner,
                 result_queue: queue.Queue,
                 cancel_event: threading.Event):
        """
        Initialize the worker thread.

        Parameters
        ----------
        runner : PipelineRunner
            Configured pipeline runner
        result_queue : queue.Queue
            Queue for sending results back to GUI
        cancel_event : threading.Event
            Event for signaling cancellation
        """
        super().__init__(daemon=True)
        self.runner = runner
        self.result_queue = result_queue
        self.cancel_event = cancel_event

    def run(self):
        """Execute the pipeline in background."""
        try:
            # Set up progress callback to send to queue
            def progress_callback(msg_type: str, data: Any):
                self.result_queue.put((msg_type, data))

            self.runner.progress_callback = progress_callback

            # Check for cancellation periodically
            def check_cancel():
                if self.cancel_event.is_set():
                    self.runner.cancel()

            # Run pipeline
            success = self.runner.run_full_pipeline()

            if success:
                self.result_queue.put(("complete", self.runner.state.alps_results))
            else:
                if self.cancel_event.is_set():
                    self.result_queue.put(("cancelled", None))
                else:
                    self.result_queue.put(("failed", None))

        except Exception as e:
            self.result_queue.put(("error", str(e)))
