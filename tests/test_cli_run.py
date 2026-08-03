"""
Unit tests for the ``run`` verb (``dti_alps/cli/run.py``).

Nothing here processes real data: preflight is faked, and the one test that
reaches ``BatchRunner`` injects a fake pipeline runner. The point of the suite
is the *contract* -- precedence, vocabulary, exit codes, resume -- not the
pipeline, which its own suites cover.
"""

import json
import os

import pytest

from dti_alps.cli import run as run_verb
from dti_alps.cli.main import (
    EXIT_FAILURES,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_USAGE,
    build_parser,
)
from dti_alps.processing.config_io import PROTOCOL_FIELDS, protocol_hash, write_protocol
from dti_alps.processing.constants import FA_THRESHOLD, AdaptiveSearchConfig
from dti_alps.processing.results_layout import CompletionMarker, write_completion_marker
from dti_alps.processing.state import BatchConfig


def _parse(*argv):
    """Parse a ``run`` command line through the real top-level grammar."""
    return build_parser().parse_args(["run", *argv])


def _args(output="/out", **overrides):
    """A parsed namespace for the minimal valid command line, plus overrides."""
    argv = ["--subjects", "/data", "--output", output]
    for flag, value in overrides.items():
        flag = "--" + flag.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv.extend([flag, str(value)])
    return _parse(*argv)


@pytest.fixture
def cohort(tmp_path):
    """Two discoverable subjects with all required files."""
    for sid in ("sub-01", "sub-02"):
        folder = tmp_path / "cohort" / sid
        folder.mkdir(parents=True)
        for ext in ("nii.gz", "bvec", "bval"):
            (folder / f"dwi.{ext}").write_bytes(b"")
    return tmp_path / "cohort"


@pytest.fixture
def tools_present(monkeypatch):
    """Preflight passes, so tests exercise the run rather than the environment."""
    monkeypatch.setattr(run_verb, "preflight", lambda use_synb0: [])


# ---------------------------------------------------------------------------
# Precedence: defaults < protocol < flags
# ---------------------------------------------------------------------------


class TestPrecedence:
    """
    The `default=None` trap, guarded in both directions.

    A flag defaulting to its engine value would silently clobber a protocol that
    set something else -- and it would look exactly like the file was ignored.
    """

    def _protocol(self, tmp_path, **overrides):
        path = tmp_path / "protocol.json"
        write_protocol(path, BatchConfig(**overrides))
        return str(path)

    def test_defaults_apply_with_no_protocol_and_no_flags(self):
        config = run_verb.build_config(_args())

        assert config.fa_threshold == FA_THRESHOLD
        assert config.alps_method == BatchConfig().alps_method
        assert config.adaptive_roi_placement == BatchConfig().adaptive_roi_placement

    def test_protocol_beats_defaults(self, tmp_path):
        path = self._protocol(tmp_path, fa_threshold=0.35, alps_method="ALPS-PAS")

        config = run_verb.build_config(_args(config=path))

        assert config.fa_threshold == 0.35
        assert config.alps_method == "ALPS-PAS"

    def test_flags_beat_the_protocol(self, tmp_path):
        path = self._protocol(tmp_path, fa_threshold=0.35, alps_method="ALPS-PAS")

        config = run_verb.build_config(_args(config=path, fa_threshold=0.4, method="ALPS-LAB"))

        assert config.fa_threshold == 0.4
        assert config.alps_method == "ALPS-LAB"

    def test_an_omitted_flag_does_not_clobber_a_file_value(self, tmp_path):
        """The whole reason every overlapping flag carries default=None."""
        path = self._protocol(
            tmp_path,
            fa_threshold=0.35,
            readout_time=0.037,
            pe_direction="LR",
            rpe_scheme="pair",
            adaptive_roi_placement="Standard",
            roi_shapes=[{"type": "squarev4"}],
        )

        config = run_verb.build_config(_args(config=path))

        assert config.fa_threshold == 0.35
        assert config.readout_time == 0.037
        assert config.pe_direction == "LR"
        assert config.rpe_scheme == "pair"
        assert config.adaptive_roi_placement == "Standard"
        assert config.roi_shapes == [{"type": "squarev4"}]

    def test_omitted_stage_flags_do_not_re_enable_a_disabled_stage(self, tmp_path):
        """`store_true` absence means "say nothing", not "run it"."""
        path = self._protocol(tmp_path, run_denoising=False, run_degibbs=False)

        config = run_verb.build_config(_args(config=path))

        assert config.run_denoising is False
        assert config.run_degibbs is False

    def test_stage_flags_disable_stages(self):
        config = run_verb.build_config(_args(no_denoise=True, no_degibbs=True))

        assert config.run_denoising is False
        assert config.run_degibbs is False

    def test_every_protocol_field_is_reachable_from_the_file(self, tmp_path):
        """A protocol key the CLI could not honour would be a silent no-op."""
        source = BatchConfig(
            run_denoising=False,
            run_degibbs=False,
            pe_direction="PA",
            auto_pe_direction=False,
            readout_time=0.037,
            rpe_scheme="all",
            dwidenoise_options={"-nthreads": 2},
            mrdegibbs_options={"-nshifts": 30},
            dwifslpreproc_options={"-nocleanup": True},
            dwi2tensor_options={"-ols": True},
            tensor2metric_options={"-modulate": "FA"},
            use_synb0=True,
            synb0_output_dir="/synb0",
            synb0_eddy_options={"repol": True},
            flirt_options={"-dof": "9"},
            fnirt_options={"--warpres": "8,8,8"},
            roi_shapes=[{"type": "squarev9"}],
            fa_threshold=0.33,
            alps_method="ALPS-PAS",
            adaptive_roi_placement="Standard",
            adaptive_search=AdaptiveSearchConfig(search_x=4),
        )
        path = tmp_path / "protocol.json"
        write_protocol(path, source)

        config = run_verb.build_config(_args(config=str(path)))

        for name in sorted(PROTOCOL_FIELDS):
            assert getattr(config, name) == getattr(source, name), name


