"""
FSL-based registration for automatic ROI placement in DTI-ALPS pipeline.

This module registers subject FA images to the JHU-ICBM-FA-1mm template
and transforms pre-defined ROI masks from template space to subject native space.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import PipelineState


def get_fsldir() -> str | None:
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


def get_fsl_bin_dir() -> Path | None:
    """
    Get path to FSL bin directory.

    Returns
    -------
    Path or None
        Path to FSL bin directory, or None if not found
    """
    fsldir = get_fsldir()
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


def check_fsl_registration_available() -> tuple[bool, list[str]]:
    """
    Check if FSL registration tools are available.

    Returns
    -------
    tuple of (bool, list)
        (all_available, list of missing commands)
    """
    required_commands = ["bet2", "flirt", "fnirt", "invwarp", "applywarp"]
    missing = []

    fsl_bin = get_fsl_bin_dir()

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


def get_jhu_template_path() -> Path | None:
    """
    Get path to JHU-ICBM-FA-1mm.nii.gz template.

    Returns
    -------
    Path or None
        Path to template, or None if not found
    """
    fsldir = get_fsldir()
    if not fsldir:
        return None

    template_path = Path(fsldir) / "data" / "atlases" / "JHU" / "JHU-ICBM-FA-1mm.nii.gz"
    if template_path.exists():
        return template_path

    return None


def get_roi_template_paths() -> dict[str, Path] | None:
    """
    Get paths to pre-defined ROI templates shipped with the package.

    Returns
    -------
    dict or None
        Dictionary mapping ROI names to template paths, or None if any missing.
        Keys: 'left_proj', 'left_assoc', 'right_proj', 'right_assoc'
    """
    # Look relative to this module
    module_dir = Path(__file__).parent.parent.parent
    templates_dir = module_dir / "templates"

    roi_templates = {
        "left_proj": templates_dir / "JHU-labels-left_proj.nii.gz",
        "left_assoc": templates_dir / "JHU-labels-left_assoc.nii.gz",
        "right_proj": templates_dir / "JHU-labels-right_proj.nii.gz",
        "right_assoc": templates_dir / "JHU-labels-right_assoc.nii.gz",
    }

    # Check all templates exist
    for path in roi_templates.values():
        if not path.exists():
            return None

    return roi_templates


def fix_nan_in_nifti(input_path: str, output_path: str) -> bool:
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
        import nibabel as nib
        import numpy as np

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


def run_fsl_command(
    cmd: list[str],
    log_callback: callable = None,
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


def register_fa_to_template(
    state: "PipelineState",
    log_callback: callable = None,
) -> bool:
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
    bool
        True if registration succeeded
    """
    log = log_callback or (lambda x: None)

    # Get FSL paths
    fsl_bin = get_fsl_bin_dir()
    if not fsl_bin:
        log("ERROR: FSL bin directory not found")
        return False

    jhu_template = get_jhu_template_path()
    if not jhu_template:
        log("ERROR: JHU-ICBM-FA-1mm.nii.gz template not found")
        return False

    roi_templates = get_roi_template_paths()
    if not roi_templates:
        log("ERROR: ROI template files not found in templates/ directory")
        return False

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
    if not fix_nan_in_nifti(state.fa_path, str(fa_nonan)):
        log("ERROR: Failed to prepare FA image")
        return False

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
    if not run_fsl_command(bet_cmd, log):
        log("ERROR: BET2 skull stripping failed")
        return False

    if not fa_brain.exists():
        log("ERROR: Skull-stripped FA not created")
        return False

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
    ]
    log(f"  Command: {' '.join(flirt_cmd)}")
    if not run_fsl_command(flirt_cmd, log):
        log("ERROR: FLIRT failed")
        return False

    if not affine_mat.exists():
        log("ERROR: Affine matrix not created")
        return False

    # Step 4: Non-linear registration (FNIRT)
    log("Running non-linear registration (FNIRT)...")
    fnirt_cmd = [
        str(fsl_bin / "fnirt"),
        f"--in={fa_brain}",
        f"--ref={jhu_template}",
        f"--aff={affine_mat}",
        f"--cout={warp_coef}",
    ]
    log(f"  Command: {' '.join(fnirt_cmd)}")
    if not run_fsl_command(fnirt_cmd, log):
        log("ERROR: FNIRT failed")
        return False

    if not warp_coef.exists():
        log("ERROR: Warp coefficients not created")
        return False

    # Step 5: Create inverse warp
    log("Creating inverse warp (INVWARP)...")
    invwarp_cmd = [
        str(fsl_bin / "invwarp"),
        f"--ref={state.fa_path}",
        f"--warp={warp_coef}",
        f"--out={inverse_warp}",
    ]
    log(f"  Command: {' '.join(invwarp_cmd)}")
    if not run_fsl_command(invwarp_cmd, log):
        log("ERROR: INVWARP failed")
        return False

    if not inverse_warp.exists():
        log("ERROR: Inverse warp not created")
        return False

    # Step 6: Apply inverse warp to all ROI masks
    log("Transforming ROI masks to native space...")
    roi_native_paths = {}

    for roi_name, roi_template in roi_templates.items():
        roi_native = roi_dir / f"{prefix}_{roi_name}.nii.gz"
        log(f"  Transforming {roi_name}...")

        applywarp_cmd = [
            str(fsl_bin / "applywarp"),
            f"--ref={state.fa_path}",
            f"--in={roi_template}",
            f"--warp={inverse_warp}",
            f"--out={roi_native}",
            "--interp=nn",
        ]
        if not run_fsl_command(applywarp_cmd, log):
            log(f"ERROR: Failed to transform {roi_name}")
            return False

        if not roi_native.exists():
            log(f"ERROR: {roi_name} ROI not created")
            return False

        roi_native_paths[roi_name] = str(roi_native)

    # Store ROI paths in state
    state.roi_mask_paths = roi_native_paths

    log("Registration and ROI transformation completed successfully")
    log(f"  Left projection ROI: {roi_native_paths['left_proj']}")
    log(f"  Left association ROI: {roi_native_paths['left_assoc']}")
    log(f"  Right projection ROI: {roi_native_paths['right_proj']}")
    log(f"  Right association ROI: {roi_native_paths['right_assoc']}")
    return True
