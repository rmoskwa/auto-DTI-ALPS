"""
MRtrix3 command builders for DTI-ALPS pipeline.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pipeline import PipelineState


def _append_options_from_dict(cmd: list[str], options: dict) -> None:
    """
    Append CLI options from a dictionary to command list.

    Parameters
    ----------
    cmd : list of str
        Command list to append to
    options : dict
        Dictionary of option_name -> value pairs.
        For flags (bool True), only the option name is added.
        For other values, both option name and value are added.
    """
    for option_name, value in options.items():
        if value is None:
            continue
        if value is True:
            # Flag option (no value)
            cmd.append(option_name)
        elif value is False:
            # Disabled flag, skip
            continue
        elif isinstance(value, str) and value.strip():
            # String value - add with potential space prefix for nested options
            if option_name in ("-eddy_options", "-topup_options"):
                cmd.extend([option_name, f" {value}"])
            else:
                cmd.extend([option_name, value])
        elif isinstance(value, int | float):
            cmd.extend([option_name, str(value)])


def build_dwifslpreproc_cmd(state: "PipelineState") -> list[str]:
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
        # Only use -json_import with -rpe_header, as it relies on header/JSON for PE info
        # Using -json_import with explicit -pe_dir/-readout_time can cause conflicts
        if state.json_sidecar_path:
            cmd.extend(["-json_import", state.json_sidecar_path])

    # Legacy options (for backward compatibility)
    if state.eddy_mask_path:
        cmd.extend(["-eddy_mask", state.eddy_mask_path])
    if state.eddy_slspec_path:
        cmd.extend(["-eddy_slspec", state.eddy_slspec_path])
    if state.eddy_options:
        cmd.extend(["-eddy_options", f" {state.eddy_options}"])
    if state.topup_options:
        cmd.extend(["-topup_options", f" {state.topup_options}"])
    if state.generate_qc:
        qc_dir = state.get_output_path("eddy_qc")
        cmd.extend(["-eddyqc_all", qc_dir])

    # Append options from dict (new GUI options)
    _append_options_from_dict(cmd, state.dwifslpreproc_options)

    return cmd


def build_dwi2tensor_cmd(state: "PipelineState") -> list[str]:
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

    # Legacy: Processing mask
    if state.dti_mask_path:
        cmd.extend(["-mask", state.dti_mask_path])

    # Append options from dict (new GUI options)
    _append_options_from_dict(cmd, state.dwi2tensor_options)

    return cmd


def build_tensor2metric_cmd(state: "PipelineState") -> list[str]:
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

    # Output FA (always required for ROI detection)
    cmd.extend(["-fa", state.fa_path])

    # Output principal eigenvector V1 (always required for fiber classification)
    cmd.extend(["-vector", state.v1_path])

    # Default V1 settings (can be overridden by options dict)
    # Check if options dict overrides -num or -modulate
    options = state.tensor2metric_options
    if "-num" not in options:
        cmd.extend(["-num", "1"])
    if "-modulate" not in options:
        cmd.extend(["-modulate", "none"])

    # Append options from dict (new GUI options)
    _append_options_from_dict(cmd, options)

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
        for variant in [cmd, f"fsl{cmd}", f"{cmd}_cuda", "eddy_openmp"]:
            if shutil.which(variant) is not None:
                found = True
                break
        if not found:
            missing.append(cmd)

    return (len(missing) == 0, missing)
