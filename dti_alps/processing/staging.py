"""
Data staging for cross-filesystem performance.

When input data lives on a slow cross-filesystem mount (e.g., /mnt/ in WSL2,
/media/sf_ in VirtualBox), this module copies input files to fast local storage,
runs the pipeline locally, then copies results back.
"""

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field

from .state import PipelineState


@dataclass
class StagingContext:
    """Tracks staging state for a single pipeline run."""

    staging_root: str = ""
    input_dir: str = ""
    output_dir: str = ""
    original_output_dir: str = ""
    original_input_paths: dict[str, str] = field(default_factory=dict)
    copy_back_failed: bool = False


class StagingManager:
    """Manages copying input files to local storage and results back."""

    def __init__(self, log_callback: Callable[[str], None] | None = None):
        self._log = log_callback or (lambda msg: None)

    def stage_in(self, state: PipelineState) -> StagingContext:
        """
        Copy input files to fast local storage and redirect state paths.

        Parameters
        ----------
        state : PipelineState
            Pipeline state whose input paths will be redirected.

        Returns
        -------
        StagingContext
            Context object for stage_out and cleanup.
        """
        ctx = StagingContext()

        # Create staging directory
        ctx.staging_root = tempfile.mkdtemp(prefix="dti_alps_staging_", dir=state.staging_dir)
        ctx.input_dir = os.path.join(ctx.staging_root, "input")
        ctx.output_dir = os.path.join(ctx.staging_root, "output")
        os.makedirs(ctx.input_dir)
        os.makedirs(ctx.output_dir)

        self._log(f"[Staging] Copying input files to local storage: {ctx.staging_root}")

        # Map of state attribute names to copy
        input_fields = [
            "dwi_path",
            "bvecs_path",
            "bvals_path",
            "reverse_pe_path",
            "json_sidecar_path",
        ]

        for field_name in input_fields:
            original = getattr(state, field_name, None)
            if original and os.path.isfile(original):
                ctx.original_input_paths[field_name] = original
                dest = os.path.join(ctx.input_dir, os.path.basename(original))
                self._log(f"[Staging]   {os.path.basename(original)}")
                shutil.copy2(original, dest)
                setattr(state, field_name, dest)

        # Redirect output directory
        ctx.original_output_dir = state.output_dir
        state.output_dir = ctx.output_dir

        self._log("[Staging] Input staging complete")
        return ctx

    def stage_out(self, state: PipelineState, ctx: StagingContext) -> None:
        """
        Copy results from staging output back to the original output directory.

        Parameters
        ----------
        state : PipelineState
            Pipeline state (output_dir will be restored).
        ctx : StagingContext
            Context from stage_in.
        """
        self._log(f"[Staging] Copying results back to: {ctx.original_output_dir}")
        try:
            os.makedirs(ctx.original_output_dir, exist_ok=True)
            shutil.copytree(ctx.output_dir, ctx.original_output_dir, dirs_exist_ok=True)
            self._log("[Staging] Results copied successfully")
        except Exception as e:
            ctx.copy_back_failed = True
            self._log(
                f"[Staging] WARNING: Failed to copy results back: {e}\n"
                f"[Staging] Results preserved at: {ctx.output_dir}"
            )

        # Restore original output dir so downstream code sees the real path
        state.output_dir = ctx.original_output_dir

    def cleanup(self, ctx: StagingContext) -> None:
        """
        Remove the staging directory.

        Skips cleanup if copy-back failed so the user can retrieve results.

        Parameters
        ----------
        ctx : StagingContext
            Context from stage_in.
        """
        if ctx.copy_back_failed:
            self._log(
                f"[Staging] Preserving staging directory due to copy-back failure: "
                f"{ctx.staging_root}"
            )
            return

        try:
            shutil.rmtree(ctx.staging_root)
            self._log("[Staging] Staging directory cleaned up")
        except Exception as e:
            self._log(f"[Staging] WARNING: Could not remove staging directory: {e}")
