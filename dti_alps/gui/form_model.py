"""
tk-free input/form model — the mirror image of ``result_model``.

Where :class:`~dti_alps.gui.result_model.ResultModel` maps *worker messages ->
view-intents*, this module maps a *form-state snapshot -> domain objects*. The
``gui/app.py`` adapter reads its Tk Variables into a :class:`FormState`, then
calls :func:`build_batch_state` (on Run) and :func:`compute_readiness` (on any
input change, for the live Run button). When ``app.py`` is later ported, the Qt
adapter fills the **same** ``FormState`` and calls the **same** functions, so no
input-mapping logic is rewritten during the port.

Naming honesty: unlike its ``result_model``/``viewer_model`` siblings this is
**not** a stateful ``*Model`` class — it is a module of pure functions plus
frozen dataclasses. The symmetry with them is at the *seam* level (tk-free
presentation logic the adapter delegates to), not the *shape* level: the form
lives in the widgets, so the model is a snapshot-at-decision-time, not a second
source of truth. It imports only ``processing`` types, ``processing.validators``,
``processing.constants``, and ``dataclasses`` — never ``tkinter`` or ``PySide6``.
"""

from dataclasses import dataclass, field

from ..processing.constants import (
    DEFAULT_PE_DIRECTION,
    DEFAULT_READOUT_TIME,
    DEFAULT_RPE_SCHEME,
    FA_THRESHOLD,
)
from ..processing.discovery import SubjectFiles
from ..processing.state import BatchConfig, BatchState, OutputConfig
from ..processing.validators import is_readout_valid, resolve_readout_time


@dataclass(frozen=True)
class OptionState:
    """
    Snapshot of one CLI option's widgets, keyed under ``stage -> option name``.

    ``value`` is always the raw entry string (every value widget is a text
    field); ``type`` is the option's declared type (``"flag"``, ``"int"``, or a
    plain value type). :func:`build_batch_state` applies the flag/coerce/skip
    rules to this — the adapter never interprets it.
    """

    enabled: bool
    value: str = ""
    type: str = "value"


@dataclass(frozen=True)
class Readiness:
    """
    Structured result of :func:`compute_readiness`.

    ``can_run`` is the overall Run-button decision; the remaining flags are the
    individual conditions behind it, so a future adapter can surface *why* a run
    is blocked without re-deriving them. Today's Tk adapter reads only
    ``can_run``.
    """

    can_run: bool
    has_subjects: bool
    all_subjects_valid: bool
    has_output_dir: bool
    readout_valid: bool
    synb0_dir_valid: bool


@dataclass(frozen=True)
class FormState:
    """
    A frozen snapshot of every widget-backed scalar form field.

    Holds *raw widget values* — e.g. ``readout_auto`` + ``readout_raw`` rather
    than a resolved float, and ``synb0_output_dir_raw`` rather than a
    ``None``-ed path. All interpretation (resolving readout time, defaulting the
    ROI shapes, coercing/skipping CLI values, emptying the synB0/staging dirs to
    ``None``) happens inside :func:`build_batch_state`, the one tested place. The
    adapter's only job is a mechanical widget-value read; the defaults here just
    ease fixture construction — the adapter always sets every field explicitly.
    """

    run_denoising: bool = True
    run_degibbs: bool = True
    pe_direction: str = DEFAULT_PE_DIRECTION
    auto_pe_direction: bool = True
    readout_auto: bool = True
    readout_raw: str = str(DEFAULT_READOUT_TIME)
    rpe_scheme: str = DEFAULT_RPE_SCHEME
    use_synb0: bool = False
    synb0_output_dir_raw: str = ""
    fa_threshold: float = FA_THRESHOLD
    alps_method: str = "Both"
    refine_roi_placement: str = "Both"
    output_dir: str = ""
    staging_enabled: bool = False
    staging_dir_raw: str = ""
    # Checkbox booleans keyed by shape token: sphere2, sphere2p5, sphere3,
    # squarev4, squarev9.
    roi_shape_flags: dict[str, bool] = field(default_factory=dict)
    # Output-retention booleans keyed by artifact (denoised_dwi, tensor, ...).
    output_flags: dict[str, bool] = field(default_factory=dict)
    # stage -> option name -> OptionState.
    cli_options: dict[str, dict[str, OptionState]] = field(default_factory=dict)


# Checkbox token -> the ROI-shape dict it maps to. Order matches the GUI's
# checkbox order so the assembled roi_shapes list order is preserved.
_ROI_SHAPE_CONFIGS: dict[str, dict] = {
    "sphere2": {"type": "sphere", "radius": 2.0},
    "sphere2p5": {"type": "sphere", "radius": 2.5},
    "sphere3": {"type": "sphere", "radius": 3.0},
    "squarev4": {"type": "squarev4"},
    "squarev9": {"type": "squarev9"},
}


def _collect_cli_options(stage_options: dict[str, OptionState]) -> dict:
    """
    Apply the flag/coerce/skip rules for one stage's options.

    Skip when not enabled; a ``flag`` type emits ``True``; otherwise emit the
    value, coercing ``int`` types via ``int(...)`` and silently skipping empty
    strings and un-parseable ints. Reproduces ``_collect_cli_options`` verbatim.
    """
    options: dict = {}
    for option_name, opt in stage_options.items():
        if not opt.enabled:
            continue
        if opt.type == "flag":
            options[option_name] = True
        else:
            value = opt.value
            if value:  # Only add non-empty values
                if opt.type == "int":
                    try:
                        options[option_name] = int(value)
                    except ValueError:
                        pass  # Skip invalid int
                else:
                    options[option_name] = value
    return options


