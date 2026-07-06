"""
Input validation for DTI-ALPS pipeline.
"""

import os

from .discovery import SubjectFiles


def validate_synb0_output_dir(path: str) -> tuple[bool, list[str]]:
    """
    Validate the contents of a synB0-DISCO OUTPUTS directory.

    Checks for the topup outputs the eddy step consumes. ``acqparams.txt`` is
    accepted either in ``path`` itself or in a sibling ``../INPUTS`` directory,
    matching the synB0-DISCO layout.

    Parameters
    ----------
    path : str
        Path to the synB0-DISCO OUTPUTS directory.

    Returns
    -------
    tuple of (bool, list[str])
        ``(ok, missing)`` where ``ok`` is True when nothing is missing and
        ``missing`` is the list of absent-file descriptions (e.g.
        ``"topup_fieldcoef.nii.gz (topup field coefficients)"``). The caller
        owns any user-facing phrasing/colour.
    """
    required_files = [
        ("topup_fieldcoef.nii.gz", "topup field coefficients"),
        ("topup_movpar.txt", "topup movement parameters"),
    ]

    missing: list[str] = []
    for filename, desc in required_files:
        if not os.path.exists(os.path.join(path, filename)):
            missing.append(f"{filename} ({desc})")

    # acqparams.txt may live in OUTPUTS or in the sibling ../INPUTS directory
    acqparams_found = os.path.exists(os.path.join(path, "acqparams.txt"))
    if not acqparams_found:
        parent = os.path.dirname(path)
        acqparams_found = os.path.exists(os.path.join(parent, "INPUTS", "acqparams.txt"))
    if not acqparams_found:
        missing.append("acqparams.txt (acquisition parameters)")

    return (not missing, missing)


def resolve_readout_time(auto: bool, raw: str, default: float) -> float | None:
    """
    Resolve the readout time the pipeline should use from the GUI inputs.

    When ``auto`` is set the readout time is extracted downstream from the JSON
    sidecar, so this returns ``None``.
    Otherwise the raw string is parsed, falling back to ``default`` on a parse
    failure. No range checking is applied — that matches the GUI's behavior.

    Parameters
    ----------
    auto : bool
        Whether auto-extraction from the JSON sidecar is enabled.
    raw : str
        The raw readout-time string entered in the GUI.
    default : float
        Fallback value used when ``raw`` cannot be parsed as a float.

    Returns
    -------
    float or None
        ``None`` when ``auto`` is set; otherwise the parsed value or ``default``.
    """
    if auto:
        return None
    try:
        return float(raw)
    except ValueError:
        return default


def is_readout_valid(auto: bool, raw: str) -> bool:
    """
    Decide whether the readout-time input is usable for the Run button.

    This is the *readiness* policy, deliberately distinct from
    :func:`resolve_readout_time` (the *build* policy). Here bad input **blocks**
    the run; there bad manual input is **coerced** to a default and runs. In auto
    mode the value is resolved downstream from the JSON sidecar, so any raw string
    is acceptable.

    Parameters
    ----------
    auto : bool
        Whether auto-extraction from the JSON sidecar is enabled.
    raw : str
        The raw readout-time string entered in the GUI.

    Returns
    -------
    bool
        True in auto mode; otherwise whether ``raw`` parses as a float.
    """
    if auto:
        return True
    try:
        float(raw)
    except ValueError:
        return False
    return True


def validate_runnable(
    subjects: list[SubjectFiles], output_dir: str
) -> tuple[bool, str | None, list[str] | None]:
    """
    Decide whether a batch can be launched, first-failure-wins.

    Reproduces the pre-flight checks in their original order: no subjects, then
    any subject with missing files, then a missing output directory. Returns a
    structured verdict; the caller owns all dialog phrasing (including the
    "first 5 + (and N more)" truncation of the invalid-subject ids).

    Parameters
    ----------
    subjects : list[SubjectFiles]
        The subjects queued for processing.
    output_dir : str
        The configured output directory.

    Returns
    -------
    tuple of (bool, str | None, list[str] | None)
        ``(ok, kind, payload)``. When ``ok`` is True, ``kind`` and ``payload``
        are ``None``. Otherwise ``kind`` is one of ``"no_subjects"``,
        ``"invalid_subjects"`` (with ``payload`` the list of invalid subject
        ids), or ``"no_output_dir"``.
    """
    if not subjects:
        return (False, "no_subjects", None)

    invalid_ids = [s.subject_id for s in subjects if not s.is_valid]
    if invalid_ids:
        return (False, "invalid_subjects", invalid_ids)

    if not output_dir:
        return (False, "no_output_dir", None)

    return (True, None, None)
