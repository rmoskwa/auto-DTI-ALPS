"""
Unit tests for the tk-free input/form model (gui/form_model.py).

These build ``FormState`` fixtures and assert the resulting
``BatchState``/``OutputConfig``/``roi_shapes``/CLI-option dicts and
``Readiness`` — no Tkinter or PySide6 object is named and the window is never
instantiated. This suite is the regression net across the eventual Tk->Qt swap,
mirroring ``test_result_model.py`` for the output side.
"""

from dti_alps.gui.form_model import (
    NAV_DATA_INPUT,
    NAV_OUTPUT_SETUP,
    NAV_SYNB0,
    Blocker,
    FormState,
    OptionState,
    Readiness,
    build_batch_state,
    compute_blockers,
    compute_readiness,
)
from dti_alps.processing.discovery import SubjectFiles
from dti_alps.processing.state import BatchState, OutputConfig
from dti_alps.processing.validators import is_readout_valid


def _valid_subject(subject_id: str = "sub-01") -> SubjectFiles:
    """A subject whose required files are all present (is_valid is True)."""
    return SubjectFiles(
        subject_id=subject_id,
        folder_path=f"/data/{subject_id}",
        dwi_path="dwi.nii.gz",
        bvec_path="dwi.bvec",
        bval_path="dwi.bval",
    )


def _invalid_subject(subject_id: str = "sub-bad") -> SubjectFiles:
    """A subject missing its DWI (is_valid is False)."""
    return SubjectFiles(
        subject_id=subject_id,
        folder_path=f"/data/{subject_id}",
        bvec_path="dwi.bvec",
        bval_path="dwi.bval",
    )


