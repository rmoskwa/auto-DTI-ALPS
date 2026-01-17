"""
Pipeline state management and execution for DTI-ALPS processing.
"""

import os
import queue
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..gui import config
from . import commands, registration

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
    roi_sphere_radius: float = 2.0
    # FA threshold for filtering CSF voxels from ROIs
    fa_threshold: float = config.FA_THRESHOLD

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
    alps_left: float | None = None
    alps_right: float | None = None
    alps_bilateral: float | None = None
    error_message: str | None = None
    processing_time: float = 0.0

    # Detailed diffusivity values (optional, populated on success)
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


class PipelineRunner:
    """
    Orchestrates the execution of the DTI-ALPS processing pipeline.

    Stages:
    1. Preprocessing with dwifslpreproc
    2. DTI tensor fitting with dwi2tensor + tensor2metric (FA, V1)
    3. Registration - FA to JHU template, ROI masks to native space
    4. ALPS index calculation
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
        import select
        import time

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

            # Verify ROI masks are available
            if not self.state.roi_mask_paths:
                self._log("ERROR: ROI masks not available. Run registration first.")
                self._update_stage("results", "failed")
                return False

            # Load tensor image
            self._log(f"Loading tensor: {self.state.tensor_path}")
            tensor_img = nib.load(self.state.tensor_path)
            tensor_data = tensor_img.get_fdata()

            # Extract directional diffusivities
            # MRtrix dwi2tensor output format: D11, D22, D33, D12, D13, D23
            dxx = tensor_data[:, :, :, config.TENSOR_DXX_INDEX]
            dyy = tensor_data[:, :, :, config.TENSOR_DYY_INDEX]
            dzz = tensor_data[:, :, :, config.TENSOR_DZZ_INDEX]

            # Load FA map for thresholding (to filter out CSF voxels)
            self._log(f"Loading FA map: {self.state.fa_path}")
            fa_img = nib.load(self.state.fa_path)
            fa_data = fa_img.get_fdata()
            self._log(f"  Applying FA threshold > {self.state.fa_threshold} to filter CSF voxels")

            # Load registered ROI masks
            self._log("Loading registered ROI masks...")
            masks = {}
            for roi_name, roi_path in self.state.roi_mask_paths.items():
                self._log(f"  Loading {roi_name}: {roi_path}")
                roi_img = nib.load(roi_path)
                masks[roi_name] = roi_img.get_fdata()

            # Calculate mean diffusivities in each ROI
            results = {}

            for side in ["left", "right"]:
                proj_mask = masks[f"{side}_proj"]
                assoc_mask = masks[f"{side}_assoc"]

                # Get voxel indices with FA threshold applied
                proj_idx_raw = np.where(proj_mask > 0)
                assoc_idx_raw = np.where(assoc_mask > 0)

                # Apply FA > threshold filter to exclude CSF voxels
                proj_fa_mask = fa_data[proj_idx_raw] > self.state.fa_threshold
                assoc_fa_mask = fa_data[assoc_idx_raw] > self.state.fa_threshold

                proj_idx = tuple(arr[proj_fa_mask] for arr in proj_idx_raw)
                assoc_idx = tuple(arr[assoc_fa_mask] for arr in assoc_idx_raw)

                # Log ROI sizes before and after FA filtering
                proj_voxels_raw = len(proj_idx_raw[0])
                assoc_voxels_raw = len(assoc_idx_raw[0])
                proj_voxels = len(proj_idx[0])
                assoc_voxels = len(assoc_idx[0])
                self._log(
                    f"  {side.capitalize()} projection ROI: "
                    f"{proj_voxels}/{proj_voxels_raw} voxels (after FA filter)"
                )
                self._log(
                    f"  {side.capitalize()} association ROI: "
                    f"{assoc_voxels}/{assoc_voxels_raw} voxels (after FA filter)"
                )

                # Warn if too many voxels were filtered out
                if proj_voxels == 0:
                    self._log(f"  WARNING: No voxels in {side} projection ROI after FA filtering!")
                if assoc_voxels == 0:
                    self._log(f"  WARNING: No voxels in {side} association ROI after FA filtering!")

                # Projection ROI: Dxx (perivascular) and Dyy (perpendicular)
                results[f"Dxx_proj_{side}"] = np.mean(dxx[proj_idx])
                results[f"Dyy_proj_{side}"] = np.mean(dyy[proj_idx])

                # Association ROI: Dxx (perivascular) and Dzz (perpendicular)
                results[f"Dxx_assoc_{side}"] = np.mean(dxx[assoc_idx])
                results[f"Dzz_assoc_{side}"] = np.mean(dzz[assoc_idx])

            # Calculate ALPS index for each hemisphere
            for side in ["left", "right"]:
                dxx_proj = results[f"Dxx_proj_{side}"]
                dxx_assoc = results[f"Dxx_assoc_{side}"]
                dyy_proj = results[f"Dyy_proj_{side}"]
                dzz_assoc = results[f"Dzz_assoc_{side}"]

                numerator = (dxx_proj + dxx_assoc) / 2
                denominator = (dyy_proj + dzz_assoc) / 2

                if denominator > 0:
                    alps_index = numerator / denominator
                else:
                    alps_index = float("nan")

                results[f"ALPS_{side}"] = alps_index
                self._log(f"  {side.capitalize()} ALPS index: {alps_index:.4f}")

            # Calculate bilateral average
            alps_left = results["ALPS_left"]
            alps_right = results["ALPS_right"]
            if not (np.isnan(alps_left) or np.isnan(alps_right)):
                results["ALPS_bilateral"] = (alps_left + alps_right) / 2
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

        # Stage 3: Registration (FA to JHU template, ROIs to native space)
        if not self.run_registration():
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

    def __init__(
        self, runner: PipelineRunner, result_queue: queue.Queue, cancel_event: threading.Event
    ):
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

        total = self.batch_state.total_subjects
        self._notify("batch_start", total)
        self._notify("log", f"Starting batch processing for {total} subjects")

        for i, subject_files in enumerate(self.batch_state.subjects):
            if self.cancelled:
                self._mark_remaining_skipped(i)
                break

            self.batch_state.current_subject_index = i
            result = self._process_single_subject(subject_files, i)
            self.batch_state.results.append(result)

            self._notify("subject_complete", (i, result))

        # Write CSV output
        self._write_csv_results()

        self._notify("batch_complete", self.batch_state)
        return self.batch_state.success_count == self.batch_state.total_subjects

    def _process_single_subject(self, subject_files: "SubjectFiles", index: int) -> SubjectResult:
        """
        Process a single subject with error handling.

        Parameters
        ----------
        subject_files : SubjectFiles
            Files for this subject
        index : int
            Index in subject list

        Returns
        -------
        SubjectResult
            Result object with status and values
        """
        import time

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
            runner = PipelineRunner(state, progress_callback=subject_progress)

            # Link cancellation
            if self.cancelled:
                runner.cancelled = True

            success = runner.run_full_pipeline()

            if success and state.alps_results:
                result.status = "completed"
                result.alps_left = state.alps_results.get("ALPS_left")
                result.alps_right = state.alps_results.get("ALPS_right")
                result.alps_bilateral = state.alps_results.get("ALPS_bilateral")

                # Store detailed diffusivity values
                result.dxx_proj_left = state.alps_results.get("Dxx_proj_left")
                result.dxx_proj_right = state.alps_results.get("Dxx_proj_right")
                result.dyy_proj_left = state.alps_results.get("Dyy_proj_left")
                result.dyy_proj_right = state.alps_results.get("Dyy_proj_right")
                result.dxx_assoc_left = state.alps_results.get("Dxx_assoc_left")
                result.dxx_assoc_right = state.alps_results.get("Dxx_assoc_right")
                result.dzz_assoc_left = state.alps_results.get("Dzz_assoc_left")
                result.dzz_assoc_right = state.alps_results.get("Dzz_assoc_right")

                self._notify(
                    "log",
                    f"  ALPS Index: L={result.alps_left:.4f}, R={result.alps_right:.4f}, "
                    f"Bi={result.alps_bilateral:.4f}",
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
        """Write batch results to CSV file."""
        import csv

        csv_path = os.path.join(self.batch_state.config.output_dir, "alps_results.csv")

        try:
            os.makedirs(self.batch_state.config.output_dir, exist_ok=True)

            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "Filename",
                        "Left Hemisphere ALPS",
                        "Right Hemisphere ALPS",
                        "Combined ALPS",
                        "Status",
                        "Error",
                    ]
                )

                for result in self.batch_state.results:
                    writer.writerow(
                        [
                            result.subject_id,
                            f"{result.alps_left:.6f}" if result.alps_left is not None else "",
                            f"{result.alps_right:.6f}" if result.alps_right is not None else "",
                            f"{result.alps_bilateral:.6f}"
                            if result.alps_bilateral is not None
                            else "",
                            result.status,
                            result.error_message or "",
                        ]
                    )

            self._notify("log", f"Results saved to {csv_path}")

        except OSError as e:
            self._notify("log", f"ERROR: Failed to write CSV: {e}")

    def cancel(self) -> None:
        """Request batch cancellation."""
        self.cancelled = True


class BatchWorker(threading.Thread):
    """
    Background thread for running batch processing.

    Communicates with GUI via queue for thread-safe updates.
    """

    def __init__(
        self,
        batch_runner: BatchRunner,
        result_queue: queue.Queue,
        cancel_event: threading.Event,
    ):
        """
        Initialize the batch worker thread.

        Parameters
        ----------
        batch_runner : BatchRunner
            Configured batch runner
        result_queue : queue.Queue
            Queue for sending results back to GUI
        cancel_event : threading.Event
            Event for signaling cancellation
        """
        super().__init__(daemon=True)
        self.batch_runner = batch_runner
        self.result_queue = result_queue
        self.cancel_event = cancel_event

    def run(self):
        """Execute batch processing in background."""
        try:
            # Set up progress callback to send to queue
            def progress_callback(msg_type: str, data: Any):
                self.result_queue.put((msg_type, data))

                # Check cancellation after each message
                if self.cancel_event.is_set():
                    self.batch_runner.cancelled = True

            self.batch_runner.progress_callback = progress_callback

            # Run batch
            success = self.batch_runner.run_batch()

            if self.cancel_event.is_set():
                self.result_queue.put(("batch_cancelled", None))
            elif success:
                self.result_queue.put(("batch_success", self.batch_runner.batch_state))
            else:
                self.result_queue.put(("batch_partial", self.batch_runner.batch_state))

        except Exception as e:
            self.result_queue.put(("error", str(e)))
