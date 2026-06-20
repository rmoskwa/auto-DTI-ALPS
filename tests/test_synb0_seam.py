"""
Fake-driven tests for the synB0-DISCO backend across the ToolRunner seam.

synB0 is dormant in production -- nothing constructs ``Synb0Backend`` or calls
``run_topup_eddy`` (the live synB0 route uses pre-computed external outputs via
``PipelineRunner.run_eddy_with_synb0``, already on the seam). This conversion
(strangler step 4, plus the 3 dormant ``extract_and_average_b0`` sites of step 5)
still routes every synB0 command through an injected runner, so the dormant
backend is testable the same way as the live pipeline: inject a ``FakeToolRunner``
directly -- there is no entry to thread it down from.

As with the other seam tests, no toolchain is installed and the fake writes no
files. Several backend helpers have file-existence gates between commands (a
``.mat`` or output ``.nii.gz`` the fake cannot produce); those helpers are driven
only as far as the gate, and the command argv issued before it is asserted from
``fake.calls`` -- which is where the seam lives. Asserting a full real run is the
integration smoke's job.
"""

from pathlib import Path

import nibabel as nib
import numpy as np

from dti_alps.processing.b0_extraction import extract_and_average_b0
from dti_alps.processing.state import PipelineState
from dti_alps.processing.synb0.backend import Synb0Backend, run_topup_eddy
from dti_alps.processing.tool_runner import SubprocessToolRunner
from tests.fakes import FakeToolRunner


def _progs(fake) -> list[str]:
    """Program names (argv[0]) of every recorded call, in order."""
    return [c[0] for c in fake.calls]


def _noop(_line: str) -> None:
    pass


# --- Constructor threading --------------------------------------------------


def test_backend_with_runner_stores_it():
    fake = FakeToolRunner()
    assert Synb0Backend(runner=fake).runner is fake


def test_backend_without_runner_defaults_to_real():
    # Production construction (no runner) keeps a real subprocess-backed runner.
    assert isinstance(Synb0Backend().runner, SubprocessToolRunner)


# --- _prepare_t1 (5 commands, gate-free once T1 exists) ---------------------


def _state_with_t1(tmp_path) -> PipelineState:
    # _prepare_t1 only checks that the T1 file exists, never its contents.
    t1 = tmp_path / "t1.nii.gz"
    t1.write_bytes(b"")
    state = PipelineState()
    state.t1_path = str(t1)
    return state


def test_prepare_t1_routes_all_five_commands(tmp_path):
    state = _state_with_t1(tmp_path)
    fake = FakeToolRunner()
    backend = Synb0Backend(runner=fake)

    result = backend._prepare_t1(state, tmp_path, _noop)

    assert result["success"] is True
    # convert -> bias-correct -> normalize -> convert back -> brain-extract
    assert _progs(fake) == [
        "mri_convert",
        "mri_nu_correct.mni",
        "mri_normalize",
        "mri_convert",
        "bet",
    ]


def test_prepare_t1_failure_reports_merged_output(tmp_path):
    state = _state_with_t1(tmp_path)
    fake = FakeToolRunner().on(lambda c: c[0] == "mri_convert", returncode=1, lines=["boom"])
    backend = Synb0Backend(runner=fake)

    result = backend._prepare_t1(state, tmp_path, _noop)

    assert result["success"] is False
    assert "mri_convert failed" in result["error"]
    assert "boom" in result["error"]  # ToolResult.output, the merged stream


def test_prepare_t1_missing_binary_does_not_raise(tmp_path):
    # A missing binary surfaces as returncode 127 with an explanatory output;
    # the helper reports it as a normal failure rather than raising.
    state = _state_with_t1(tmp_path)
    fake = FakeToolRunner().on(
        lambda c: c[0] == "mri_convert",
        returncode=127,
        lines=["Command not found: mri_convert"],
    )
    backend = Synb0Backend(runner=fake)

    result = backend._prepare_t1(state, tmp_path, _noop)

    assert result["success"] is False
    assert "Command not found: mri_convert" in result["error"]


# --- _register_b0_to_t1 (epi_reg, .mat gate, c3d_affine_tool) ---------------


def test_register_b0_to_t1_epi_reg_failure(tmp_path):
    fake = FakeToolRunner().on(lambda c: c[0] == "epi_reg", returncode=1, lines=["nope"])
    backend = Synb0Backend(runner=fake)

    result = backend._register_b0_to_t1(
        "/b0.nii.gz", "/t1.nii.gz", "/t1_brain.nii.gz", tmp_path, _noop
    )

    assert result["success"] is False
    assert "epi_reg failed" in result["error"]
    assert any(c[0] == "epi_reg" for c in fake.calls)


