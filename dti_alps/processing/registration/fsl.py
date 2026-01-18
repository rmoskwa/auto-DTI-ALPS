"""
FSL-based registration backend for DTI-ALPS pipeline.

This module implements the RegistrationBackend interface using FSL tools
(FLIRT, FNIRT, invwarp, applywarp) for FA-to-template registration.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import nibabel as nib
import numpy as np

from .base import (
    RegistrationBackend,
    RegistrationResult,
    calculate_roi_quality,
    create_sphere_mask,
    find_mask_centroid,
    get_roi_template_paths,
    refine_roi_placement,
)

if TYPE_CHECKING:
    from ..state import PipelineState


class FSLRegistration(RegistrationBackend):
    """
    FSL-based registration backend.

    Uses FLIRT for linear registration and FNIRT for nonlinear registration
    to transform ROI templates from JHU space to subject native space.
    """

    @property
    def name(self) -> str:
        """Return the backend name."""
        return "fsl"

    def check_available(self) -> tuple[bool, list[str]]:
        """
        Check if FSL registration tools are available.

        Returns
        -------
        tuple of (bool, list)
            (all_available, list of missing commands)
        """
        required_commands = ["bet2", "flirt", "fnirt", "invwarp", "applywarp"]
        missing = []

        fsl_bin = self._get_fsl_bin_dir()

        for cmd in required_commands:
            found = False
            # Check in FSL bin directory
            if fsl_bin and (fsl_bin / cmd).is_file():
                found = True
            # Also check PATH
            elif shutil.which(cmd) is not None:
                found = True

            if not found:
                missing.append(cmd)

        return (len(missing) == 0, missing)

    def get_template_path(self) -> Path | None:
        """
        Get path to JHU-ICBM-FA-1mm.nii.gz template.

        Returns
        -------
        Path or None
            Path to template, or None if not found
        """
        fsldir = self._get_fsldir()
        if not fsldir:
            return None

        template_path = Path(fsldir) / "data" / "atlases" / "JHU" / "JHU-ICBM-FA-1mm.nii.gz"
        if template_path.exists():
            return template_path

        return None

    def register(
        self,
        state: "PipelineState",
        log_callback: Callable[[str], None] | None = None,
    ) -> RegistrationResult:
        """
        Register subject FA to JHU template and transform ROI masks to native space.

        Steps:
        1. Fix NaN values in FA image
        2. Skull stripping with BET2
        3. Linear registration (FLIRT)
        4. Non-linear registration (FNIRT)
        5. Create inverse warp (INVWARP)
        6. Apply inverse warp to all 4 ROI masks (APPLYWARP)

        Parameters
        ----------
        state : PipelineState
            Pipeline state with fa_path set
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        RegistrationResult
            Result containing ROI paths and centroids
        """
        log = log_callback or (lambda x: None)

        # Get FSL paths
        fsl_bin = self._get_fsl_bin_dir()
        if not fsl_bin:
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="FSL bin directory not found",
            )

        jhu_template = self.get_template_path()
        if not jhu_template:
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="JHU-ICBM-FA-1mm.nii.gz template not found",
            )

        roi_templates = get_roi_template_paths()
        if not roi_templates:
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="ROI template files not found in templates/ directory",
            )

        # Set up output paths
        reg_dir = Path(state.output_dir) / "registration"
        reg_dir.mkdir(parents=True, exist_ok=True)

        # Also create rois directory for final ROI masks
        roi_dir = Path(state.output_dir) / "rois"
        roi_dir.mkdir(parents=True, exist_ok=True)

        prefix = state.output_prefix
        fa_nonan = reg_dir / f"{prefix}_FA_nonan.nii.gz"
        fa_brain = reg_dir / f"{prefix}_FA_brain.nii.gz"
        affine_mat = reg_dir / f"{prefix}_subject2jhu_affine.mat"
        warp_coef = reg_dir / f"{prefix}_subject2jhu_warp_coef.nii.gz"
        inverse_warp = reg_dir / f"{prefix}_jhu2subject_warp_coef.nii.gz"

        # Step 1: Fix NaN values (bet2 may fail on NaN)
        log("Preparing FA image (fixing NaN values if present)...")
        if not self._fix_nan_in_nifti(state.fa_path, str(fa_nonan)):
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Failed to prepare FA image",
            )

        # Step 2: Skull stripping with bet2
        log("Running skull stripping (BET2)...")
        bet_cmd = [
            str(fsl_bin / "bet2"),
            str(fa_nonan),
            str(fa_brain),
            "-f",
            "0.3",  # Fractional intensity threshold (lower = larger brain)
        ]
        log(f"  Command: {' '.join(bet_cmd)}")
        if not self._run_fsl_command(bet_cmd, log):
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="BET2 skull stripping failed",
            )

        if not fa_brain.exists():
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Skull-stripped FA not created",
            )

        # Step 3: Linear registration (FLIRT)
        log("Running linear registration (FLIRT)...")
        flirt_cmd = [
            str(fsl_bin / "flirt"),
            "-in",
            str(fa_brain),
            "-ref",
            str(jhu_template),
            "-omat",
            str(affine_mat),
            "-dof",
            "12",
            "-cost",
            "corratio",  # Correlation ratio - good for same-modality
            "-searchrx",
            "-30",
            "30",  # Reduced search range for stability
            "-searchry",
            "-30",
            "30",
            "-searchrz",
            "-30",
            "30",
        ]
        log(f"  Command: {' '.join(flirt_cmd)}")
        if not self._run_fsl_command(flirt_cmd, log):
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="FLIRT failed",
            )

        if not affine_mat.exists():
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Affine matrix not created",
            )

        # Step 4: Non-linear registration (FNIRT)
        log("Running non-linear registration (FNIRT)...")
        fnirt_cmd = [
            str(fsl_bin / "fnirt"),
            f"--in={fa_nonan}",  # Use non-skull-stripped FA
            f"--ref={jhu_template}",
            f"--aff={affine_mat}",
            f"--cout={warp_coef}",
            "--intmod=none",  # No intensity modulation for quantitative FA
            "--jacrange=0.2,5",  # Constrain Jacobian to prevent extreme warps
            "--lambda=300,150,100,50",  # Stronger regularization
            "--subsamp=4,2,2,1",  # Balanced: keep subsamp=2 longer for speed
            "--miter=5,5,3,3",  # Balanced: fewer iterations at full resolution
            "--warpres=10,10,10",  # 10mm warp resolution (FSL recommended)
        ]
        log(f"  Command: {' '.join(fnirt_cmd)}")
        if not self._run_fsl_command(fnirt_cmd, log):
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="FNIRT failed",
            )

        if not warp_coef.exists():
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Warp coefficients not created",
            )

        # Step 5: Create inverse warp
        log("Creating inverse warp (INVWARP)...")
        invwarp_cmd = [
            str(fsl_bin / "invwarp"),
            f"--ref={state.fa_path}",
            f"--warp={warp_coef}",
            f"--out={inverse_warp}",
        ]
        log(f"  Command: {' '.join(invwarp_cmd)}")
        if not self._run_fsl_command(invwarp_cmd, log):
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="INVWARP failed",
            )

        if not inverse_warp.exists():
            return RegistrationResult(
                success=False,
                roi_mask_paths={},
                roi_centers={},
                error_message="Inverse warp not created",
            )

        # Step 6: Apply inverse warp to all ROI masks and find centroids
        log("Transforming ROI masks to native space...")

        result = self._transform_rois_to_native(
            state=state,
            fsl_bin=fsl_bin,
            inverse_warp=inverse_warp,
            roi_templates=roi_templates,
            reg_dir=reg_dir,
            roi_dir=roi_dir,
            log=log,
        )

        return result

    def _transform_rois_to_native(
        self,
        state: "PipelineState",
        fsl_bin: Path,
        inverse_warp: Path,
        roi_templates: dict[str, Path],
        reg_dir: Path,
        roi_dir: Path,
        log: Callable[[str], None],
    ) -> RegistrationResult:
        """Transform ROI templates to native space and create spherical ROIs."""
        # Load reference image for shape and voxel size
        ref_img = nib.load(state.fa_path)
        ref_shape = ref_img.shape[:3]
        fa_data = ref_img.get_fdata()
        voxel_size = ref_img.header.get_zooms()[:3]

        prefix = state.output_prefix

        # Get sphere radius from state (default 3.0mm)
        sphere_radius = getattr(state, "roi_sphere_radius", 3.0)
        log(f"  Sphere radius: {sphere_radius} mm")

        # Check if ROI refinement is enabled
        do_refinement = getattr(state, "refine_roi_placement", True)
        v1_data = None
        if do_refinement:
            log("  ROI refinement enabled (±2 X/Y, ±1 Z voxels)")
            # Load V1 data for fiber orientation analysis
            if state.v1_path and os.path.exists(state.v1_path):
                v1_img = nib.load(state.v1_path)
                v1_data = v1_img.get_fdata()
            else:
                log("  WARNING: V1 data not available, skipping refinement")
                do_refinement = False

        roi_centroids = {}
        roi_native_paths = {}

        for roi_name, roi_template in roi_templates.items():
            # First transform to get approximate location
            roi_transformed = reg_dir / f"{prefix}_{roi_name}_transformed.nii.gz"
            log(f"  Transforming {roi_name}...")

            applywarp_cmd = [
                str(fsl_bin / "applywarp"),
                f"--ref={state.fa_path}",
                f"--in={roi_template}",
                f"--warp={inverse_warp}",
                f"--out={roi_transformed}",
                "--interp=nn",
            ]
            if not self._run_fsl_command(applywarp_cmd, log):
                return RegistrationResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"Failed to transform {roi_name}",
                )

            if not roi_transformed.exists():
                return RegistrationResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"{roi_name} transformed ROI not created",
                )

            # Load transformed mask and find centroid
            transformed_img = nib.load(str(roi_transformed))
            transformed_data = transformed_img.get_fdata()

            centroid = find_mask_centroid(transformed_data)
            if centroid is None:
                return RegistrationResult(
                    success=False,
                    roi_mask_paths={},
                    roi_centers={},
                    error_message=f"No voxels found in transformed {roi_name}",
                )

            log(f"    Template centroid: {centroid}")

            # Determine fiber type from ROI name
            fiber_type = "proj" if "proj" in roi_name else "assoc"

            # Apply refinement if enabled
            if do_refinement and v1_data is not None:
                # Calculate original purity
                orig_sphere = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
                orig_purity, _, _, _ = calculate_roi_quality(
                    v1_data, fa_data, orig_sphere, fiber_type
                )

                # Refine placement
                refined_centroid, refined_purity, _ = refine_roi_placement(
                    centroid,
                    v1_data,
                    fa_data,
                    ref_shape,
                    voxel_size,
                    fiber_type,
                    radius_mm=sphere_radius,
                    search_xy=2,
                    search_z=1,
                )

                # Calculate offset
                offset = (
                    refined_centroid[0] - centroid[0],
                    refined_centroid[1] - centroid[1],
                    refined_centroid[2] - centroid[2],
                )

                if offset != (0, 0, 0):
                    log(f"    Refined centroid: {refined_centroid} (offset: {offset})")
                    log(f"    Purity: {orig_purity * 100:.0f}% -> {refined_purity * 100:.0f}%")
                else:
                    log(f"    No refinement needed (purity: {refined_purity * 100:.0f}%)")

                centroid = refined_centroid

            roi_centroids[roi_name] = centroid

            # Create spherical ROI at (possibly refined) centroid
            sphere_mask = create_sphere_mask(ref_shape, centroid, sphere_radius, voxel_size)
            n_voxels = int(np.sum(sphere_mask))
            log(f"    Created sphere with {n_voxels} voxels")

            # Save spherical ROI
            roi_sphere = roi_dir / f"{prefix}_{roi_name}.nii.gz"
            sphere_img = nib.Nifti1Image(
                sphere_mask.astype(np.float32), ref_img.affine, ref_img.header
            )
            nib.save(sphere_img, str(roi_sphere))

            roi_native_paths[roi_name] = str(roi_sphere)

        log("Registration and ROI creation completed successfully")
        log(f"  Left projection ROI: {roi_native_paths['left_proj']}")
        log(f"  Left association ROI: {roi_native_paths['left_assoc']}")
        log(f"  Right projection ROI: {roi_native_paths['right_proj']}")
        log(f"  Right association ROI: {roi_native_paths['right_assoc']}")

        return RegistrationResult(
            success=True,
            roi_mask_paths=roi_native_paths,
            roi_centers=roi_centroids,
        )

    # =========================================================================
    # Private helper methods
    # =========================================================================

    def _get_fsldir(self) -> str | None:
        """
        Get FSLDIR from environment or common installation paths.

        Returns
        -------
        str or None
            Path to FSL directory, or None if not found
        """
        fsldir = os.environ.get("FSLDIR")
        if fsldir and os.path.isdir(fsldir):
            return fsldir

        # Try common locations
        common_paths = [
            "/usr/local/fsl",
            "/usr/share/fsl/6.0",
            "/opt/fsl",
            os.path.expanduser("~/fsl"),
        ]
        for path in common_paths:
            if os.path.isdir(path):
                # Verify it has bin directory with flirt
                bin_dir = os.path.join(path, "bin")
                if not os.path.isdir(bin_dir):
                    bin_dir = os.path.join(path, "share", "fsl", "bin")
                if os.path.isfile(os.path.join(bin_dir, "flirt")):
                    return path

        return None

    def _get_fsl_bin_dir(self) -> Path | None:
        """
        Get path to FSL bin directory.

        Returns
        -------
        Path or None
            Path to FSL bin directory, or None if not found
        """
        fsldir = self._get_fsldir()
        if not fsldir:
            return None

        bin_dir = Path(fsldir) / "bin"
        if bin_dir.exists():
            return bin_dir

        # Some installations have bin under share/fsl/
        alt_bin = Path(fsldir) / "share" / "fsl" / "bin"
        if alt_bin.exists():
            return alt_bin

        return None

    def _fix_nan_in_nifti(self, input_path: str, output_path: str) -> bool:
        """
        Replace NaN values with 0 in a NIfTI image.

        FSL tools fail on images with NaN values, so this is necessary
        for FA maps where voxels outside the brain may be NaN.

        Parameters
        ----------
        input_path : str
            Path to input NIfTI file
        output_path : str
            Path for output NIfTI file

        Returns
        -------
        bool
            True if successful
        """
        try:
            img = nib.load(input_path)
            data = img.get_fdata()

            nan_count = np.sum(np.isnan(data))
            if nan_count > 0:
                data = np.nan_to_num(data, nan=0.0)
                new_img = nib.Nifti1Image(data.astype(np.float32), img.affine, img.header)
                new_img.header.set_data_dtype(np.float32)
                nib.save(new_img, output_path)
            else:
                # No NaN, just copy
                shutil.copy(input_path, output_path)

            return True

        except Exception:
            return False

    def _run_fsl_command(
        self,
        cmd: list[str],
        log_callback: Callable[[str], None] | None = None,
    ) -> bool:
        """
        Execute an FSL command and capture output.

        Parameters
        ----------
        cmd : list of str
            Command and arguments
        log_callback : callable, optional
            Function to call with log messages

        Returns
        -------
        bool
            True if command succeeded
        """
        log = log_callback or (lambda x: None)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            for line in iter(process.stdout.readline, ""):
                line = line.rstrip()
                if line:
                    log(line)

            process.wait()
            return process.returncode == 0

        except FileNotFoundError:
            log(f"ERROR: Command not found: {cmd[0]}")
            return False
        except Exception as e:
            log(f"ERROR: {e}")
            return False


# =============================================================================
# Backward compatibility functions
# =============================================================================


def get_fsldir() -> str | None:
    """Get FSLDIR from environment or common installation paths."""
    return FSLRegistration()._get_fsldir()


def get_fsl_bin_dir() -> Path | None:
    """Get path to FSL bin directory."""
    return FSLRegistration()._get_fsl_bin_dir()


def check_fsl_registration_available() -> tuple[bool, list[str]]:
    """Check if FSL registration tools are available."""
    return FSLRegistration().check_available()


def get_jhu_template_path() -> Path | None:
    """Get path to JHU-ICBM-FA-1mm.nii.gz template."""
    return FSLRegistration().get_template_path()
