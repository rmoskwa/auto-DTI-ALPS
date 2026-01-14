"""
MRtrix3 command builders for DTI-ALPS pipeline.
"""

from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import PipelineState


def build_dwifslpreproc_cmd(state: 'PipelineState') -> List[str]:
    """
    Build dwifslpreproc command for DWI preprocessing.

    Parameters
    ----------
    state : PipelineState
        Pipeline configuration

    Returns
    -------
    list of str
        Command and arguments
    """
    cmd = ["dwifslpreproc"]

    # Input and output
    cmd.append(state.dwi_path)
    cmd.append(state.preprocessed_dwi_path)

    # Gradient table (FSL format)
    cmd.extend(["-fslgrad", state.bvecs_path, state.bvals_path])

    # Export corrected gradients
    bvecs_out = state.get_output_path("bvecs_preproc")
    bvals_out = state.get_output_path("bvals_preproc")
    cmd.extend(["-export_grad_fsl", bvecs_out, bvals_out])

    # Phase encoding direction
    cmd.extend(["-pe_dir", state.pe_direction])

    # Readout time
    cmd.extend(["-readout_time", str(state.readout_time)])

    # RPE scheme
    if state.rpe_scheme == "none":
        cmd.append("-rpe_none")
    elif state.rpe_scheme == "pair":
        cmd.append("-rpe_pair")
        if state.reverse_pe_path:
            cmd.extend(["-se_epi", state.reverse_pe_path])
            cmd.append("-align_seepi")
    elif state.rpe_scheme == "all":
        cmd.append("-rpe_all")
    elif state.rpe_scheme == "header":
        cmd.append("-rpe_header")

    # Optional: JSON sidecar
    if state.json_sidecar_path:
        cmd.extend(["-json_import", state.json_sidecar_path])

    # Optional: Eddy mask
    if state.eddy_mask_path:
        cmd.extend(["-eddy_mask", state.eddy_mask_path])

    # Optional: Slice specification
    if state.eddy_slspec_path:
        cmd.extend(["-eddy_slspec", state.eddy_slspec_path])

    # Optional: Extra eddy options
    if state.eddy_options:
        cmd.extend(["-eddy_options", f" {state.eddy_options}"])

    # Optional: Extra topup options
    if state.topup_options:
        cmd.extend(["-topup_options", f" {state.topup_options}"])

    # Optional: QC output
    if state.generate_qc:
        qc_dir = state.get_output_path("eddy_qc")
        cmd.extend(["-eddyqc_all", qc_dir])

    return cmd


def build_dwi2tensor_cmd(state: 'PipelineState') -> List[str]:
    """
    Build dwi2tensor command for DTI fitting.

    Parameters
    ----------
    state : PipelineState
        Pipeline configuration

    Returns
    -------
    list of str
        Command and arguments
    """
    cmd = ["dwi2tensor"]

    # Input (preprocessed DWI)
    cmd.append(state.preprocessed_dwi_path)

    # Output tensor
    cmd.append(state.tensor_path)

    # Gradient table (use exported corrected gradients)
    bvecs_preproc = state.get_output_path("bvecs_preproc")
    bvals_preproc = state.get_output_path("bvals_preproc")
    cmd.extend(["-fslgrad", bvecs_preproc, bvals_preproc])

    # Optional: Processing mask
    if state.dti_mask_path:
        cmd.extend(["-mask", state.dti_mask_path])

    return cmd


def build_tensor2metric_cmd(state: 'PipelineState') -> List[str]:
    """
    Build tensor2metric command to extract FA and V1.

    Parameters
    ----------
    state : PipelineState
        Pipeline configuration

    Returns
    -------
    list of str
        Command and arguments
    """
    cmd = ["tensor2metric"]

    # Input tensor
    cmd.append(state.tensor_path)

    # Output FA
    cmd.extend(["-fa", state.fa_path])

    # Output principal eigenvector (V1)
    # Use -modulate none for unit vectors (orientation only)
    cmd.extend(["-vector", state.v1_path])
    cmd.extend(["-num", "1"])
    cmd.extend(["-modulate", "none"])

    return cmd


def check_mrtrix3_available() -> tuple:
    """
    Check if MRtrix3 commands are available in PATH.

    Returns
    -------
    tuple of (bool, list)
        (all_available, list of missing commands)
    """
    import shutil

    required_commands = ["dwifslpreproc", "dwi2tensor", "tensor2metric"]
    missing = []

    for cmd in required_commands:
        if shutil.which(cmd) is None:
            missing.append(cmd)

    return (len(missing) == 0, missing)


def check_fsl_available() -> tuple:
    """
    Check if FSL commands required by dwifslpreproc are available.

    Returns
    -------
    tuple of (bool, list)
        (all_available, list of missing commands)
    """
    import shutil

    # FSL commands used by dwifslpreproc
    required_commands = ["eddy", "topup", "applytopup"]
    missing = []

    for cmd in required_commands:
        # FSL commands might have different names
        found = False
        for variant in [cmd, f"fsl{cmd}", f"{cmd}_cuda", f"eddy_openmp"]:
            if shutil.which(variant) is not None:
                found = True
                break
        if not found:
            missing.append(cmd)

    return (len(missing) == 0, missing)
