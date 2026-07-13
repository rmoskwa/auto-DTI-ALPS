"""
Unit tests for the data-staging seam (processing/staging.py).

These exercise ``StagingManager`` directly against a real temp filesystem
(``tmp_path``): copy input files in, redirect ``PipelineState`` paths, copy
results back, and clean up. The load-bearing case is the copy-back failure
path, where results must be preserved and the staging dir must NOT be removed.
No pipeline stage runs — the manager is tested in isolation.
"""

import os

from dti_alps.processing.staging import StagingContext, StagingManager
from dti_alps.processing.state import PipelineState


def _make_inputs(root) -> dict[str, str]:
    """Write the five stage-able input files under ``root`` and return their paths."""
    paths = {}
    contents = {
        "dwi_path": ("dwi.nii.gz", b"dwi-bytes"),
        "bvecs_path": ("dwi.bvec", b"1 0 0\n"),
        "bvals_path": ("dwi.bval", b"0 1000\n"),
        "reverse_pe_path": ("rpe.nii.gz", b"rpe-bytes"),
        "json_sidecar_path": ("dwi.json", b'{"k": 1}'),
    }
    for field_name, (basename, data) in contents.items():
        p = os.path.join(root, basename)
        with open(p, "wb") as fh:
            fh.write(data)
        paths[field_name] = p
    return paths


def _state_with_inputs(input_root, output_dir, staging_dir=None) -> PipelineState:
    inputs = _make_inputs(input_root)
    return PipelineState(
        output_dir=output_dir,
        staging_enabled=True,
        staging_dir=staging_dir,
        **inputs,
    )


def test_stage_in_copies_inputs_and_redirects_state(tmp_path):
    """stage_in copies each input to local storage and rewrites the state paths."""
    src = tmp_path / "slow_mount"
    src.mkdir()
    out = tmp_path / "results"
    staging = tmp_path / "fast"
    staging.mkdir()

    state = _state_with_inputs(str(src), str(out), staging_dir=str(staging))
    originals = {
        f: getattr(state, f)
        for f in ("dwi_path", "bvecs_path", "bvals_path", "reverse_pe_path", "json_sidecar_path")
    }

    ctx = StagingManager().stage_in(state)

    # Every input path now points inside the staging input dir, not the source.
    for field_name, original in originals.items():
        redirected = getattr(state, field_name)
        assert redirected != original
        assert redirected.startswith(ctx.input_dir)
        assert os.path.isfile(redirected)
        with open(redirected, "rb") as a, open(original, "rb") as b:
            assert a.read() == b.read()
        assert ctx.original_input_paths[field_name] == original

    # Output is redirected into the staging root; the real path is remembered.
    assert state.output_dir == ctx.output_dir
    assert ctx.output_dir.startswith(ctx.staging_root)
    assert ctx.original_output_dir == str(out)
    # Staging root honors the custom staging_dir base.
    assert ctx.staging_root.startswith(str(staging))


def test_stage_in_skips_missing_optional_inputs(tmp_path):
    """Optional inputs that don't exist on disk are left untouched, not copied."""
    src = tmp_path / "in"
    src.mkdir()
    inputs = _make_inputs(str(src))
    # Drop the two optional files from disk to simulate a run without them.
    os.remove(inputs["reverse_pe_path"])
    os.remove(inputs["json_sidecar_path"])

    state = PipelineState(
        output_dir=str(tmp_path / "out"),
        staging_enabled=True,
        **inputs,
    )
    ctx = StagingManager().stage_in(state)

    assert "reverse_pe_path" not in ctx.original_input_paths
    assert "json_sidecar_path" not in ctx.original_input_paths
    # Missing paths keep their original (non-staged) value.
    assert state.reverse_pe_path == inputs["reverse_pe_path"]
    assert state.dwi_path.startswith(ctx.input_dir)


def test_round_trip_copies_results_back_and_cleans_up(tmp_path):
    """stage_out returns results to the real output dir; cleanup removes staging."""
    src = tmp_path / "in"
    src.mkdir()
    out = tmp_path / "out"
    state = _state_with_inputs(str(src), str(out))
    mgr = StagingManager()

    ctx = mgr.stage_in(state)
    # Simulate the pipeline producing a result in the redirected output dir.
    result = os.path.join(state.output_dir, "subject_alps.csv")
    with open(result, "w") as fh:
        fh.write("index,value\n")

    mgr.stage_out(state, ctx)

    assert not ctx.copy_back_failed
    assert os.path.isfile(out / "subject_alps.csv")
    # output_dir restored to the real path so downstream code sees the truth.
    assert state.output_dir == str(out)

    mgr.cleanup(ctx)
    assert not os.path.exists(ctx.staging_root)


def test_copy_back_failure_preserves_results_and_skips_cleanup(tmp_path, monkeypatch):
    """If copy-back fails, staging is preserved so the user can recover results."""
    src = tmp_path / "in"
    src.mkdir()
    out = tmp_path / "out"
    state = _state_with_inputs(str(src), str(out))

    logs: list[str] = []
    mgr = StagingManager(log_callback=logs.append)
    ctx = mgr.stage_in(state)

    result = os.path.join(state.output_dir, "subject_alps.csv")
    with open(result, "w") as fh:
        fh.write("index,value\n")

    # Force the copy-back to blow up mid-flight.
    import dti_alps.processing.staging as staging_mod

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(staging_mod.shutil, "copytree", _boom)

    mgr.stage_out(state, ctx)

    assert ctx.copy_back_failed is True
    # output_dir is still restored even on failure.
    assert state.output_dir == str(out)
    # The staged result survives.
    assert os.path.isfile(result)
    assert any("Failed to copy results back" in m for m in logs)

    # cleanup must NOT remove the staging dir while copy-back is flagged failed.
    mgr.cleanup(ctx)
    assert os.path.exists(ctx.staging_root)
    assert os.path.isfile(result)
    assert any("Preserving staging directory" in m for m in logs)


def test_cleanup_removes_staging_when_copy_back_succeeded(tmp_path):
    """A context with copy_back_failed=False is removed by cleanup."""
    staging_root = tmp_path / "dti_alps_staging_xyz"
    (staging_root / "output").mkdir(parents=True)
    ctx = StagingContext(staging_root=str(staging_root), copy_back_failed=False)

    StagingManager().cleanup(ctx)

    assert not os.path.exists(staging_root)