class TestPlacementComesFromFlagsOnly:
    """A protocol carries no paths, so `--output` is required and authoritative."""

    def test_output_is_required(self):
        with pytest.raises(SystemExit) as exc:
            _parse("--subjects", "/data")
        assert exc.value.code == EXIT_USAGE

    def test_output_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = run_verb.build_config(_args(output="relative/out"))

        assert os.path.isabs(config.output_dir)

    def test_staging_flags_land_on_the_config(self):
        config = run_verb.build_config(_args(staging=True, staging_dir="/fast"))

        assert config.staging_enabled is True
        assert config.staging_dir == "/fast"


# ---------------------------------------------------------------------------
# Individual flag semantics
# ---------------------------------------------------------------------------


class TestAcquisitionFlags:
    def test_pe_dir_disables_auto_detection(self):
        """
        Without this, an explicit --pe-dir AP would do nothing for any subject
        whose JSON sidecar says `j` -- BatchRunner lets the sidecar win.
        """
        config = run_verb.build_config(_args(pe_dir="AP"))

        assert config.pe_direction == "AP"
        assert config.auto_pe_direction is False

    def test_omitting_pe_dir_leaves_auto_detection_on(self):
        assert run_verb.build_config(_args()).auto_pe_direction is True

    def test_readout_omitted_means_auto_extract(self):
        assert run_verb.build_config(_args()).readout_time is None

    def test_readout_flag_sets_the_time(self):
        assert run_verb.build_config(_args(readout=0.042)).readout_time == 0.042

    def test_synb0_dir_selects_the_ten_stage_route(self):
        """The route is selected by supplying its input, not a redundant mode flag."""
        config = run_verb.build_config(_args(synb0_dir="/data/synb0/OUTPUTS"))

        assert config.use_synb0 is True
        assert config.synb0_output_dir == "/data/synb0/OUTPUTS"

    def test_no_synb0_dir_leaves_the_standard_route(self):
        assert run_verb.build_config(_args()).use_synb0 is False


