"""
synB0-DISCO backend for susceptibility distortion correction.

This module orchestrates the synB0-DISCO pipeline which synthesizes
a distortion-free b0 image from T1 structural data and uses it with
FSL's topup to correct EPI distortions.

Pipeline steps:
1. T1 preparation (bias correction, normalization, skull stripping)
2. Extract mean b0 from DWI
3. Register b0 to T1 using epi_reg
4. Register T1 to MNI using ANTs
5. Apply transforms to warp b0 and T1 to atlas space
6. Run neural network inference to generate synthetic b0
7. Inverse transform synthetic b0 to native space
8. Prepare topup inputs (smoothed b0 + synthetic b0)
9. Run topup to estimate field
10. Run eddy for final correction
"""

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import commands
from .inference import run_inference

if TYPE_CHECKING:
    from ..state import PipelineState


@dataclass
class Synb0Result:
    """Result of synB0-DISCO processing."""

    success: bool
    synthetic_b0_path: str | None = None
    acqparams_path: str | None = None
    topup_prefix: str | None = None
    error_message: str | None = None


@dataclass
class TopupEddyResult:
    """Result of topup+eddy processing."""

    success: bool
    corrected_dwi_path: str | None = None
    corrected_bvecs_path: str | None = None
    error_message: str | None = None


def _find_tool(tool_name: str, package: str) -> str | None:
    """
    Find a tool by checking PATH and common installation locations.

    Parameters
    ----------
    tool_name : str
        Name of the tool to find
    package : str
        Package the tool belongs to (FreeSurfer, FSL, ANTs, c3d)

    Returns
    -------
    str | None
        Full path to the tool if found, None otherwise
    """
    import os

    # First check PATH
    path = shutil.which(tool_name)
    if path:
        return path

    # Check package-specific environment variables and common locations
    search_dirs: list[str] = []

    if package == "FreeSurfer":
        # Check FREESURFER_HOME
        fs_home = os.environ.get("FREESURFER_HOME")
        if fs_home:
            search_dirs.append(os.path.join(fs_home, "bin"))
        # Common installation paths
        search_dirs.extend(
            [
                os.path.expanduser("~/freesurfer/bin"),
                "/usr/local/freesurfer/bin",
                "/opt/freesurfer/bin",
            ]
        )
    elif package == "FSL":
        # Check FSLDIR
        fsl_dir = os.environ.get("FSLDIR")
        if fsl_dir:
            search_dirs.append(os.path.join(fsl_dir, "bin"))
        # Common installation paths
        search_dirs.extend(
            [
                "/usr/local/fsl/bin",
                "/opt/fsl/bin",
                "/usr/share/fsl/bin",
            ]
        )
    elif package == "ANTs":
        # Check ANTSPATH
        ants_path = os.environ.get("ANTSPATH")
        if ants_path:
            search_dirs.append(ants_path)
        # Common installation paths
        search_dirs.extend(
            [
                "/usr/local/ANTs/bin",
                "/opt/ANTs/bin",
                os.path.expanduser("~/ANTs/bin"),
            ]
        )
        # Check for versioned installations in ~/opts/
        opts_dir = os.path.expanduser("~/opts")
        if os.path.isdir(opts_dir):
            for entry in os.listdir(opts_dir):
                if entry.lower().startswith("ants"):
                    search_dirs.append(os.path.join(opts_dir, entry, "bin"))
                    # Also check for nested versioned directories
                    nested = os.path.join(opts_dir, entry)
                    if os.path.isdir(nested):
                        for subentry in os.listdir(nested):
                            search_dirs.append(os.path.join(nested, subentry, "bin"))
    elif package == "c3d/Convert3D":
        # Common installation paths
        search_dirs.extend(
            [
                "/usr/local/c3d/bin",
                "/opt/c3d/bin",
                os.path.expanduser("~/c3d/bin"),
                "/usr/local/bin",
            ]
        )
        # Check for versioned installations in ~/opts/
        opts_dir = os.path.expanduser("~/opts")
        if os.path.isdir(opts_dir):
            for entry in os.listdir(opts_dir):
                if entry.startswith("c3d"):
                    search_dirs.append(os.path.join(opts_dir, entry, "bin"))

    # Search in the directories
    for search_dir in search_dirs:
        tool_path = os.path.join(search_dir, tool_name)
        if os.path.isfile(tool_path) and os.access(tool_path, os.X_OK):
            return tool_path

    return None


