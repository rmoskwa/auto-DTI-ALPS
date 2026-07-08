"""
Fake-driven tests for the FSL registration backend across the ToolRunner seam.

These prove the fsl conversion (strangler step 3): the runner threaded into
``FSLRegistration`` reaches every FSL command, so a single fake injected where
the pipeline injects it (see ``test_run_registration_forwards_runner_to_backend``
in test_pipeline_seam.py) captures registration / ROI-placement commands too.

As with the pipeline-seam tests, no FSL is installed and nothing is written to
disk by the seam. ``register()`` itself is not driven end-to-end here: it has
real ``check_available()`` / template / file-existence gates and nibabel loads
between commands, and the fake produces no ``.nii.gz`` for those gates to find --
asserting a full real run is the integration smoke's job, not the fake's. So we
exercise the command-issuing helpers directly, which is where the seam lives.
"""

from dti_alps.processing.b0_extraction import (
    apply_mask_to_image,
    create_brain_mask_from_dwi,
)
from dti_alps.processing.registration.fsl import FSLRegistration
from dti_alps.processing.tool_runner import SubprocessToolRunner
from tests.fakes import FakeToolRunner

# --- Runner threading -------------------------------------------------------


def test_fsl_backend_threads_runner():
    # The exact call the pipeline makes -- FSLRegistration(runner=...) -- hands
    # the backend the injected runner, so its FSL commands cross the same seam.
    fake = FakeToolRunner()
    backend = FSLRegistration(runner=fake)
    assert backend.runner is fake


def test_fsl_backend_without_runner_defaults_to_real():
    # Production construction (no runner) keeps a real subprocess-backed runner,
    # so the live pipeline path is unchanged.
    backend = FSLRegistration()
    assert isinstance(backend.runner, SubprocessToolRunner)


# --- FSL command routing (flirt / fnirt / invwarp / applywarp) --------------


def test_fsl_command_routes_through_injected_runner_and_streams():
    # All four FSL commands route through _run_fsl_command; a scripted output
    # line reaches the log callback and a 0 exit yields success.
    fake = FakeToolRunner().on(lambda c: c[0].endswith("flirt"), lines=["flirt: 50%"])
    backend = FSLRegistration(runner=fake)

    logs: list[str] = []
    cmd = ["/fsl/bin/flirt", "-in", "fa_brain", "-ref", "jhu"]
    assert backend._run_fsl_command(cmd, logs.append) is True
    assert cmd in fake.calls
    assert "flirt: 50%" in logs


def test_fsl_command_nonzero_returncode_reports_failure():
    # A non-zero exit scripted by predicate makes the helper return False.
    fake = FakeToolRunner().on(lambda c: c[0].endswith("fnirt"), returncode=1)
    backend = FSLRegistration(runner=fake)
    assert backend._run_fsl_command(["/fsl/bin/fnirt", "--in=fa"], lambda _l: None) is False


# --- Live b0_extraction helpers (Decision 3 returncode rewrite) -------------


def test_create_brain_mask_routes_dwi2mask_through_runner(tmp_path):
    # The live helper issues dwi2mask through the injected runner; a non-zero
    # exit becomes a (False, message) result via the returncode rewrite -- no
    # CalledProcessError crosses the seam.
    fake = FakeToolRunner().on(lambda c: c[0] == "dwi2mask", returncode=1, lines=["boom"])
    ok, msg = create_brain_mask_from_dwi(
        dwi_path="/in/dwi.nii.gz",
        bvecs_path="/in/dwi.bvec",
        bvals_path="/in/dwi.bval",
        output_mask_path=str(tmp_path / "mask.nii.gz"),
        runner=fake,
    )
    assert ok is False
    assert "dwi2mask failed" in msg
    assert any(c[0] == "dwi2mask" for c in fake.calls)


def test_apply_mask_routes_fslmaths_through_runner(tmp_path):
    fake = FakeToolRunner().on(lambda c: c[0] == "fslmaths", returncode=1, lines=["boom"])
    ok, msg = apply_mask_to_image(
        input_path="/in/fa.nii.gz",
        mask_path="/in/mask.nii.gz",
        output_path=str(tmp_path / "fa_brain.nii.gz"),
        runner=fake,
    )
    assert ok is False
    assert "fslmaths failed" in msg
    assert any(c[0] == "fslmaths" for c in fake.calls)


def test_missing_binary_does_not_raise_across_b0_helper(tmp_path):
    # A missing binary surfaces as returncode 127 with an explanatory output
    # (the real runner catches it); the helper reports it as a normal failure
    # rather than raising. Here the fake stands in for that 127 outcome.
    fake = FakeToolRunner().on(
        lambda c: c[0] == "dwi2mask",
        returncode=127,
        lines=["Command not found: dwi2mask"],
    )
    ok, msg = create_brain_mask_from_dwi(
        dwi_path="/in/dwi.nii.gz",
        bvecs_path="/in/dwi.bvec",
        bvals_path="/in/dwi.bval",
        output_mask_path=str(tmp_path / "mask.nii.gz"),
        runner=fake,
    )
    assert ok is False
    assert "Command not found: dwi2mask" in msg
