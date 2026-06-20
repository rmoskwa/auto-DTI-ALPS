"""
Fake-driven tests for PipelineRunner across the ToolRunner seam.

These assert *external behaviour at the seam* -- the argv each stage issues, and
how a stage reacts to an exit code / missing binary / user cancel -- with no
toolchain installed and nothing written to disk. They never patch ``subprocess``
and never touch the filesystem.

Scope: the pipeline module issues many commands directly via ``_run_command``
(denoise, degibbs, preproc, DTI fitting, eddy/synB0). Since the fsl conversion
landed, the pipeline also forwards its runner into the registration backend, so
the backend's FSL commands cross the same seam -- asserted here by
``test_run_registration_forwards_runner_to_backend`` and exercised end-to-end at
the backend level in ``tests/test_registration_seam.py``. The synB0 and
reanalysis entries still own their own execution until those conversions land.
"""

from dti_alps.processing import registration
from dti_alps.processing.pipeline import PipelineRunner, PipelineState
from tests.fakes import FakeToolRunner


def _make_state(tmp_path, **overrides) -> PipelineState:
    """Build a minimal, on-disk-free PipelineState with output paths resolved."""
    state = PipelineState(
        dwi_path="/in/dwi.nii.gz",
        bvecs_path="/in/dwi.bvec",
        bvals_path="/in/dwi.bval",
        output_dir=str(tmp_path),
        output_prefix="sub",
        **overrides,
    )
    state.setup_output_paths()
    return state


def _runner(state, fake):
    """PipelineRunner wired to a fake, collecting log lines into ``.logs``."""
    logs: list[str] = []

    def progress(msg_type, data):
        if msg_type == "log":
            logs.append(data)

    runner = PipelineRunner(state, progress_callback=progress, runner=fake)
    runner.logs = logs  # type: ignore[attr-defined]
    return runner


def _find_call(fake, tool):
    """Return the single recorded argv whose program is ``tool``."""
    matches = [c for c in fake.calls if c and c[0] == tool]
    assert len(matches) == 1, f"expected exactly one {tool} call, got {matches}"
    return matches[0]


# --- Command construction ---------------------------------------------------


def test_denoising_issues_dwidenoise_with_input_and_output(tmp_path):
    state = _make_state(tmp_path)
    fake = FakeToolRunner()
    runner = _runner(state, fake)

    assert runner.run_denoising() is True

    call = _find_call(fake, "dwidenoise")
    assert state.dwi_path in call
    assert state.denoised_dwi_path in call


def test_preprocessing_argv_has_pe_dir_and_rpe_pair(tmp_path):
    # User story 1: a given state must drive dwifslpreproc with -pe_dir AP and
    # an -rpe_pair reverse-PE configuration.
    state = _make_state(
        tmp_path,
        pe_direction="AP",
        rpe_scheme="pair",
        reverse_pe_path="/in/reverse_pe.nii.gz",
    )
    fake = FakeToolRunner()
    runner = _runner(state, fake)

    assert runner.run_preprocessing() is True

    call = _find_call(fake, "dwifslpreproc")
    assert "-pe_dir" in call
    assert call[call.index("-pe_dir") + 1] == "AP"
    assert "-rpe_pair" in call
    assert "-se_epi" in call
    assert "/in/reverse_pe.nii.gz" in call


def test_phase_encode_direction_changes_argv(tmp_path):
    # User story 2: AP vs PA must change the argv the pipeline issues.
    def pe_value(direction):
        state = _make_state(tmp_path, pe_direction=direction, rpe_scheme="none")
        fake = FakeToolRunner()
        assert _runner(state, fake).run_preprocessing() is True
        call = _find_call(fake, "dwifslpreproc")
        return call[call.index("-pe_dir") + 1]

    assert pe_value("AP") == "AP"
    assert pe_value("PA") == "PA"
    assert pe_value("AP") != pe_value("PA")


# --- Control flow -----------------------------------------------------------


def test_nonzero_exit_aborts_denoise(tmp_path):
    # User story 3: inject a non-zero exit at denoising; the stage must fail.
    state = _make_state(tmp_path)
    fake = FakeToolRunner().on(lambda c: c[0] == "dwidenoise", returncode=1)
    runner = _runner(state, fake)

    assert runner.run_denoising() is False
    assert any("exit code 1" in m for m in runner.logs)