def check_synb0_available() -> tuple[bool, list[str]]:
    """
    Check if all required tools for synB0-DISCO are available.

    Checks PATH and common installation locations for each tool.
    Also checks FREESURFER_HOME, FSLDIR, and ANTSPATH environment variables.

    Returns
    -------
    tuple[bool, list[str]]
        (all_available, list_of_missing_tools)
    """
    required_tools = {
        # FreeSurfer tools
        "mri_convert": "FreeSurfer",
        "mri_nu_correct.mni": "FreeSurfer",
        "mri_normalize": "FreeSurfer",
        # FSL tools
        "bet": "FSL",
        "epi_reg": "FSL",
        "topup": "FSL",
        "eddy": "FSL",
        "fslmaths": "FSL",
        "fslmerge": "FSL",
        # ANTs tools
        "antsRegistrationSyNQuick.sh": "ANTs",
        "antsApplyTransforms": "ANTs",
        # c3d
        "c3d_affine_tool": "c3d/Convert3D",
    }

    missing = []
    for tool, package in required_tools.items():
        if _find_tool(tool, package) is None:
            missing.append(f"{tool} ({package})")

    # Check for PyTorch
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch (PyTorch)")

    # Check for nibabel
    try:
        import nibabel  # noqa: F401
    except ImportError:
        missing.append("nibabel")

    return len(missing) == 0, missing


def get_tool_paths() -> dict[str, str]:
    """
    Get full paths for all synB0-DISCO tools.

    Returns
    -------
    dict[str, str]
        Dictionary mapping tool names to their full paths
    """
    required_tools = {
        "mri_convert": "FreeSurfer",
        "mri_nu_correct.mni": "FreeSurfer",
        "mri_normalize": "FreeSurfer",
        "bet": "FSL",
        "epi_reg": "FSL",
        "topup": "FSL",
        "eddy": "FSL",
        "fslmaths": "FSL",
        "fslmerge": "FSL",
        "antsRegistrationSyNQuick.sh": "ANTs",
        "antsApplyTransforms": "ANTs",
        "c3d_affine_tool": "c3d/Convert3D",
    }

    paths = {}
    for tool, package in required_tools.items():
        path = _find_tool(tool, package)
        if path:
            paths[tool] = path
    return paths


def get_synb0_env() -> dict[str, str]:
    """
    Get environment with updated PATH for synB0-DISCO tools.

    Returns
    -------
    dict[str, str]
        Environment dictionary with updated PATH
    """
    import os

    env = os.environ.copy()
    extra_paths = set()

    # Get paths from tool locations
    tool_paths = get_tool_paths()
    for full_path in tool_paths.values():
        bin_dir = os.path.dirname(full_path)
        extra_paths.add(bin_dir)

    # Also check common environment variables
    fs_home = os.environ.get("FREESURFER_HOME")
    if fs_home:
        extra_paths.add(os.path.join(fs_home, "bin"))

    fsl_dir = os.environ.get("FSLDIR")
    if fsl_dir:
        extra_paths.add(os.path.join(fsl_dir, "bin"))

    ants_path = os.environ.get("ANTSPATH")
    if ants_path:
        extra_paths.add(ants_path)

    # Update PATH
    if extra_paths:
        current_path = env.get("PATH", "")
        new_paths = ":".join(extra_paths)
        env["PATH"] = f"{new_paths}:{current_path}"

    return env