class TestBuildBatchState:
    """build_batch_state() maps a FormState snapshot to a BatchState."""

    def test_fully_populated_form(self):
        """Scalar fields flow through to BatchConfig unchanged."""
        form = FormState(
            run_denoising=False,
            run_degibbs=False,
            pe_direction="PA",
            auto_pe_direction=False,
            readout_auto=False,
            readout_raw="0.062",
            rpe_scheme="all",
            fa_threshold=0.3,
            alps_method="ALPS-LAB",
            refine_roi_placement="Standard",
            output_dir="/out",
            staging_enabled=True,
            staging_dir_raw="/scratch",
            roi_shape_flags={"sphere3": True},
        )
        subjects = [_valid_subject("a"), _valid_subject("b")]

        state = build_batch_state(form, subjects)

        assert isinstance(state, BatchState)
        cfg = state.config
        assert cfg.run_denoising is False
        assert cfg.run_degibbs is False
        assert cfg.pe_direction == "PA"
        assert cfg.auto_pe_direction is False
        assert cfg.readout_time == 0.062
        assert cfg.rpe_scheme == "all"
        assert cfg.fa_threshold == 0.3
        assert cfg.alps_method == "ALPS-LAB"
        assert cfg.refine_roi_placement == "Standard"
        assert cfg.output_dir == "/out"
        assert cfg.staging_enabled is True
        assert cfg.staging_dir == "/scratch"
        # subjects are copied, not aliased
        assert state.subjects == subjects
        assert state.subjects is not subjects

    def test_readout_auto_resolves_to_none(self):
        """Auto mode defers readout resolution downstream -> readout_time is None."""
        form = FormState(readout_auto=True, readout_raw="ignored")
        cfg = build_batch_state(form, []).config
        assert cfg.readout_time is None

    def test_readout_manual_unparseable_falls_back_to_default(self):
        """Bad manual input is coerced to the default on the build path (runs)."""
        form = FormState(readout_auto=False, readout_raw="not-a-number")
        cfg = build_batch_state(form, []).config
        assert cfg.readout_time == 0.05

    def test_roi_shapes_default_when_nothing_selected(self):
        """No shape checkbox on -> defaults to sphere 3 mm."""
        cfg = build_batch_state(FormState(roi_shape_flags={}), []).config
        assert cfg.roi_shapes == [{"type": "sphere", "radius": 3.0}]

    def test_roi_shapes_multiple_selected_preserve_order(self):
        """Selected checkboxes map to shape dicts in checkbox order."""
        form = FormState(
            roi_shape_flags={
                "sphere2": True,
                "sphere2p5": False,
                "sphere3": True,
                "squarev4": False,
                "squarev9": True,
            }
        )
        cfg = build_batch_state(form, []).config
        assert cfg.roi_shapes == [
            {"type": "sphere", "radius": 2.0},
            {"type": "sphere", "radius": 3.0},
            {"type": "squarev9"},
        ]

    def test_cli_options_rules(self):
        """Disabled skipped, flag->True, value passthrough, int coerced, junk skipped."""
        form = FormState(
            cli_options={
                "dwidenoise": {
                    "-off": OptionState(enabled=False, type="flag"),
                    "-on": OptionState(enabled=True, type="flag"),
                    "-val": OptionState(enabled=True, value="foo", type="value"),
                    "-empty": OptionState(enabled=True, value="", type="value"),
                    "-n": OptionState(enabled=True, value="7", type="int"),
                    "-bad": OptionState(enabled=True, value="x", type="int"),
                }
            }
        )
        opts = build_batch_state(form, []).config.dwidenoise_options
        assert opts == {"-on": True, "-val": "foo", "-n": 7}

    def test_cli_options_collected_per_stage(self):
        """Each of the eight stage keys is collected into its own config dict."""
        form = FormState(
            cli_options={
                "dwidenoise": {"-a": OptionState(enabled=True, type="flag")},
                "mrdegibbs": {"-b": OptionState(enabled=True, type="flag")},
                "dwifslpreproc": {"-c": OptionState(enabled=True, type="flag")},
                "dwi2tensor": {"-d": OptionState(enabled=True, type="flag")},
                "tensor2metric": {"-e": OptionState(enabled=True, type="flag")},
                "flirt": {"-f": OptionState(enabled=True, type="flag")},
                "fnirt": {"-g": OptionState(enabled=True, type="flag")},
                "synb0_eddy": {"-h": OptionState(enabled=True, type="flag")},
            },
            use_synb0=True,
            synb0_output_dir_raw="/synb0",
        )
        cfg = build_batch_state(form, []).config
        assert cfg.dwidenoise_options == {"-a": True}
        assert cfg.mrdegibbs_options == {"-b": True}
        assert cfg.dwifslpreproc_options == {"-c": True}
        assert cfg.dwi2tensor_options == {"-d": True}
        assert cfg.tensor2metric_options == {"-e": True}
        assert cfg.flirt_options == {"-f": True}
        assert cfg.fnirt_options == {"-g": True}
        assert cfg.synb0_eddy_options == {"-h": True}

    def test_missing_cli_stage_yields_empty_dict(self):
        """A stage absent from the snapshot collects to an empty option dict."""
        cfg = build_batch_state(FormState(), []).config
        assert cfg.dwifslpreproc_options == {}

    def test_output_config_reflects_flags_with_default_true(self):
        """Explicit flags win; unspecified keys (incl. never-surfaced ones) stay True."""
        form = FormState(output_flags={"tensor": False, "log_file": False})
        oc = build_batch_state(form, []).config.output_config
        assert isinstance(oc, OutputConfig)
        assert oc.tensor is False
        assert oc.log_file is False
        # unspecified GUI key defaults true
        assert oc.fa_map is True
        # never-surfaced keys keep their OutputConfig defaults
        assert oc.b0_image is True
        assert oc.brain_mask is True

    def test_synb0_dir_empty_becomes_none(self):
        """Blank synB0 dir -> None; eddy options collected regardless of mode."""
        form = FormState(use_synb0=False, synb0_output_dir_raw="")
        cfg = build_batch_state(form, []).config
        assert cfg.synb0_output_dir is None

    def test_synb0_dir_populated_passes_through(self):
        """A non-blank synB0 dir passes through verbatim."""
        form = FormState(use_synb0=True, synb0_output_dir_raw="/data/synb0")
        cfg = build_batch_state(form, []).config
        assert cfg.synb0_output_dir == "/data/synb0"

    def test_staging_dir_empty_becomes_none(self):
        """Blank staging dir -> None."""
        cfg = build_batch_state(FormState(staging_dir_raw=""), []).config
        assert cfg.staging_dir is None


class TestComputeReadiness:
    """compute_readiness() reproduces the Run-button decision as structured flags."""

    def _ready_form(self) -> FormState:
        return FormState(output_dir="/out", readout_auto=True)

    def test_all_conditions_met(self):
        r = compute_readiness(self._ready_form(), [_valid_subject()])
        assert isinstance(r, Readiness)
        assert r.can_run is True
        assert r == Readiness(
            can_run=True,
            has_subjects=True,
            all_subjects_valid=True,
            has_output_dir=True,
            readout_valid=True,
            synb0_dir_valid=True,
        )

    def test_no_subjects_blocks(self):
        r = compute_readiness(self._ready_form(), [])
        assert r.can_run is False
        assert r.has_subjects is False
        assert r.all_subjects_valid is False

    def test_invalid_subject_blocks(self):
        r = compute_readiness(self._ready_form(), [_valid_subject(), _invalid_subject()])
        assert r.can_run is False
        assert r.has_subjects is True
        assert r.all_subjects_valid is False

    def test_missing_output_dir_blocks(self):
        form = FormState(output_dir="", readout_auto=True)
        r = compute_readiness(form, [_valid_subject()])
        assert r.can_run is False
        assert r.has_output_dir is False

    def test_invalid_manual_readout_blocks(self):
        form = FormState(output_dir="/out", readout_auto=False, readout_raw="junk")
        r = compute_readiness(form, [_valid_subject()])
        assert r.can_run is False
        assert r.readout_valid is False

    def test_synb0_mode_requires_dir(self):
        form = FormState(output_dir="/out", use_synb0=True, synb0_output_dir_raw="")
        r = compute_readiness(form, [_valid_subject()])
        assert r.can_run is False
        assert r.synb0_dir_valid is False

    def test_synb0_mode_with_dir_ready(self):
        form = FormState(output_dir="/out", use_synb0=True, synb0_output_dir_raw="/synb0")
        r = compute_readiness(form, [_valid_subject()])
        assert r.can_run is True
        assert r.synb0_dir_valid is True

    def test_non_synb0_mode_ignores_synb0_dir(self):
        """Outside synB0 mode a blank synB0 dir does not block."""
        form = FormState(output_dir="/out", use_synb0=False, synb0_output_dir_raw="")
        r = compute_readiness(form, [_valid_subject()])
        assert r.synb0_dir_valid is True
        assert r.can_run is True


