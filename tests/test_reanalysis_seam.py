"""
Fake-driven tests for the reanalysis CLI module across the ToolRunner seam.

Strangler step 6: the lone ``applywarp`` ``subprocess.run`` in ``reanalyze_subject``
now goes through an injected runner, with the Decision-3 returncode rewrite -- a
non-zero exit (including a missing binary at 127) becomes a failed
``ReanalysisResult``, not a raised ``CalledProcessError``. ``run_reanalysis``
creates one runner and threads it into every subject.

No FSL is installed and the fake writes no files. ``reanalyze_subject`` reaches
the ``applywarp`` only after an FSL-presence gate (``_get_fsl_bin_dir``) and
template/FA gates; those are satisfied with a monkeypatched bin dir + template map
and a real FA volume, so the command is issued through the fake. The first
``applywarp``'s output (``roi_transformed``) is never written by the fake, so the
run then fails at the nibabel load right after -- but the ``applywarp`` argv and
any non-zero handling are asserted from ``fake.calls`` before that, which is where
the seam lives.
"""

import nibabel as nib
import numpy as np
import pytest

import dti_alps.cli.main as cli
from dti_alps.processing import reanalysis
from dti_alps.processing.constants import AdaptiveSearchConfig
from dti_alps.processing.reanalysis import (
    ReanalysisResult,
    ROIShape,
    reanalyze_subject,
    run_reanalysis,
)
from dti_alps.processing.tool_runner import SubprocessToolRunner
from tests.fakes import FakeToolRunner


def _subject(tmp_path):
    """Create a minimal processed-subject layout reanalyze_subject can walk."""
    subject_dir = tmp_path / "sub-01"
    reg_dir = subject_dir / "registration"
    reg_dir.mkdir(parents=True)
    # Inverse warp + tensor are only referenced as argv / globbed, never loaded
    # before applywarp -- empty files satisfy the existence gates.
    (reg_dir / "sub-01_jhu2subject_warp_coef.nii.gz").write_bytes(b"")
    (subject_dir / "sub-01_tensor.nii.gz").write_bytes(b"")
    # FA is loaded (nib.load) for ref shape / voxel size -> needs a real volume.
    nib.save(
        nib.Nifti1Image(np.zeros((4, 4, 4), dtype=np.float32), np.eye(4)),
        str(subject_dir / "sub-01_FA.nii.gz"),
    )
    return subject_dir


def _patch_gates(monkeypatch, tmp_path):
    """Get past the FSL-presence and template gates without FSL installed."""
    monkeypatch.setattr(reanalysis, "_get_fsl_bin_dir", lambda: tmp_path / "fslbin")
    monkeypatch.setattr(
        reanalysis,
        "get_roi_template_paths",
        lambda: {
            "left_proj": "/tpl/left_proj.nii.gz",
            "left_assoc": "/tpl/left_assoc.nii.gz",
            "right_proj": "/tpl/right_proj.nii.gz",
            "right_assoc": "/tpl/right_assoc.nii.gz",
        },
    )


def _reanalyze(subject_dir, fake):
    return reanalyze_subject(
        subject_id="sub-01",
        subject_dir=subject_dir,
        roi_shape=ROIShape(shape_type="sphere", sphere_radius=3.0),
        enable_adaptive=False,
        alps_method="ALPS-LAB",
        fa_threshold=0.2,
        runner=fake,
    )


# --- applywarp routing + failure handling -----------------------------------


def test_reanalyze_subject_routes_applywarp_through_runner(tmp_path, monkeypatch):
    subject_dir = _subject(tmp_path)
    _patch_gates(monkeypatch, tmp_path)
    fake = FakeToolRunner()

    _reanalyze(subject_dir, fake)

    applywarp_calls = [c for c in fake.calls if c and c[0].endswith("applywarp")]
    assert applywarp_calls, "expected an applywarp command to cross the seam"
    cmd = applywarp_calls[0]
    assert any(a.startswith("--ref=") for a in cmd)
    assert any(a.startswith("--warp=") for a in cmd)
    assert "--interp=nn" in cmd


def test_reanalyze_subject_applywarp_failure_reports_merged_output(tmp_path, monkeypatch):
    subject_dir = _subject(tmp_path)
    _patch_gates(monkeypatch, tmp_path)
    fake = FakeToolRunner().on(
        lambda c: c[0].endswith("applywarp"), returncode=1, lines=["applywarp boom"]
    )

    result = _reanalyze(subject_dir, fake)

    assert result.status == "failed"
    assert "FSL applywarp failed" in result.error_message
    assert "applywarp boom" in result.error_message  # ToolResult.output, merged