def _collect_roi_shapes(flags: dict[str, bool]) -> list[dict]:
    """
    Map selected ROI-shape checkboxes to shape dicts, defaulting to sphere 3 mm.

    Preserves the checkbox iteration order and the "nothing selected -> sphere
    3 mm" fallback of ``_collect_roi_shapes``.
    """
    shapes = [_ROI_SHAPE_CONFIGS[key] for key, on in flags.items() if on]
    if not shapes:
        shapes.append({"type": "sphere", "radius": 3.0})
    return shapes


def collect_output_config(flags: dict[str, bool]) -> OutputConfig:
    """
    Build ``OutputConfig`` from the retention flags, per-key default-true.

    Only the keys the GUI exposes are read; ``b0_image`` and ``brain_mask`` are
    never surfaced, so they keep their ``OutputConfig`` defaults — matching
    ``_collect_output_config`` exactly. Public because the adapter also needs the
    output config on its own (deciding whether to delete the log file), not only
    as part of a full ``build_batch_state``.
    """
    return OutputConfig(
        denoised_dwi=flags.get("denoised_dwi", True),
        degibbs_dwi=flags.get("degibbs_dwi", True),
        preprocessed_dwi=flags.get("preprocessed_dwi", True),
        preprocessed_bvecs=flags.get("preprocessed_bvecs", True),
        tensor=flags.get("tensor", True),
        fa_map=flags.get("fa_map", True),
        eigenvector_maps=flags.get("eigenvector_maps", True),
        fa_brain=flags.get("fa_brain", True),
        affine_matrix=flags.get("affine_matrix", True),
        warp_coefficients=flags.get("warp_coefficients", True),
        inverse_warp=flags.get("inverse_warp", True),
        roi_masks=flags.get("roi_masks", True),
        log_file=flags.get("log_file", True),
    )


def build_batch_state(form_state: FormState, subjects: list[SubjectFiles]) -> BatchState:
    """
    Assemble a ``BatchState`` from a form snapshot and the subject list.

    Byte-for-byte equivalent to the former
    ``_collect_batch_state``/``_collect_output_config``/``_collect_roi_shapes``/
    ``_collect_cli_options`` chain: it reuses ``resolve_readout_time`` unchanged,
    empties the synB0/staging dirs to ``None`` when blank, and applies the CLI /
    ROI-shape / output-flag rules through the helpers above.
    """
    readout_time = resolve_readout_time(
        form_state.readout_auto,
        form_state.readout_raw,
        DEFAULT_READOUT_TIME,
    )

    cli = form_state.cli_options
    batch_config = BatchConfig(
        run_denoising=form_state.run_denoising,
        dwidenoise_options=_collect_cli_options(cli.get("dwidenoise", {})),
        run_degibbs=form_state.run_degibbs,
        mrdegibbs_options=_collect_cli_options(cli.get("mrdegibbs", {})),
        pe_direction=form_state.pe_direction,
        auto_pe_direction=form_state.auto_pe_direction,
        readout_time=readout_time,
        rpe_scheme=form_state.rpe_scheme,
        dwifslpreproc_options=_collect_cli_options(cli.get("dwifslpreproc", {})),
        dwi2tensor_options=_collect_cli_options(cli.get("dwi2tensor", {})),
        tensor2metric_options=_collect_cli_options(cli.get("tensor2metric", {})),
        use_synb0=form_state.use_synb0,
        synb0_output_dir=form_state.synb0_output_dir_raw or None,
        synb0_eddy_options=_collect_cli_options(cli.get("synb0_eddy", {})),
        flirt_options=_collect_cli_options(cli.get("flirt", {})),
        fnirt_options=_collect_cli_options(cli.get("fnirt", {})),
        roi_shapes=_collect_roi_shapes(form_state.roi_shape_flags),
        fa_threshold=form_state.fa_threshold,
        alps_method=form_state.alps_method,
        refine_roi_placement=form_state.refine_roi_placement,
        output_dir=form_state.output_dir,
        output_config=collect_output_config(form_state.output_flags),
        staging_enabled=form_state.staging_enabled,
        staging_dir=form_state.staging_dir_raw or None,
    )

    return BatchState(config=batch_config, subjects=list(subjects))


def compute_readiness(form_state: FormState, subjects: list[SubjectFiles]) -> Readiness:
    """
    Reproduce today's Run-button enable/disable decision, as structured flags.

    The five conditions match ``_update_run_button_state`` exactly: subjects
    present, all subjects valid, output dir set, readout valid (via
    :func:`is_readout_valid`, **not** the coerce-to-default resolver), and — in
    synB0 mode only — the synB0 output dir set. Computed independently of
    ``validate_runnable`` (which is first-failure-wins and cannot yield the
    per-condition flags); the two agree by construction.
    """
    has_subjects = len(subjects) > 0
    all_subjects_valid = all(s.is_valid for s in subjects) if has_subjects else False
    has_output_dir = bool(form_state.output_dir)
    readout_valid = is_readout_valid(form_state.readout_auto, form_state.readout_raw)
    synb0_dir_valid = bool(form_state.synb0_output_dir_raw) if form_state.use_synb0 else True

    can_run = (
        has_subjects and all_subjects_valid and has_output_dir and readout_valid and synb0_dir_valid
    )

    return Readiness(
        can_run=can_run,
        has_subjects=has_subjects,
        all_subjects_valid=all_subjects_valid,
        has_output_dir=has_output_dir,
        readout_valid=readout_valid,
        synb0_dir_valid=synb0_dir_valid,
    )
