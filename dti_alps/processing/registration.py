"""
FSL-based registration for ROI region limiting in DTI-ALPS pipeline.

This module registers subject FA images to the JHU-ICBM-FA-1mm template
and transforms SCR/SLF label masks to subject native space.
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
    required_commands = ["flirt", "fnirt", "invwarp", "applywarp"]
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


def get_labels_template_path() -> Path | None:
    """
    Get path to SCR/SLF labels template shipped with the package.

    Returns
    -------
    Path or None
        Path to labels template, or None if not found
    """
    # Look relative to this module
    module_dir = Path(__file__).parent.parent.parent
    template_path = module_dir / "templates" / "JHU-labels-SCR-SLF.nii.gz"
    if template_path.exists():
        return template_path

    return None


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
    Register subject FA to JHU template and transform labels to native space.

    Steps:
    1. Fix NaN values in FA image
    2. Linear registration (FLIRT)
    3. Non-linear registration (FNIRT)
    4. Create inverse warp (INVWARP)
    5. Apply inverse warp to labels (APPLYWARP)

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

    labels_template = get_labels_template_path()
    if not labels_template:
        log("ERROR: JHU-labels-SCR-SLF.nii.gz template not found")
        return False

    # Set up output paths
    reg_dir = Path(state.output_dir) / "registration"
    reg_dir.mkdir(parents=True, exist_ok=True)

    prefix = state.output_prefix
    fa_nonan = reg_dir / f"{prefix}_FA_nonan.nii.gz"
    affine_mat = reg_dir / f"{prefix}_subject2jhu_affine.mat"
    warp_coef = reg_dir / f"{prefix}_subject2jhu_warp_coef.nii.gz"
    inverse_warp = reg_dir / f"{prefix}_jhu2subject_warp_coef.nii.gz"
    labels_native = reg_dir / f"{prefix}_SCR_SLF_labels_native.nii.gz"

    # Step 1: Fix NaN values
    log("Preparing FA image (fixing NaN values if present)...")
    if not fix_nan_in_nifti(state.fa_path, str(fa_nonan)):
        log("ERROR: Failed to prepare FA image")
        return False

    # Step 2: Linear registration (FLIRT)
    log("Running linear registration (FLIRT)...")
    flirt_cmd = [
        str(fsl_bin / "flirt"),
        "-in",
        str(fa_nonan),
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

    # Step 3: Non-linear registration (FNIRT)
    log("Running non-linear registration (FNIRT)...")
    fnirt_cmd = [
        str(fsl_bin / "fnirt"),
        f"--in={fa_nonan}",
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

    # Step 4: Create inverse warp
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

    # Step 5: Apply inverse warp to labels
    log("Applying inverse warp to labels (APPLYWARP)...")
    applywarp_cmd = [
        str(fsl_bin / "applywarp"),
        f"--ref={state.fa_path}",
        f"--in={labels_template}",
        f"--warp={inverse_warp}",
        f"--out={labels_native}",
        "--interp=nn",
    ]
    log(f"  Command: {' '.join(applywarp_cmd)}")
    if not run_fsl_command(applywarp_cmd, log):
        log("ERROR: APPLYWARP failed")
        return False

    if not labels_native.exists():
        log("ERROR: Labels in native space not created")
        return False

    # Store result path in state
    state.labels_native_path = str(labels_native)

    log("Registration completed successfully")
    return True