def test_reanalyze_subject_missing_binary_does_not_raise(tmp_path, monkeypatch):
    # A missing binary surfaces as returncode 127 with an explanatory output; the
    # function reports a failed result rather than raising CalledProcessError.
    subject_dir = _subject(tmp_path)
    _patch_gates(monkeypatch, tmp_path)
    fake = FakeToolRunner().on(
        lambda c: c[0].endswith("applywarp"),
        returncode=127,
        lines=["Command not found: applywarp"],
    )

    result = _reanalyze(subject_dir, fake)

    assert result.status == "failed"
    assert "Command not found: applywarp" in result.error_message


# --- run_reanalysis threads one runner into every subject -------------------


def _spy_subjects(monkeypatch, tmp_path, captured):
    monkeypatch.setattr(
        reanalysis, "discover_processed_subjects", lambda _d: [("sub-01", tmp_path / "sub-01")]
    )
    monkeypatch.setattr(reanalysis, "_write_reanalysis_csv", lambda *a, **k: None)

    def spy(*args, **kwargs):
        captured["runner"] = kwargs.get("runner")
        return ReanalysisResult(subject_id=kwargs["subject_id"], status="completed")

    monkeypatch.setattr(reanalysis, "reanalyze_subject", spy)


def test_run_reanalysis_threads_runner_into_each_subject(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    _spy_subjects(monkeypatch, tmp_path, captured)
    fake = FakeToolRunner()

    run_reanalysis(
        output_dir=str(tmp_path),
        roi_shape=ROIShape(shape_type="sphere", sphere_radius=3.0),
        alps_method="ALPS-LAB",
        runner=fake,
    )

    assert captured["runner"] is fake


def test_run_reanalysis_without_runner_defaults_to_real(tmp_path, monkeypatch):
    captured: dict[str, object] = {}
    _spy_subjects(monkeypatch, tmp_path, captured)

    run_reanalysis(
        output_dir=str(tmp_path),
        roi_shape=ROIShape(shape_type="sphere", sphere_radius=3.0),
        alps_method="ALPS-LAB",
    )

    assert isinstance(captured["runner"], SubprocessToolRunner)


# --- CLI flags: parse, validation, defaults ---------------------------------


def _parse(extra_args):
    """Parse a ``reanalyze`` command line through the real top-level grammar."""
    return cli.build_parser().parse_args(["reanalyze", "/out", "--sphere", "3"] + extra_args)


def test_envelope_flags_parse_to_ints():
    args = _parse(
        [
            "--search-x",
            "4",
            "--search-y",
            "2",
            "--search-z",
            "3",
            "--max-y-drift",
            "2",
            "--max-z-drift",
            "4",
        ],
    )
    assert (args.search_x, args.search_y, args.search_z) == (4, 2, 3)
    assert (args.max_y_drift, args.max_z_drift) == (2, 4)


def test_envelope_flags_default_to_historical_values():
    args = _parse([])
    default = AdaptiveSearchConfig()
    assert args.search_x == default.search_x
    assert args.search_y == default.search_y
    assert args.search_z == default.search_z
    assert args.max_y_drift == default.max_y_drift
    assert args.max_z_drift == default.max_z_drift


@pytest.mark.parametrize("flag", ["--search-x", "--search-z", "--max-y-drift"])
@pytest.mark.parametrize("bad", ["0", "5"])
def test_out_of_range_envelope_flag_rejected(flag, bad):
    with pytest.raises(SystemExit):
        _parse([flag, bad])


# --- The assembled envelope reaches placement -------------------------------


def test_reanalyze_subject_forwards_search_to_placement(tmp_path, monkeypatch):
    subject_dir = _subject(tmp_path)
    _patch_gates(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    def spy(*args, **kwargs):
        captured["search"] = kwargs.get("search")
        return {}, {}

    monkeypatch.setattr(reanalysis, "place_rois_in_native", spy)

    envelope = AdaptiveSearchConfig(search_x=4, max_z_drift=3)
    reanalyze_subject(
        subject_id="sub-01",
        subject_dir=subject_dir,
        roi_shape=ROIShape(shape_type="sphere", sphere_radius=3.0),
        enable_adaptive=True,
        alps_method="ALPS-LAB",
        fa_threshold=0.2,
        search=envelope,
        runner=FakeToolRunner(),
    )

    assert captured["search"] is envelope