def test_register_b0_to_t1_routes_both_when_mat_exists(tmp_path):
    # epi_reg's .mat output gates c3d_affine_tool; pre-create it so the helper
    # reaches the second command and both argv can be asserted.
    (tmp_path / "b0_to_T1.mat").write_bytes(b"")
    fake = FakeToolRunner()
    backend = Synb0Backend(runner=fake)

    result = backend._register_b0_to_t1(
        "/b0.nii.gz", "/t1.nii.gz", "/t1_brain.nii.gz", tmp_path, _noop
    )

    assert result["success"] is True
    assert _progs(fake) == ["epi_reg", "c3d_affine_tool"]


# --- _register_t1_to_mni (antsRegistrationSyNQuick.sh) ----------------------


def test_register_t1_to_mni_failure(tmp_path):
    fake = FakeToolRunner().on(
        lambda c: c[0] == "antsRegistrationSyNQuick.sh", returncode=1, lines=["err"]
    )
    backend = Synb0Backend(runner=fake)

    result = backend._register_t1_to_mni("/t1.nii.gz", tmp_path, _noop)

    assert result["success"] is False
    assert "ANTs registration failed" in result["error"]
    assert any(c[0] == "antsRegistrationSyNQuick.sh" for c in fake.calls)


# --- _transform_to_atlas / _transform_to_native (antsApplyTransforms) -------


def test_transform_to_atlas_routes_two_apply_transforms(tmp_path):
    fake = FakeToolRunner()
    backend = Synb0Backend(runner=fake)

    result = backend._transform_to_atlas(
        "/b0.nii.gz", "/t1.nii.gz", "/b0_to_t1.txt", "/t1_to_mni.mat", tmp_path, _noop
    )

    assert result["success"] is True
    assert _progs(fake) == ["antsApplyTransforms", "antsApplyTransforms"]


def test_transform_to_atlas_b0_failure_reported(tmp_path):
    # The b0 transform is issued first; failing antsApplyTransforms aborts there.
    fake = FakeToolRunner().on(lambda c: c[0] == "antsApplyTransforms", returncode=1, lines=["x"])
    backend = Synb0Backend(runner=fake)

    result = backend._transform_to_atlas(
        "/b0.nii.gz", "/t1.nii.gz", "/b0_to_t1.txt", "/t1_to_mni.mat", tmp_path, _noop
    )

    assert result["success"] is False
    assert "antsApplyTransforms (b0) failed" in result["error"]


def test_transform_to_native_routes_inverted_apply_transforms(tmp_path):
    fake = FakeToolRunner()
    backend = Synb0Backend(runner=fake)

    result = backend._transform_to_native(
        "/synb0_atlas.nii.gz", "/b0.nii.gz", "/b0_to_t1.txt", "/t1_to_mni.mat", tmp_path, _noop
    )

    assert result["success"] is True
    call = next(c for c in fake.calls if c[0] == "antsApplyTransforms")
    # both transforms applied inverted -> bracketed ",1]" forms in the argv
    assert any(arg.endswith(",1]") for arg in call)


# --- _prepare_topup_inputs (fslmaths smooth + fslmerge) ---------------------


def test_prepare_topup_inputs_routes_commands_and_writes_acqparams(tmp_path):
    state = PipelineState(output_dir=str(tmp_path), readout_time=0.05, pe_direction="AP")
    fake = FakeToolRunner()
    backend = Synb0Backend(runner=fake)

    result = backend._prepare_topup_inputs("/b0.nii.gz", "/synb0.nii.gz", state, tmp_path, _noop)

    assert result["success"] is True
    assert _progs(fake) == ["fslmaths", "fslmerge"]
    # acqparams is written by the helper (no command) with the AP phase-encode row
    assert "0 -1 0" in Path(result["acqparams_path"]).read_text()
    assert state.synb0_b0_pair_path == result["b0_pair_path"]


# --- run_topup_eddy (module-level function; topup + eddy) -------------------


def _topup_eddy_gates(tmp_path):
    """Create the b0-pair / acqparams gate files run_topup_eddy checks up front."""
    synb0_dir = tmp_path / "synb0_work"
    synb0_dir.mkdir()
    (synb0_dir / "b0_pair.nii.gz").write_bytes(b"")
    (synb0_dir / "acqparams.txt").write_text("0 -1 0 0.05\n0 -1 0 0\n")
    return synb0_dir