class TestRoiFlags:
    def test_sphere_radii_become_geometries(self):
        config = run_verb.build_config(_args(sphere="2,3"))

        assert config.roi_shapes == [
            {"type": "sphere", "radius": 2.0},
            {"type": "sphere", "radius": 3.0},
        ]

    def test_shapes_combine(self):
        config = run_verb.build_config(_parse(*_shape_argv()))

        assert config.roi_shapes == [
            {"type": "sphere", "radius": 3.0},
            {"type": "squarev9"},
            {"type": "squarev4"},
        ]

    def test_no_shape_flag_leaves_the_default(self):
        assert run_verb.build_config(_args()).roi_shapes == BatchConfig().roi_shapes

    def test_roi_method_is_tri_state(self):
        """Three values, spelled identically by both verbs."""
        assert run_verb.build_config(_args(roi_method="Standard")).adaptive_roi_placement == (
            "Standard"
        )
        assert run_verb.build_config(_args(roi_method="Both")).adaptive_roi_placement == "Both"

    def test_partial_search_override_keeps_the_other_four(self):
        config = run_verb.build_config(_args(search_x=4))
        default = AdaptiveSearchConfig()

        assert config.adaptive_search.search_x == 4
        assert config.adaptive_search.search_y == default.search_y
        assert config.adaptive_search.max_z_drift == default.max_z_drift

    def test_search_override_on_top_of_a_protocol(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(
            path, BatchConfig(adaptive_search=AdaptiveSearchConfig(search_x=2, search_y=2))
        )

        config = run_verb.build_config(_args(config=str(path), search_z=3))

        assert config.adaptive_search.search_x == 2  # from the file
        assert config.adaptive_search.search_y == 2  # from the file
        assert config.adaptive_search.search_z == 3  # from the flag

    def test_out_of_range_search_is_rejected_at_parse_time(self):
        with pytest.raises(SystemExit) as exc:
            _parse("--subjects", "/d", "--output", "/o", "--search-x", "9")
        assert exc.value.code == EXIT_USAGE

    def test_roi_flags_are_spelled_as_on_reanalyze(self):
        """User story 19: one vocabulary, not two."""
        run_flags = _flag_names("run")
        reanalyze_flags = _flag_names("reanalyze")

        shared = {
            "--sphere",
            "--squarev9",
            "--squarev4",
            "--search-x",
            "--search-y",
            "--search-z",
            "--max-y-drift",
            "--max-z-drift",
            "--method",
            "--fa-threshold",
        }
        assert shared <= run_flags
        assert shared <= reanalyze_flags


def _shape_argv():
    return ["--subjects", "/d", "--output", "/o", "--sphere", "3", "--squarev9", "--squarev4"]


def _flag_names(verb: str) -> set[str]:
    """Every long-option string a verb's subparser accepts."""
    parser = build_parser()
    subparsers = next(
        a for a in parser._actions if isinstance(a, type(parser._subparsers._group_actions[0]))
    )
    verb_parser = subparsers.choices[verb]
    return {opt for action in verb_parser._actions for opt in action.option_strings}


# ---------------------------------------------------------------------------
# --opt and --nthreads
# ---------------------------------------------------------------------------


class TestOptEscapeHatch:
    """
    Guard 4b: the `--opt` stage vocabulary is derived from BatchConfig, so it
    cannot drift as stages are added -- and the CLI needs no GUI import to know it.
    """

    def test_every_stage_maps_to_a_real_options_field(self):
        config = BatchConfig()
        for stage, field_name in run_verb.option_stages().items():
            assert isinstance(getattr(config, field_name), dict), stage

    def test_the_vocabulary_covers_every_options_field(self):
        options_fields = {f for f in vars(BatchConfig()) if f.endswith("_options")}
        assert set(run_verb.option_stages().values()) == options_fields

    def test_nthreads_stages_are_all_valid_stages(self):
        assert set(run_verb.NTHREADS_STAGES) <= set(run_verb.option_stages())

    def test_opt_lands_in_the_matching_dict(self):
        config = run_verb.build_config(
            _parse(
                "--subjects",
                "/d",
                "--output",
                "/o",
                "--opt",
                "dwifslpreproc:-eddy_options=--repol --slm=linear",
            )
        )

        assert config.dwifslpreproc_options["-eddy_options"] == "--repol --slm=linear"

    def test_opt_is_repeatable_across_stages(self):
        config = run_verb.build_config(
            _parse(
                "--subjects",
                "/d",
                "--output",
                "/o",
                "--opt",
                "dwidenoise:-extent=5,5,5",
                "--opt",
                "flirt:-dof=9",
                "--opt",
                "synb0_eddy:niter=7",
            )
        )

        assert config.dwidenoise_options["-extent"] == "5,5,5"
        assert config.flirt_options["-dof"] == 9
        assert config.synb0_eddy_options["niter"] == 7

    def test_empty_value_is_a_flag(self):
        config = run_verb.build_config(
            _parse("--subjects", "/d", "--output", "/o", "--opt", "dwi2tensor:-ols=")
        )

        assert config.dwi2tensor_options["-ols"] is True

    def test_numeric_values_are_coerced(self):
        config = run_verb.build_config(
            _parse("--subjects", "/d", "--output", "/o", "--opt", "mrdegibbs:-nshifts=30")
        )

        assert config.mrdegibbs_options["-nshifts"] == 30

    def test_unknown_stage_is_rejected_and_lists_the_valid_ones(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _parse("--subjects", "/d", "--output", "/o", "--opt", "dwipreproc:-x=1")
        assert exc.value.code == EXIT_USAGE
        assert "dwifslpreproc" in capsys.readouterr().err

    @pytest.mark.parametrize("bad", ["nostage", "dwidenoise-nomarker", "dwidenoise:novalue"])
    def test_malformed_opt_is_rejected(self, bad):
        with pytest.raises(SystemExit):
            _parse("--subjects", "/d", "--output", "/o", "--opt", bad)

    def test_opt_layers_onto_a_protocol(self, tmp_path):
        path = tmp_path / "protocol.json"
        write_protocol(path, BatchConfig(dwidenoise_options={"-extent": "3,3,3"}))

        config = run_verb.build_config(
            _parse(
                "--subjects",
                "/d",
                "--output",
                "/o",
                "--config",
                str(path),
                "--opt",
                "dwidenoise:-nthreads=4",
            )
        )

        assert config.dwidenoise_options == {"-extent": "3,3,3", "-nthreads": 4}


class TestNthreads:
    def test_fans_out_to_every_mrtrix_stage(self):
        config = run_verb.build_config(_args(nthreads=8))

        for stage in run_verb.NTHREADS_STAGES:
            field = run_verb.option_stages()[stage]
            assert getattr(config, field)["-nthreads"] == 8

    def test_does_not_touch_the_fsl_stages(self):
        """FLIRT and FNIRT take no -nthreads; passing one would be an error."""
        config = run_verb.build_config(_args(nthreads=8))

        assert config.flirt_options == {}
        assert config.fnirt_options == {}

    def test_a_per_stage_opt_wins(self):
        config = run_verb.build_config(
            _parse(
                "--subjects",
                "/d",
                "--output",
                "/o",
                "--nthreads",
                "8",
                "--opt",
                "dwidenoise:-nthreads=2",
            )
        )

        assert config.dwidenoise_options["-nthreads"] == 2
        assert config.dwi2tensor_options["-nthreads"] == 8


# ---------------------------------------------------------------------------
# Subject resolution
# ---------------------------------------------------------------------------


class TestSubjectResolution:
    def test_subjects_flag_discovers_a_cohort(self, cohort):
        subjects = run_verb.resolve_subjects(_parse("--subjects", str(cohort), "--output", "/o"))

        assert sorted(s.subject_id for s in subjects) == ["sub-01", "sub-02"]

    def test_repeated_subjects_flags_are_deduped(self, cohort):
        args = _parse("--subjects", str(cohort), "--subjects", str(cohort), "--output", "/o")

        assert len(run_verb.resolve_subjects(args)) == 2

    def test_id_depth_is_forwarded_to_discovery(self, cohort):
        args = _parse("--subjects", str(cohort), "--output", "/o", "--id-depth", "2")

        subjects = run_verb.resolve_subjects(args)

        assert sorted(s.subject_id for s in subjects) == ["cohort_sub-01", "cohort_sub-02"]

    def test_single_subject_escape_hatch(self, tmp_path):
        args = _parse(
            "--dwi",
            str(tmp_path / "a.nii.gz"),
            "--bvec",
            str(tmp_path / "a.bvec"),
            "--bval",
            str(tmp_path / "a.bval"),
            "--rpe",
            str(tmp_path / "rpe.nii.gz"),
            "--output",
            "/o",
        )

        subjects = run_verb.resolve_subjects(args)

        assert len(subjects) == 1
        # The explicit RPE is the only way to override discovery's greedy guess.
        assert subjects[0].reverse_pe_path == str(tmp_path / "rpe.nii.gz")


class TestDataFlagValidation:
    def test_subjects_and_dwi_are_mutually_exclusive(self):
        args = _parse("--subjects", "/d", "--dwi", "a.nii.gz", "--output", "/o")

        assert "mutually exclusive" in run_verb.validate_data_flags(args)

    def test_one_of_them_is_required(self):
        args = _parse("--output", "/o")

        assert "required" in run_verb.validate_data_flags(args)

    def test_dwi_needs_its_gradients(self):
        args = _parse("--dwi", "a.nii.gz", "--output", "/o")

        assert "together" in run_verb.validate_data_flags(args)

    def test_id_depth_must_be_at_least_one(self):
        args = _parse("--subjects", "/d", "--output", "/o", "--id-depth", "0")

        assert "at least 1" in run_verb.validate_data_flags(args)

    def test_a_coherent_command_line_passes(self):
        assert run_verb.validate_data_flags(_args()) is None


# ---------------------------------------------------------------------------
# Exit codes and execution
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_usage_error_is_two(self, capsys):
        assert run_verb.execute(_parse("--output", "/o")) == EXIT_USAGE
        assert "ERROR" in capsys.readouterr().err

    def test_unreadable_protocol_is_a_usage_error(self, tmp_path, capsys):
        bad = tmp_path / "protocol.json"
        bad.write_text("{ not json")

        code = run_verb.execute(_args(config=str(bad)))

        assert code == EXIT_USAGE
        assert "not valid JSON" in capsys.readouterr().err

    def test_no_subjects_found_is_a_usage_error(self, tmp_path, capsys):
        empty = tmp_path / "empty"
        empty.mkdir()

        code = run_verb.execute(_parse("--subjects", str(empty), "--output", "/o"))

        assert code == EXIT_USAGE
        assert "no subjects found" in capsys.readouterr().err

    def test_id_collision_is_a_usage_error(self, tmp_path, capsys):
        for sid in ("sub-01", "sub-02"):
            leaf = tmp_path / sid / "ses-1" / "dwi"
            leaf.mkdir(parents=True)
            for ext in ("nii.gz", "bvec", "bval"):
                (leaf / f"dwi.{ext}").write_bytes(b"")

        code = run_verb.execute(
            _parse(
                "--subjects",
                str(tmp_path / "sub-01" / "ses-1" / "dwi"),
                "--subjects",
                str(tmp_path / "sub-02" / "ses-1" / "dwi"),
                "--output",
                str(tmp_path / "out"),
            )
        )

        assert code == EXIT_USAGE
        assert "--id-depth" in capsys.readouterr().err

    def test_missing_tools_are_exit_three(self, cohort, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(run_verb, "preflight", lambda use_synb0: ["fnirt", "dwi2mask"])

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(tmp_path / "out"))
        )

        assert code == EXIT_PREFLIGHT
        err = capsys.readouterr().err
        assert "fnirt" in err and "dwi2mask" in err

    def test_preflight_runs_before_any_data_is_touched(self, cohort, tmp_path, monkeypatch):
        monkeypatch.setattr(run_verb, "preflight", lambda use_synb0: ["fnirt"])
        output = tmp_path / "out"

        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        assert not output.exists()


class TestDryRun:
    """
    In scope rather than deferred: discovery is heuristic, and this replaces the
    GUI's file-summary column -- the thing a headless user otherwise loses.
    """

    def test_lists_subjects_and_their_target_directories(
        self, cohort, tmp_path, tools_present, capsys
    ):
        output = tmp_path / "out"

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(output), "--dry-run")
        )

        out = capsys.readouterr().out
        assert code == EXIT_OK
        assert "sub-01" in out and "sub-02" in out
        assert str(output / "sub-01") in out

    def test_processes_nothing(self, cohort, tmp_path, tools_present):
        output = tmp_path / "out"

        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output), "--dry-run"))

        assert not output.exists()

    def test_reports_preflight_and_exits_three_when_tools_are_missing(
        self, cohort, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(run_verb, "preflight", lambda use_synb0: ["fnirt"])

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(tmp_path / "o"), "--dry-run")
        )

        assert code == EXIT_PREFLIGHT
        assert "fnirt" in capsys.readouterr().out

    def test_surfaces_a_collision_before_the_guard_fires(self, tmp_path, capsys, tools_present):
        """A mis-typed glob should cost ten seconds, not a wasted weekend."""
        for sid in ("sub-01", "sub-02"):
            leaf = tmp_path / sid / "ses-1" / "dwi"
            leaf.mkdir(parents=True)
            for ext in ("nii.gz", "bvec", "bval"):
                (leaf / f"dwi.{ext}").write_bytes(b"")

        code = run_verb.execute(
            _parse(
                "--subjects",
                str(tmp_path / "sub-01" / "ses-1" / "dwi"),
                "--subjects",
                str(tmp_path / "sub-02" / "ses-1" / "dwi"),
                "--output",
                str(tmp_path / "out"),
                "--dry-run",
            )
        )

        assert code == EXIT_USAGE
        assert "'dwi'" in capsys.readouterr().err

    def test_reports_the_resume_split(self, cohort, tmp_path, tools_present, capsys):
        output = tmp_path / "out"
        args = _parse("--subjects", str(cohort), "--output", str(output))
        config = run_verb.build_config(args)
        write_completion_marker(
            output / "sub-01",
            CompletionMarker("sub-01", "completed", protocol_hash(config)),
        )

        run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(output), "--dry-run", "--resume")
        )

        assert "1 already complete, 1 to process" in capsys.readouterr().out


