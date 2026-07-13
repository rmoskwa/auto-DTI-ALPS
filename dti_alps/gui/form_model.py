"""
Toolkit-free input/form model — the mirror image of ``result_model``.

Where :class:`~dti_alps.gui.result_model.ResultModel` maps *worker messages ->
view-intents*, this module maps a *form-state snapshot -> domain objects*. The
``gui/app.py`` Qt adapter reads its widgets into a :class:`FormState`, then
calls :func:`build_batch_state` (on Run) and :func:`compute_readiness` (on any
input change, for the live Run button). Because this input-mapping logic is
toolkit-agnostic, the Tk-to-Qt port (PRD 0013) reused it unchanged — the Qt
adapter fills the **same** ``FormState`` and calls the **same** functions the
former Tk adapter did.

Naming honesty: unlike its ``result_model``/``viewer_model`` siblings this is
**not** a stateful ``*Model`` class — it is a module of pure functions plus
frozen dataclasses. The symmetry with them is at the *seam* level (toolkit-free
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
    AdaptiveSearchConfig,
)
from ..processing.discovery import SubjectFiles
from ..processing.state import BatchConfig, BatchState, OutputConfig
from ..processing.validators import is_readout_valid, resolve_readout_time
from .config import ROI_SHAPES


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
    is blocked without re-deriving them. The Qt adapter reads ``can_run`` for the
    button; :func:`compute_blockers` turns the same conditions into the readiness
    strip's to-do rows.
    """

    can_run: bool
    has_subjects: bool
    all_subjects_valid: bool
    has_output_dir: bool
    readout_valid: bool
    synb0_dir_valid: bool


# Semantic navigation targets for a blocker row — the on-disk page ids the Qt
# adapter already registers (``_register_page``), so ``compute_blockers`` names
# *where* to send the user without importing a widget or a nav method. The
# adapter maps each token to the matching ``_show_*`` call.
NAV_DATA_INPUT = "data"
NAV_SYNB0 = "synb0"
NAV_OUTPUT_SETUP = "output_setup"


@dataclass(frozen=True)
class Blocker:
    """
    One outstanding-requirement row for the readiness strip.

    ``text`` is the fully-phrased, descriptive to-do line (the presentation model
    owns the wording); ``target`` is a semantic page id (``NAV_*``) the adapter
    routes to when the row is clicked. Toolkit-free: no colour, icon, or widget
    lives here — the adapter renders the neutral marker and link style.
    """

    text: str
    target: str


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
    adaptive_roi_placement: str = "Both"
    # Adaptive search envelope, as five raw widget scalars. Assembled into an
    # AdaptiveSearchConfig (where the 1-4 guard fires) in build_batch_state.
    # Defaults track the AdaptiveSearchConfig defaults so an untouched form
    # reproduces today's placement.
    search_x: int = AdaptiveSearchConfig().search_x
    search_y: int = AdaptiveSearchConfig().search_y
    search_z: int = AdaptiveSearchConfig().search_z
    max_y_drift: int = AdaptiveSearchConfig().max_y_drift
    max_z_drift: int = AdaptiveSearchConfig().max_z_drift
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


# Token -> geometry lookup and the empty-selection fallback, both derived from
# the ROI shape catalog (config.ROI_SHAPES, PRD 0015) so the selectable set, its
# geometry, and its default live in exactly one place.
_ROI_GEOMETRY_BY_TOKEN: dict[str, dict] = {shape.token: shape.geometry for shape in ROI_SHAPES}
_DEFAULT_ROI_GEOMETRY: dict = next(shape.geometry for shape in ROI_SHAPES if shape.default)


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
    Map selected ROI-shape checkboxes to shape dicts, defaulting to the catalog's
    default shape (3 mm sphere).

    Preserves the checkbox iteration order and the "nothing selected -> default
    shape" fallback of ``_collect_roi_shapes``. Geometry and the default both come
    from the ROI shape catalog (PRD 0015).
    """
    shapes = [_ROI_GEOMETRY_BY_TOKEN[key] for key, on in flags.items() if on]
    if not shapes:
        shapes.append(_DEFAULT_ROI_GEOMETRY)
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
        adaptive_roi_placement=form_state.adaptive_roi_placement,
        adaptive_search=AdaptiveSearchConfig(
            search_x=form_state.search_x,
            search_y=form_state.search_y,
            search_z=form_state.search_z,
            max_y_drift=form_state.max_y_drift,
            max_z_drift=form_state.max_z_drift,
        ),
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


def compute_blockers(form_state: FormState, subjects: list[SubjectFiles]) -> list[Blocker]:
    """
    The outstanding requirements to run, as ordered, clickable to-do rows.

    One :class:`Blocker` per unmet condition, in nav-flow order (Data Input
    items, then synB0, then Output Setup); an empty list means
    :attr:`Readiness.can_run` is ``True``. This is the strip's *only* wording
    home — the Qt adapter renders the rows and routes clicks by ``target`` but
    supplies no text.

    The two subject conditions collapse to a single adaptive row: no subjects ->
    "No subjects added"; some present but invalid -> "N subject(s) are invalid".
    They never both appear (with zero subjects the validity row would be noise).
    The synB0 row is emitted only in synB0 mode, matching
    :func:`compute_readiness`.
    """
    blockers: list[Blocker] = []

    if not subjects:
        blockers.append(Blocker("No subjects added", NAV_DATA_INPUT))
    else:
        invalid = sum(1 for s in subjects if not s.is_valid)
        if invalid:
            phrase = "subject is" if invalid == 1 else "subjects are"
            blockers.append(Blocker(f"{invalid} {phrase} invalid", NAV_DATA_INPUT))

    if not is_readout_valid(form_state.readout_auto, form_state.readout_raw):
        blockers.append(Blocker("Readout time is invalid", NAV_DATA_INPUT))

    if form_state.use_synb0 and not form_state.synb0_output_dir_raw:
        blockers.append(Blocker("synB0 output folder not set", NAV_SYNB0))

    if not form_state.output_dir:
        blockers.append(Blocker("Output folder not set", NAV_OUTPUT_SETUP))

    return blockers