def test_run_topup_eddy_topup_failure(tmp_path):
    _topup_eddy_gates(tmp_path)
    state = PipelineState(output_dir=str(tmp_path))
    fake = FakeToolRunner().on(lambda c: c[0] == "topup", returncode=1, lines=["topup boom"])

    result = run_topup_eddy(state, _noop, runner=fake)

    assert result.success is False
    assert "topup failed" in result.error_message
    assert any(c[0] == "topup" for c in fake.calls)


def test_run_topup_eddy_routes_eddy_command(tmp_path):
    # Drive the function all the way to eddy. The brain-mask and nibabel gates in
    # between need real files; the fake supplies none, so the run still ends in
    # failure at the eddy-output gate -- but eddy's argv is recorded en route.
    synb0_dir = _topup_eddy_gates(tmp_path)
    (synb0_dir / "brain_mask.nii.gz").write_bytes(b"")  # create_brain_mask output gate
    dwi = tmp_path / "dwi.nii.gz"
    nib.save(nib.Nifti1Image(np.zeros((2, 2, 2, 3), dtype=np.float32), np.eye(4)), str(dwi))

    state = PipelineState(
        dwi_path=str(dwi),
        bvecs_path=str(tmp_path / "dwi.bvec"),
        bvals_path=str(tmp_path / "dwi.bval"),
        output_dir=str(tmp_path),
    )
    state.eddy_options = {}  # dormant code mutates eddy_options as a dict

    fake = FakeToolRunner()
    result = run_topup_eddy(state, _noop, runner=fake)

    # topup -> dwi2mask (brain mask) -> eddy all crossed the single injected seam.
    assert _progs(fake) == ["topup", "dwi2mask", "eddy"]
    assert result.success is False  # stopped at the eddy-output existence gate


# --- extract_and_average_b0 (step 5: the 3 dormant b0-extraction sites) ------


def _bvals(tmp_path, content: str) -> str:
    p = tmp_path / "dwi.bval"
    p.write_text(content)
    return str(p)


def test_extract_b0_single_routes_dwiextract_and_mrconvert(tmp_path):
    # One b0 volume -> dwiextract then mrconvert (no averaging).
    fake = FakeToolRunner()
    extract_and_average_b0(
        dwi_path="/dwi.nii.gz",
        bvecs_path="/dwi.bvec",
        bvals_path=_bvals(tmp_path, "0 1000 1000"),
        output_path=str(tmp_path / "b0.nii.gz"),
        runner=fake,
    )
    assert _progs(fake) == ["dwiextract", "mrconvert"]


def test_extract_b0_multiple_routes_dwiextract_and_mrmath(tmp_path):
    # Two b0 volumes -> dwiextract then mrmath (average).
    fake = FakeToolRunner()
    extract_and_average_b0(
        dwi_path="/dwi.nii.gz",
        bvecs_path="/dwi.bvec",
        bvals_path=_bvals(tmp_path, "0 0 1000"),
        output_path=str(tmp_path / "b0.nii.gz"),
        runner=fake,
    )
    assert _progs(fake) == ["dwiextract", "mrmath"]


def test_extract_b0_dwiextract_failure_reports_merged_output(tmp_path):
    fake = FakeToolRunner().on(lambda c: c[0] == "dwiextract", returncode=1, lines=["boom"])
    result = extract_and_average_b0(
        dwi_path="/dwi.nii.gz",
        bvecs_path="/dwi.bvec",
        bvals_path=_bvals(tmp_path, "0 1000"),
        output_path=str(tmp_path / "b0.nii.gz"),
        runner=fake,
    )
    assert result.success is False
    assert "dwiextract failed" in result.error_message
    assert "boom" in result.error_message


def test_extract_b0_missing_binary_does_not_raise(tmp_path):
    fake = FakeToolRunner().on(
        lambda c: c[0] == "dwiextract",
        returncode=127,
        lines=["Command not found: dwiextract"],
    )
    result = extract_and_average_b0(
        dwi_path="/dwi.nii.gz",
        bvecs_path="/dwi.bvec",
        bvals_path=_bvals(tmp_path, "0 1000"),
        output_path=str(tmp_path / "b0.nii.gz"),
        runner=fake,
    )
    assert result.success is False
    assert "Command not found: dwiextract" in result.error_message