class TestResume:
    """The marker's protocol hash decides, so a changed protocol reprocesses all."""

    def _configured(self, cohort, output, *extra):
        args = _parse("--subjects", str(cohort), "--output", str(output), *extra)
        return args, run_verb.build_config(args)

    def test_matching_hash_is_skipped(self, cohort, tmp_path):
        output = tmp_path / "out"
        args, config = self._configured(cohort, output)
        write_completion_marker(
            output / "sub-01", CompletionMarker("sub-01", "completed", protocol_hash(config))
        )

        done, todo = run_verb.partition_for_resume(run_verb.resolve_subjects(args), config)

        assert [s.subject_id for s in done] == ["sub-01"]
        assert [s.subject_id for s in todo] == ["sub-02"]

    def test_mismatched_hash_reprocesses(self, cohort, tmp_path):
        output = tmp_path / "out"
        args, config = self._configured(cohort, output)
        write_completion_marker(
            output / "sub-01", CompletionMarker("sub-01", "completed", "a-different-protocol")
        )

        done, todo = run_verb.partition_for_resume(run_verb.resolve_subjects(args), config)

        assert done == []
        assert len(todo) == 2

    def test_absent_marker_reprocesses(self, cohort, tmp_path):
        args, config = self._configured(cohort, tmp_path / "out")

        done, todo = run_verb.partition_for_resume(run_verb.resolve_subjects(args), config)

        assert done == []
        assert len(todo) == 2

    def test_resumed_subjects_are_seeded_as_results(self, cohort, tmp_path):
        """
        Otherwise a run resumed at 180 of 200 would write a 20-row cohort CSV --
        worse than the recomputation resume exists to avoid.
        """
        output = tmp_path / "out"
        args, config = self._configured(cohort, output)
        write_completion_marker(
            output / "sub-01",
            CompletionMarker(
                "sub-01",
                "completed",
                protocol_hash(config),
                alps_by_shape={"rois": {"alps_lab_bilateral": 1.4, "alps_method": "ALPS-LAB"}},
            ),
        )

        done, _ = run_verb.partition_for_resume(run_verb.resolve_subjects(args), config)
        rows = run_verb._results_from_markers(done, config)

        assert len(rows) == 1
        assert rows[0].subject_id == "sub-01"
        assert rows[0].status == "completed"
        assert rows[0].alps_lab_bilateral == 1.4

    def test_nothing_to_do_exits_clean(self, cohort, tmp_path, tools_present, capsys):
        output = tmp_path / "out"
        args, config = self._configured(cohort, output)
        for sid in ("sub-01", "sub-02"):
            write_completion_marker(
                output / sid, CompletionMarker(sid, "completed", protocol_hash(config))
            )

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(output), "--resume")
        )

        assert code == EXIT_OK
        assert "Nothing to do" in capsys.readouterr().out