def test_missing_binary_reports_failure(tmp_path):
    # User story 4: a "binary not found" outcome must be reported cleanly.
    state = _make_state(tmp_path)
    fake = FakeToolRunner().on(
        lambda c: c[0] == "dwidenoise",
        returncode=127,
        lines=["Command not found: dwidenoise"],
    )
    runner = _runner(state, fake)

    assert runner.run_denoising() is False
    assert any("Command not found: dwidenoise" in m for m in runner.logs)


def test_user_cancel_mid_stage_reports_cancelled(tmp_path):
    # User story 5: a user cancel must make the stage report as cancelled, using
    # the caller's own flag (not a special result field) to disambiguate.
    state = _make_state(tmp_path)
    fake = FakeToolRunner().on(lambda c: True, cancel=True)
    runner = _runner(state, fake)
    runner.cancelled = True  # as if the user clicked cancel during the stage

    assert runner.run_denoising() is False
    assert any("cancelled" in m.lower() for m in runner.logs)


def test_streamed_lines_reach_log_callback(tmp_path):
    # User story 9: the fake drives the streaming callback; the stage's log
    # handling must surface those lines.
    state = _make_state(tmp_path)
    fake = FakeToolRunner().on(
        lambda c: c[0] == "dwidenoise", lines=["denoise 10%", "denoise 100%"]
    )
    runner = _runner(state, fake)

    assert runner.run_denoising() is True
    assert "denoise 10%" in runner.logs
    assert "denoise 100%" in runner.logs


def test_single_fake_captures_every_pipeline_issued_command(tmp_path):
    # User stories 6/7 (scoped to pipeline-issued commands): one injection point
    # records the argv of every stage the pipeline drives directly.
    state = _make_state(tmp_path, alps_method="ALPS-LAB")  # one tensor2metric pass
    fake = FakeToolRunner()
    runner = _runner(state, fake)

    assert runner.run_denoising() is True
    assert runner.run_degibbs() is True
    assert runner.run_preprocessing() is True
    assert runner.run_dti_fitting() is True

    issued = {c[0] for c in fake.calls}
    assert {"dwidenoise", "mrdegibbs", "dwifslpreproc", "dwi2tensor", "tensor2metric"} <= issued


def test_failure_scripted_by_predicate_not_position(tmp_path):
    # User story 8: script the failure by what the command is, not its index in
    # the chain. DTI fitting issues dwi2tensor then tensor2metric; failing the
    # FA-extraction command by predicate must abort the stage.
    state = _make_state(tmp_path, alps_method="ALPS-LAB")
    fake = FakeToolRunner().on(lambda c: "-fa" in c, returncode=1)
    runner = _runner(state, fake)

    assert runner.run_dti_fitting() is False
    # dwi2tensor ran and succeeded; tensor2metric was reached and failed.
    assert [c[0] for c in fake.calls] == ["dwi2tensor", "tensor2metric"]


# --- Registration seam (closes deviation #2 for fsl) ------------------------


def test_run_registration_forwards_runner_to_backend(tmp_path, monkeypatch):
    # Deviation #2 closure (fsl): the single fake injected at the pipeline must
    # reach the registration backend, so the backend's FSL commands cross the
    # same seam. We assert the pipeline forwards *its own* runner to the
    # get_backend factory -- the seam-crossing call. (run_registration then fails
    # at the real check_available() gate because no FSL is installed; that is
    # expected and not what this test is about. The backend actually routing FSL
    # commands through that runner is covered in tests/test_registration_seam.py.)
    state = _make_state(tmp_path)
    fake = FakeToolRunner()
    pipeline = _runner(state, fake)

    captured: dict[str, object] = {}
    real_get_backend = registration.get_backend

    def spy(name, runner=None):
        captured["runner"] = runner
        return real_get_backend(name, runner=runner)

    monkeypatch.setattr(registration, "get_backend", spy)

    pipeline.run_registration()

    assert captured["runner"] is fake