class TestComputeBlockers:
    """compute_blockers() turns unmet conditions into ordered to-do rows."""

    def _ready_form(self) -> FormState:
        return FormState(output_dir="/out", readout_auto=True)

    def test_ready_form_has_no_blockers(self):
        assert compute_blockers(self._ready_form(), [_valid_subject()]) == []

    def test_no_subjects_yields_single_add_row(self):
        """Zero subjects -> the 'add' row only; the validity row is suppressed."""
        blockers = compute_blockers(self._ready_form(), [])
        assert blockers == [Blocker("No subjects added", NAV_DATA_INPUT)]

    def test_some_invalid_subjects_pluralised(self):
        subjects = [_valid_subject(), _invalid_subject("bad-1"), _invalid_subject("bad-2")]
        blockers = compute_blockers(self._ready_form(), subjects)
        assert blockers == [Blocker("2 subjects are invalid", NAV_DATA_INPUT)]

    def test_single_invalid_subject_singular(self):
        blockers = compute_blockers(self._ready_form(), [_valid_subject(), _invalid_subject()])
        assert blockers == [Blocker("1 subject is invalid", NAV_DATA_INPUT)]

    def test_invalid_readout_row(self):
        form = FormState(output_dir="/out", readout_auto=False, readout_raw="junk")
        blockers = compute_blockers(form, [_valid_subject()])
        assert Blocker("Readout time is invalid", NAV_DATA_INPUT) in blockers

    def test_missing_output_dir_row(self):
        form = FormState(output_dir="", readout_auto=True)
        blockers = compute_blockers(form, [_valid_subject()])
        assert blockers == [Blocker("Output folder not set", NAV_OUTPUT_SETUP)]

    def test_synb0_dir_row_only_in_synb0_mode(self):
        form = FormState(output_dir="/out", use_synb0=True, synb0_output_dir_raw="")
        blockers = compute_blockers(form, [_valid_subject()])
        assert blockers == [Blocker("synB0 output folder not set", NAV_SYNB0)]

    def test_non_synb0_mode_omits_synb0_row(self):
        form = FormState(output_dir="/out", use_synb0=False, synb0_output_dir_raw="")
        assert compute_blockers(form, [_valid_subject()]) == []

    def test_rows_ordered_by_nav_flow(self):
        """Data Input rows, then synB0, then Output Setup."""
        form = FormState(
            output_dir="",
            readout_auto=False,
            readout_raw="junk",
            use_synb0=True,
            synb0_output_dir_raw="",
        )
        blockers = compute_blockers(form, [])
        assert blockers == [
            Blocker("No subjects added", NAV_DATA_INPUT),
            Blocker("Readout time is invalid", NAV_DATA_INPUT),
            Blocker("synB0 output folder not set", NAV_SYNB0),
            Blocker("Output folder not set", NAV_OUTPUT_SETUP),
        ]

    def test_blocker_empty_iff_readiness_can_run(self):
        """The strip empties exactly when the Run button would enable."""
        form = FormState(output_dir="/out", readout_auto=True)
        subjects = [_valid_subject()]
        assert (compute_blockers(form, subjects) == []) is compute_readiness(form, subjects).can_run


class TestReadoutValidityInversionGuard:
    """
    Guard against the Decision-4 inversion: the button check must NOT be the
    coerce-to-default resolver. Auto mode is always valid; unparseable manual
    input is invalid (where resolve_readout_time would return its default).
    """

    def test_auto_mode_always_valid(self):
        assert is_readout_valid(True, "") is True
        assert is_readout_valid(True, "garbage") is True

    def test_manual_unparseable_is_invalid(self):
        assert is_readout_valid(False, "garbage") is False

    def test_manual_parseable_is_valid(self):
        assert is_readout_valid(False, "0.05") is True