class _FakePipelineRunner:
    """Succeeds (or fails) without touching a toolchain."""

    fail_ids: set = set()

    def __init__(self, state, progress_callback=None):
        self.state = state
        self.cancelled = False

    def run_full_pipeline(self):
        if self.state.output_prefix in type(self).fail_ids:
            return False
        values = {
            "method": "ALPS-LAB",
            "LAB_ALPS_left": 1.38,
            "LAB_ALPS_right": 1.42,
            "LAB_ALPS_bilateral": 1.4,
        }
        self.state.alps_results = values
        self.state.alps_results_by_shape = {"rois": values}
        return True


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Install a fake PipelineRunner; returns a setter for which ids fail."""

    def _install(*fail_ids):
        class Runner(_FakePipelineRunner):
            pass

        Runner.fail_ids = set(fail_ids)
        monkeypatch.setattr("dti_alps.processing.pipeline.PipelineRunner", Runner, raising=False)

    return _install


class TestEndToEnd:
    """The verb, driven all the way through, with the pipeline faked."""

    def test_all_success_exits_zero_and_writes_the_csv(
        self, cohort, tmp_path, tools_present, fake_pipeline
    ):
        fake_pipeline()
        output = tmp_path / "out"

        code = run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        assert code == EXIT_OK
        assert (output / "alps_results.csv").exists()

    def test_partial_failure_exits_one(self, cohort, tmp_path, tools_present, fake_pipeline):
        fake_pipeline("sub-02")
        output = tmp_path / "out"

        code = run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        assert code == EXIT_FAILURES
        # The CSV is still written -- the subjects that worked keep their numbers.
        assert (output / "alps_results.csv").exists()

    def test_a_run_leaves_a_log_file(self, cohort, tmp_path, tools_present, fake_pipeline):
        """User story 12: an unattended batch leaves the record the GUI would."""
        fake_pipeline()
        output = tmp_path / "out"

        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        logs = list(output.glob("dti_alps_*.log"))
        assert len(logs) == 1
        assert "sub-01" in logs[0].read_text()

    def test_completion_markers_are_written(self, cohort, tmp_path, tools_present, fake_pipeline):
        fake_pipeline()
        output = tmp_path / "out"

        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        marker = json.loads((output / "sub-01" / "alps_result.json").read_text())
        assert marker["status"] == "completed"

    def test_a_second_resume_run_skips_everything(
        self, cohort, tmp_path, tools_present, fake_pipeline, capsys
    ):
        fake_pipeline()
        output = tmp_path / "out"
        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))
        capsys.readouterr()

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(output), "--resume")
        )

        assert code == EXIT_OK
        assert "Nothing to do" in capsys.readouterr().out

    def test_resume_after_an_edited_protocol_reprocesses(
        self, cohort, tmp_path, tools_present, fake_pipeline, capsys
    ):
        """A cohort can never end up half-processed one way and half the other."""
        fake_pipeline()
        output = tmp_path / "out"
        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))
        capsys.readouterr()

        code = run_verb.execute(
            _parse(
                "--subjects",
                str(cohort),
                "--output",
                str(output),
                "--resume",
                "--fa-threshold",
                "0.4",
            )
        )

        assert code == EXIT_OK
        assert "Nothing to do" not in capsys.readouterr().out

    def test_fail_fast_stops_after_the_first_failure(
        self, cohort, tmp_path, tools_present, fake_pipeline
    ):
        fake_pipeline("sub-01")
        output = tmp_path / "out"

        code = run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(output), "--fail-fast")
        )

        assert code == EXIT_FAILURES
        # sub-02 was never processed -- it is recorded as skipped, not completed.
        assert not (output / "sub-02" / "alps_result.json").exists()

    def test_without_fail_fast_the_batch_continues(
        self, cohort, tmp_path, tools_present, fake_pipeline
    ):
        fake_pipeline("sub-01")
        output = tmp_path / "out"

        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        assert (output / "sub-02" / "alps_result.json").exists()

    def test_invalid_subject_files_are_a_usage_error(self, tmp_path, tools_present, capsys):
        folder = tmp_path / "cohort" / "sub-01"
        folder.mkdir(parents=True)
        (folder / "dwi.nii.gz").write_bytes(b"")  # no bvec/bval

        code = run_verb.execute(
            _parse("--subjects", str(tmp_path / "cohort"), "--output", str(tmp_path / "out"))
        )

        assert code == EXIT_USAGE


class TestQuiet:
    """Verbose by default -- an eddy failure is diagnosed by reading eddy."""

    def test_raw_tool_output_is_shown_by_default(
        self, cohort, tmp_path, tools_present, fake_pipeline, capsys
    ):
        fake_pipeline()
        run_verb.execute(_parse("--subjects", str(cohort), "--output", str(tmp_path / "out")))

        assert "Starting batch processing" in capsys.readouterr().out

    def test_quiet_keeps_the_structural_lines(
        self, cohort, tmp_path, tools_present, fake_pipeline, capsys
    ):
        fake_pipeline()
        run_verb.execute(
            _parse("--subjects", str(cohort), "--output", str(tmp_path / "out"), "--quiet")
        )

        out = capsys.readouterr().out
        assert "Processing 2 subject(s)" in out
        assert "sub-01" in out
        assert "Starting batch processing" not in out


class TestSigint:
    """Ctrl-C cancels cleanly; the CSV still lands with what finished."""

    def test_cancel_writes_the_csv_and_exits_130(
        self, cohort, tmp_path, tools_present, fake_pipeline, monkeypatch
    ):
        fake_pipeline()
        output = tmp_path / "out"

        # Fire the handler the moment the first subject completes, exactly as a
        # Ctrl-C between subjects would.
        real_install = run_verb._install_sigint_handler
        captured = {}

        def install(runner):
            seen = real_install(runner)
            captured["runner"] = runner
            original = runner._process_single_subject

            def step(*a, **k):
                result = original(*a, **k)
                os.kill(os.getpid(), 2)
                return result

            runner._process_single_subject = step
            return seen

        monkeypatch.setattr(run_verb, "_install_sigint_handler", install)

        code = run_verb.execute(_parse("--subjects", str(cohort), "--output", str(output)))

        assert code == EXIT_INTERRUPTED
        assert (output / "alps_results.csv").exists()
        assert captured["runner"].cancelled is True


class TestEquivalentCommand:
    """
    The GUI's copyable line. Rendered by the CLI so the flag spellings have one
    home -- and asserted to parse back through the real grammar, which is what
    actually makes drift impossible rather than merely unlikely.
    """

    def test_minimal_form(self):
        assert run_verb.equivalent_command(["/data/cohort"], "/data/out") == (
            "dti-alps run --subjects /data/cohort --output /data/out"
        )

    def test_repeated_subjects(self):
        command = run_verb.equivalent_command(["/a", "/b"], "/out")

        assert command.count("--subjects") == 2
        assert "/a" in command and "/b" in command

    def test_config_is_included_when_exported(self):
        command = run_verb.equivalent_command(["/a"], "/out", "/study/protocol.json")

        assert "--config /study/protocol.json" in command

    def test_id_depth_is_included_only_when_non_default(self):
        assert "--id-depth" not in run_verb.equivalent_command(["/a"], "/out")
        assert "--id-depth 3" in run_verb.equivalent_command(["/a"], "/out", id_depth=3)

    def test_paths_with_spaces_are_quoted(self):
        command = run_verb.equivalent_command(["/data/my cohort"], "/out")

        assert "'/data/my cohort'" in command

    def test_placeholders_when_the_form_is_incomplete(self):
        """An empty form still renders something intelligible rather than a stub."""
        command = run_verb.equivalent_command([], "")

        assert command == "dti-alps run --subjects <SUBJECT_FOLDER> --output <OUTPUT_DIR>"

    def test_placeholders_are_not_shell_quoted(self):
        """Quoted, they read as a literal path somebody forgot to fill in."""
        command = run_verb.equivalent_command([], "")

        assert "'<" not in command

    @pytest.mark.parametrize(
        "subjects,output,config,depth",
        [
            (["/data/cohort"], "/data/out", "", 1),
            (["/a", "/b"], "/out", "/p.json", 3),
            (["/data/my cohort"], "/my out", "/my protocol.json", 2),
        ],
    )
    def test_the_rendered_command_parses_back(self, subjects, output, config, depth):
        """The drift guard: what the GUI shows is a command that actually works."""
        import shlex

        rendered = run_verb.equivalent_command(subjects, output, config, depth)
        argv = shlex.split(rendered)

        assert argv[:2] == ["dti-alps", "run"]
        args = build_parser().parse_args(argv[1:])

        assert args.subjects == subjects
        assert args.output == output
        assert args.id_depth == depth
        assert (args.config or "") == config
        assert run_verb.validate_data_flags(args) is None