class Synb0Backend:
    """
    synB0-DISCO backend for distortion correction.

    This backend provides an alternative to dwifslpreproc by using
    T1-based synthetic b0 generation for susceptibility correction.
    """

    def __init__(self):
        """Initialize the synB0-DISCO backend."""
        self.name = "synb0"

    def check_available(self) -> tuple[bool, list[str]]:
        """
        Check if synB0-DISCO tools are available.

        Returns
        -------
        tuple[bool, list[str]]
            (all_available, list_of_missing_tools)
        """
        return check_synb0_available()

    def run(
        self,
        state: "PipelineState",
        log: Callable[[str], None] | None = None,
    ) -> Synb0Result:
        """
        Run the synB0-DISCO pipeline to generate synthetic b0.

        Parameters
        ----------
        state : PipelineState
            Pipeline state containing paths and parameters
        log : Callable[[str], None] | None
            Optional logging callback

        Returns
        -------
        Synb0Result
            Result containing paths to outputs or error message
        """
        if log is None:
            log = lambda x: None  # noqa: E731

        log("Starting synB0-DISCO processing...")

        # Check availability
        available, missing = self.check_available()
        if not available:
            return Synb0Result(
                success=False,
                error_message=f"Missing required tools: {', '.join(missing)}",
            )

        # Create synb0 working directory
        synb0_dir = Path(state.output_dir) / "synb0_work"
        synb0_dir.mkdir(parents=True, exist_ok=True)

        # Get device for inference
        device = getattr(state, "synb0_device", "auto")

        try:
            # Step 1: T1 preparation
            log("Step 1: Preparing T1 image...")
            t1_result = self._prepare_t1(state, synb0_dir, log)
            if not t1_result["success"]:
                return Synb0Result(success=False, error_message=t1_result["error"])

            t1_norm_path = t1_result["t1_norm_path"]
            t1_brain_path = t1_result["t1_brain_path"]

            # Step 2: Extract mean b0
            log("Step 2: Extracting mean b0 from DWI...")
            b0_result = self._extract_b0(state, synb0_dir, log)
            if not b0_result["success"]:
                return Synb0Result(success=False, error_message=b0_result["error"])

            b0_mean_path = b0_result["b0_mean_path"]

            # Step 3: Register b0 to T1
            log("Step 3: Registering b0 to T1...")
            b0_to_t1_result = self._register_b0_to_t1(
                b0_mean_path, t1_norm_path, t1_brain_path, synb0_dir, log
            )
            if not b0_to_t1_result["success"]:
                return Synb0Result(success=False, error_message=b0_to_t1_result["error"])

            _b0_to_t1_mat = b0_to_t1_result["fsl_mat_path"]  # noqa: F841
            b0_to_t1_ants = b0_to_t1_result["ants_mat_path"]

            # Step 4: Register T1 to MNI
            log("Step 4: Registering T1 to MNI atlas...")
            t1_to_mni_result = self._register_t1_to_mni(t1_norm_path, synb0_dir, log)
            if not t1_to_mni_result["success"]:
                return Synb0Result(success=False, error_message=t1_to_mni_result["error"])

            t1_to_mni_affine = t1_to_mni_result["affine_path"]

            # Step 5: Apply transforms to atlas space
            log("Step 5: Transforming images to atlas space...")
            atlas_result = self._transform_to_atlas(
                b0_mean_path, t1_norm_path, b0_to_t1_ants, t1_to_mni_affine, synb0_dir, log
            )
            if not atlas_result["success"]:
                return Synb0Result(success=False, error_message=atlas_result["error"])

            b0_atlas_path = atlas_result["b0_atlas_path"]
            t1_atlas_path = atlas_result["t1_atlas_path"]

            # Step 6: Neural network inference
            log("Step 6: Running neural network inference...")
            synb0_atlas_path = str(synb0_dir / "synb0_atlas.nii.gz")
            success, msg = run_inference(
                t1_atlas_path=t1_atlas_path,
                b0_atlas_path=b0_atlas_path,
                output_path=synb0_atlas_path,
                device=device,
                log=log,
            )
            if not success:
                return Synb0Result(success=False, error_message=msg)

            # Step 7: Inverse transform to native space
            log("Step 7: Transforming synthetic b0 to native space...")
            native_result = self._transform_to_native(
                synb0_atlas_path, b0_mean_path, b0_to_t1_ants, t1_to_mni_affine, synb0_dir, log
            )
            if not native_result["success"]:
                return Synb0Result(success=False, error_message=native_result["error"])

            synb0_native_path = native_result["synb0_native_path"]

            # Step 8: Prepare topup inputs
            log("Step 8: Preparing topup inputs...")
            topup_prep_result = self._prepare_topup_inputs(
                b0_mean_path, synb0_native_path, state, synb0_dir, log
            )
            if not topup_prep_result["success"]:
                return Synb0Result(success=False, error_message=topup_prep_result["error"])

            log("synB0-DISCO processing complete!")

            return Synb0Result(
                success=True,
                synthetic_b0_path=synb0_native_path,
                acqparams_path=topup_prep_result["acqparams_path"],
                topup_prefix=None,  # Will be set after topup runs
            )

        except Exception as e:
            return Synb0Result(success=False, error_message=f"Unexpected error: {e}")

    def _prepare_t1(
        self,
        state: "PipelineState",
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Prepare T1 image with bias correction and normalization."""
        t1_path = state.t1_path
        if not t1_path or not Path(t1_path).exists():
            return {"success": False, "error": f"T1 image not found: {t1_path}"}

        t1_stripped = getattr(state, "t1_stripped", False)

        # Convert to mgz format
        t1_mgz = str(work_dir / "T1.mgz")
        cmd = commands.build_mri_convert_cmd(t1_path, t1_mgz)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"mri_convert failed: {result.stderr}"}

        # Bias field correction
        t1_nu_mgz = str(work_dir / "T1_nu.mgz")
        cmd = commands.build_mri_nu_correct_cmd(t1_mgz, t1_nu_mgz)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"mri_nu_correct.mni failed: {result.stderr}"}

        # Intensity normalization
        t1_norm_mgz = str(work_dir / "T1_norm.mgz")
        cmd = commands.build_mri_normalize_cmd(t1_nu_mgz, t1_norm_mgz)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"mri_normalize failed: {result.stderr}"}

        # Convert back to NIfTI
        t1_norm_nii = str(work_dir / "T1_norm.nii.gz")
        cmd = commands.build_mri_convert_cmd(t1_norm_mgz, t1_norm_nii)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"mri_convert failed: {result.stderr}"}

        # Brain extraction (if not already stripped)
        t1_brain_nii = str(work_dir / "T1_brain.nii.gz")
        if t1_stripped:
            log("  T1 already skull-stripped, copying...")
            shutil.copy(t1_norm_nii, t1_brain_nii)
        else:
            log("  Running brain extraction with BET...")
            cmd = commands.build_bet_cmd(t1_norm_nii, t1_brain_nii, frac=0.4, robust=True)
            log(f"  Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return {"success": False, "error": f"bet failed: {result.stderr}"}

        return {
            "success": True,
            "t1_norm_path": t1_norm_nii,
            "t1_brain_path": t1_brain_nii,
        }

    def _extract_b0(
        self,
        state: "PipelineState",
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Extract mean b0 from DWI data."""
        from ..b0_extraction import extract_and_average_b0

        # Determine input DWI (use degibbs output if available, else denoised, else original)
        if state.degibbs_dwi_path and Path(state.degibbs_dwi_path).exists():
            dwi_path = state.degibbs_dwi_path
        elif state.denoised_dwi_path and Path(state.denoised_dwi_path).exists():
            dwi_path = state.denoised_dwi_path
        else:
            dwi_path = state.dwi_path

        b0_mean_path = str(work_dir / "b0_mean.nii.gz")

        result = extract_and_average_b0(
            dwi_path=dwi_path,
            bvecs_path=state.bvecs_path,
            bvals_path=state.bvals_path,
            output_path=b0_mean_path,
            log=log,
        )

        if not result.success:
            return {"success": False, "error": result.error_message}

        return {"success": True, "b0_mean_path": b0_mean_path}

    def _register_b0_to_t1(
        self,
        b0_path: str,
        t1_path: str,
        t1_brain_path: str,
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Register b0 to T1 using epi_reg and convert to ANTs format."""
        output_prefix = str(work_dir / "b0_to_T1")

        # Run epi_reg
        cmd = commands.build_epi_reg_cmd(b0_path, t1_path, t1_brain_path, output_prefix)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"epi_reg failed: {result.stderr}"}

        fsl_mat_path = f"{output_prefix}.mat"
        if not Path(fsl_mat_path).exists():
            return {"success": False, "error": f"epi_reg did not create matrix: {fsl_mat_path}"}

        # Convert FSL matrix to ANTs/ITK format
        ants_mat_path = str(work_dir / "b0_to_T1_ANTS.txt")
        cmd = commands.build_c3d_affine_tool_cmd(
            reference_path=t1_path,
            source_path=b0_path,
            input_matrix_path=fsl_mat_path,
            output_matrix_path=ants_mat_path,
        )
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"c3d_affine_tool failed: {result.stderr}"}

        return {
            "success": True,
            "fsl_mat_path": fsl_mat_path,
            "ants_mat_path": ants_mat_path,
        }

    def _register_t1_to_mni(
        self,
        t1_path: str,
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Register T1 to MNI atlas using ANTs."""
        mni_template = str(commands.get_mni_template_path())
        output_prefix = str(work_dir / "T1_to_MNI_")

        cmd = commands.build_ants_registration_cmd(
            fixed_path=mni_template,
            moving_path=t1_path,
            output_prefix=output_prefix,
            transform_type="a",  # Affine only for speed
        )
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"ANTs registration failed: {result.stderr}"}

        affine_path = f"{output_prefix}0GenericAffine.mat"
        if not Path(affine_path).exists():
            return {"success": False, "error": f"ANTs did not create affine: {affine_path}"}

        return {
            "success": True,
            "affine_path": affine_path,
        }

    def _transform_to_atlas(
        self,
        b0_path: str,
        t1_path: str,
        b0_to_t1_mat: str,
        t1_to_mni_mat: str,
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Transform b0 and T1 to atlas space (2.5mm)."""
        mni_2mm = str(commands.get_mni_template_2mm_path())

        # Transform b0 to atlas space
        b0_atlas_path = str(work_dir / "b0_atlas.nii.gz")
        cmd = commands.build_ants_apply_transforms_cmd(
            input_path=b0_path,
            reference_path=mni_2mm,
            output_path=b0_atlas_path,
            transforms=[t1_to_mni_mat, b0_to_t1_mat],
            interpolation="BSpline",
        )
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"antsApplyTransforms (b0) failed: {result.stderr}"}

        # Transform T1 to atlas space
        t1_atlas_path = str(work_dir / "T1_atlas.nii.gz")
        cmd = commands.build_ants_apply_transforms_cmd(
            input_path=t1_path,
            reference_path=mni_2mm,
            output_path=t1_atlas_path,
            transforms=[t1_to_mni_mat],
            interpolation="BSpline",
        )
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"antsApplyTransforms (T1) failed: {result.stderr}"}

        return {
            "success": True,
            "b0_atlas_path": b0_atlas_path,
            "t1_atlas_path": t1_atlas_path,
        }

    def _transform_to_native(
        self,
        synb0_atlas_path: str,
        b0_reference_path: str,
        b0_to_t1_mat: str,
        t1_to_mni_mat: str,
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Transform synthetic b0 from atlas space back to native space."""
        synb0_native_path = str(work_dir / "synb0_native.nii.gz")

        # Apply inverse transforms
        cmd = commands.build_ants_apply_transforms_cmd(
            input_path=synb0_atlas_path,
            reference_path=b0_reference_path,
            output_path=synb0_native_path,
            transforms=[b0_to_t1_mat, t1_to_mni_mat],
            interpolation="BSpline",
            invert_flags=[True, True],  # Invert both transforms
        )
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"antsApplyTransforms failed: {result.stderr}"}

        return {
            "success": True,
            "synb0_native_path": synb0_native_path,
        }

    def _prepare_topup_inputs(
        self,
        b0_path: str,
        synb0_path: str,
        state: "PipelineState",
        work_dir: Path,
        log: Callable[[str], None],
    ) -> dict[str, Any]:
        """Prepare inputs for FSL topup."""
        # Smooth the original b0
        b0_smooth_path = str(work_dir / "b0_smooth.nii.gz")
        cmd = commands.build_fslmaths_smooth_cmd(b0_path, b0_smooth_path, sigma=1.15)
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"fslmaths smooth failed: {result.stderr}"}

        # Merge b0 volumes for topup
        b0_pair_path = str(work_dir / "b0_pair.nii.gz")
        cmd = commands.build_fslmerge_cmd(b0_pair_path, [b0_smooth_path, synb0_path])
        log(f"  Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {"success": False, "error": f"fslmerge failed: {result.stderr}"}

        # Create acquisition parameters file
        acqparams_path = str(work_dir / "acqparams.txt")
        readout_time = state.readout_time if state.readout_time else 0.05

        # Map PE direction to topup format
        pe_dir = state.pe_direction if hasattr(state, "pe_direction") else "AP"
        pe_vectors = {
            "AP": "0 -1 0",
            "PA": "0 1 0",
            "LR": "-1 0 0",
            "RL": "1 0 0",
            "SI": "0 0 -1",
            "IS": "0 0 1",
        }
        pe_vector = pe_vectors.get(pe_dir, "0 -1 0")

        # Write acqparams: distorted b0 with real readout, synthetic with 0
        with open(acqparams_path, "w") as f:
            f.write(f"{pe_vector} {readout_time:.6f}\n")  # Distorted b0
            f.write(f"{pe_vector} 0.000000\n")  # Synthetic b0 (distortion-free)

        log(f"  Created acquisition parameters: {acqparams_path}")

        # Store paths for topup/eddy
        state.synb0_b0_pair_path = b0_pair_path
        state.synb0_acqparams_path = acqparams_path

        return {
            "success": True,
            "b0_pair_path": b0_pair_path,
            "acqparams_path": acqparams_path,
        }


def run_topup_eddy(
    state: "PipelineState",
    log: Callable[[str], None] | None = None,
) -> TopupEddyResult:
    """
    Run FSL topup and eddy for distortion correction.

    This should be called after synB0-DISCO has prepared the synthetic b0
    and acquisition parameters.

    Parameters
    ----------
    state : PipelineState
        Pipeline state with synB0 outputs
    log : Callable[[str], None] | None
        Optional logging callback

    Returns
    -------
    TopupEddyResult
        Result containing corrected DWI path or error
    """
    if log is None:
        log = lambda x: None  # noqa: E731

    log("Running topup + eddy distortion correction...")

    # Get paths from state
    synb0_dir = Path(state.output_dir) / "synb0_work"
    b0_pair_path = getattr(state, "synb0_b0_pair_path", str(synb0_dir / "b0_pair.nii.gz"))
    acqparams_path = getattr(state, "synb0_acqparams_path", str(synb0_dir / "acqparams.txt"))

    if not Path(b0_pair_path).exists():
        return TopupEddyResult(success=False, error_message=f"b0 pair not found: {b0_pair_path}")
    if not Path(acqparams_path).exists():
        return TopupEddyResult(
            success=False, error_message=f"acqparams not found: {acqparams_path}"
        )

    # Step 1: Run topup
    log("Step 1: Running topup...")
    topup_prefix = str(synb0_dir / "topup")
    topup_options = getattr(state, "topup_options", {})

    cmd = commands.build_topup_cmd(
        imain_path=b0_pair_path,
        datain_path=acqparams_path,
        output_prefix=topup_prefix,
        extra_options=topup_options,
    )
    log(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return TopupEddyResult(success=False, error_message=f"topup failed: {result.stderr}")

    # Step 2: Create brain mask for eddy
    log("Step 2: Creating brain mask...")
    from ..b0_extraction import create_brain_mask_from_dwi

    # Determine input DWI
    if state.degibbs_dwi_path and Path(state.degibbs_dwi_path).exists():
        dwi_path = state.degibbs_dwi_path
    elif state.denoised_dwi_path and Path(state.denoised_dwi_path).exists():
        dwi_path = state.denoised_dwi_path
    else:
        dwi_path = state.dwi_path

    mask_path = str(synb0_dir / "brain_mask.nii.gz")
    success, msg = create_brain_mask_from_dwi(
        dwi_path=dwi_path,
        bvecs_path=state.bvecs_path,
        bvals_path=state.bvals_path,
        output_mask_path=mask_path,
        log=log,
    )
    if not success:
        return TopupEddyResult(success=False, error_message=msg)

    # Step 3: Create index file for eddy
    log("Step 3: Creating index file...")
    index_path = str(synb0_dir / "index.txt")

    # Count number of volumes in DWI
    import nibabel as nib

    dwi_nii = nib.load(dwi_path)
    n_volumes = dwi_nii.shape[3] if len(dwi_nii.shape) > 3 else 1

    with open(index_path, "w") as f:
        f.write(" ".join(["1"] * n_volumes))

    # Step 4: Run eddy
    log("Step 4: Running eddy...")
    eddy_output = str(synb0_dir / "dwi_eddy")
    eddy_options = getattr(state, "eddy_options", {})

    # Add default recommended options if not specified
    if "repol" not in eddy_options:
        eddy_options["repol"] = True

    cmd = commands.build_eddy_cmd(
        imain_path=dwi_path,
        mask_path=mask_path,
        acqp_path=acqparams_path,
        index_path=index_path,
        bvecs_path=state.bvecs_path,
        bvals_path=state.bvals_path,
        topup_prefix=topup_prefix,
        output_path=eddy_output,
        extra_options=eddy_options,
    )
    log(f"  Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return TopupEddyResult(success=False, error_message=f"eddy failed: {result.stderr}")

    # Check outputs
    corrected_dwi = f"{eddy_output}.nii.gz"
    corrected_bvecs = f"{eddy_output}.eddy_rotated_bvecs"

    if not Path(corrected_dwi).exists():
        return TopupEddyResult(
            success=False, error_message=f"eddy did not create output: {corrected_dwi}"
        )

    # Copy to expected output location
    final_dwi_path = state.preprocessed_dwi_path
    final_bvecs_path = state.get_output_path("bvecs_preproc")

    log(f"  Copying corrected DWI to: {final_dwi_path}")
    shutil.copy(corrected_dwi, final_dwi_path)

    if Path(corrected_bvecs).exists():
        log(f"  Copying rotated bvecs to: {final_bvecs_path}")
        shutil.copy(corrected_bvecs, final_bvecs_path)
        # Also copy bvals (unchanged)
        shutil.copy(state.bvals_path, state.get_output_path("bvals_preproc"))

    log("topup + eddy completed successfully!")

    return TopupEddyResult(
        success=True,
        corrected_dwi_path=final_dwi_path,
        corrected_bvecs_path=final_bvecs_path,
    )
