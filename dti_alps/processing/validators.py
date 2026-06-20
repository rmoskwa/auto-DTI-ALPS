"""
Input validation for DTI-ALPS pipeline.
"""

import os

import numpy as np

from ..gui import config


def validate_file_exists(path: str, file_type: str) -> tuple[bool, str]:
    """
    Check if a file exists.

    Parameters
    ----------
    path : str
        File path to check
    file_type : str
        Description of file type for error message

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message or success_info)
    """
    if not path:
        return False, f"{file_type} file is required"
    if not os.path.isfile(path):
        return False, f"{file_type} file not found: {path}"
    return True, f"{file_type} found"


def validate_nifti(path: str) -> tuple[bool, str]:
    """
    Validate a NIfTI file and return shape information.

    Parameters
    ----------
    path : str
        Path to NIfTI file

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message or shape_info)
    """
    try:
        import nibabel as nib

        img = nib.load(path)
        shape = img.shape
        zooms = img.header.get_zooms()

        if len(shape) == 3:
            return (
                True,
                f"3D: {shape[0]}x{shape[1]}x{shape[2]}, voxel: {zooms[0]:.2f}x{zooms[1]:.2f}x{zooms[2]:.2f}mm",
            )
        elif len(shape) == 4:
            return (
                True,
                f"4D: {shape[0]}x{shape[1]}x{shape[2]}x{shape[3]} volumes, voxel: {zooms[0]:.2f}x{zooms[1]:.2f}x{zooms[2]:.2f}mm",
            )
        else:
            return True, f"Shape: {shape}"

    except Exception as e:
        return False, f"Invalid NIfTI file: {str(e)}"


def validate_gradients(bvecs_path: str, bvals_path: str, dwi_path: str = None) -> tuple[bool, str]:
    """
    Validate gradient table files.

    Parameters
    ----------
    bvecs_path : str
        Path to bvecs file
    bvals_path : str
        Path to bvals file
    dwi_path : str, optional
        Path to DWI file for volume count validation

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message or info)
    """
    try:
        # Load bvals
        bvals = np.loadtxt(bvals_path)
        if bvals.ndim > 1:
            bvals = bvals.flatten()
        n_bvals = len(bvals)

        # Load bvecs
        bvecs = np.loadtxt(bvecs_path)

        # Determine orientation (3xN or Nx3)
        if bvecs.shape[0] == 3:
            n_bvecs = bvecs.shape[1]
        elif bvecs.shape[1] == 3:
            n_bvecs = bvecs.shape[0]
        else:
            return False, f"Invalid bvecs shape: {bvecs.shape} (expected 3xN or Nx3)"

        # Check consistency
        if n_bvals != n_bvecs:
            return False, f"Mismatch: {n_bvals} b-values but {n_bvecs} gradient directions"

        # Check against DWI volume count if provided
        if dwi_path:
            import nibabel as nib

            dwi = nib.load(dwi_path)
            if len(dwi.shape) > 3:
                n_volumes = dwi.shape[3]
                if n_bvals != n_volumes:
                    return False, f"Mismatch: {n_bvals} gradients but DWI has {n_volumes} volumes"

        # Count b=0 volumes
        n_b0 = np.sum(bvals < 50)
        n_dwi = n_bvals - n_b0

        return True, f"{n_bvals} directions ({n_b0} b=0, {n_dwi} DWI)"

    except Exception as e:
        return False, f"Error reading gradient files: {str(e)}"


def validate_readout_time(value: str) -> tuple[bool, str]:
    """
    Validate readout time value.

    Parameters
    ----------
    value : str
        Readout time as string

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message)
    """
    try:
        rt = float(value)
        min_rt, max_rt = config.READOUT_TIME_RANGE

        if rt <= 0:
            return False, "Readout time must be positive"
        if rt < min_rt:
            return False, f"Readout time seems too small (< {min_rt}s)"
        if rt > max_rt:
            return False, f"Readout time seems too large (> {max_rt}s)"

        return True, ""

    except ValueError:
        return False, "Readout time must be a number"


def validate_directory(path: str, create: bool = False) -> tuple[bool, str]:
    """
    Validate output directory.

    Parameters
    ----------
    path : str
        Directory path
    create : bool
        Whether to create directory if it doesn't exist

    Returns
    -------
    tuple of (bool, str)
        (is_valid, error_message)
    """
    if not path:
        return False, "Output directory is required"

    if os.path.isfile(path):
        return False, "Path exists as a file, not a directory"

    if not os.path.isdir(path):
        if create:
            try:
                os.makedirs(path, exist_ok=True)
                return True, "Directory created"
            except Exception as e:
                return False, f"Could not create directory: {str(e)}"
        else:
            return False, "Directory does not exist"

    # Check write permission
    if not os.access(path, os.W_OK):
        return False, "Directory is not writable"

    return True, ""
