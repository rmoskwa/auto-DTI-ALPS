"""
Unit tests for the per-subject completion marker.

The marker answers two questions the batch CSV cannot: "did this subject finish,
and under which protocol?" (for ``--resume``) and "what were its numbers?" when a
hard kill stopped the run before the CSV was written.

Pure engine, no toolchain: the ``BatchRunner`` tests inject a fake pipeline
runner class rather than invoking MRtrix3 or FSL.
"""

import json

import pytest

from dti_alps.processing import results_layout
from dti_alps.processing.batch import BatchRunner
from dti_alps.processing.config_io import protocol_hash
from dti_alps.processing.discovery import SubjectFiles
from dti_alps.processing.results_layout import (
    CompletionMarker,
    completion_marker_path,
    is_complete_for_protocol,
    read_completion_marker,
    write_completion_marker,
)
from dti_alps.processing.state import BatchConfig, BatchState

MARKER = CompletionMarker(
    subject_id="sub-01",
    status="completed",
    protocol_hash="abc123",
    alps_by_shape={"rois_adaptive": {"alps_lab_left": 1.42, "alps_lab_right": 1.51}},
)


class TestReadWrite:
    """The marker round-trips through its own file."""

    def test_round_trip(self, tmp_path):
        write_completion_marker(tmp_path, MARKER)

        assert read_completion_marker(tmp_path) == MARKER

    def test_lands_beside_the_subject_data(self, tmp_path):
        write_completion_marker(tmp_path, MARKER)

        assert (tmp_path / "alps_result.json").exists()
        assert completion_marker_path(tmp_path).name == "alps_result.json"

    def test_creates_the_subject_directory(self, tmp_path):
        target = tmp_path / "out" / "sub-01"
        write_completion_marker(target, MARKER)

        assert (target / "alps_result.json").exists()

    def test_alps_values_are_readable_json(self, tmp_path):
        write_completion_marker(tmp_path, MARKER)

        document = json.loads((tmp_path / "alps_result.json").read_text())

        assert document["alps_by_shape"]["rois_adaptive"]["alps_lab_left"] == 1.42
        assert document["protocol_hash"] == "abc123"


class TestAbsentOrDamagedReadsAsAbsent:
    """
    The only consumer is ``--resume``, and the safe answer to "is this subject
    done?" when the record is damaged is *no*.
    """

    def test_no_marker(self, tmp_path):
        assert read_completion_marker(tmp_path) is None

    def test_unparseable_marker(self, tmp_path):
        (tmp_path / "alps_result.json").write_text("{ not json")

        assert read_completion_marker(tmp_path) is None

    def test_json_that_is_not_an_object(self, tmp_path):
        (tmp_path / "alps_result.json").write_text("[]")

        assert read_completion_marker(tmp_path) is None


class TestIsCompleteForProtocol:
    """The precise question ``--resume`` asks."""

    def test_completed_and_matching_hash_is_done(self, tmp_path):
        write_completion_marker(tmp_path, MARKER)

        assert is_complete_for_protocol(tmp_path, "abc123") is True

    def test_mismatched_hash_is_not_done(self, tmp_path):
        """An edited protocol reprocesses everything -- the safe default."""
        write_completion_marker(tmp_path, MARKER)

        assert is_complete_for_protocol(tmp_path, "different") is False

    def test_absent_marker_is_not_done(self, tmp_path):
        assert is_complete_for_protocol(tmp_path, "abc123") is False

    @pytest.mark.parametrize("status", ["failed", "skipped", "running", "pending"])
    def test_unfinished_status_is_not_done(self, tmp_path, status):
        """
        A directory that merely *looks* populated is not evidence of completion:
        a subject killed mid-FNIRT leaves one, and skipping it would silently
        carry ROI masks from a half-finished warp into the cohort.
        """
        write_completion_marker(
            tmp_path,
            CompletionMarker(subject_id="sub-01", status=status, protocol_hash="abc123"),
        )

        assert is_complete_for_protocol(tmp_path, "abc123") is False


class _FakePipelineRunner:
    """A PipelineRunner stand-in that succeeds without touching a toolchain."""

    succeed = True

    def __init__(self, state, progress_callback=None):
        self.state = state
        self.cancelled = False

    def run_full_pipeline(self):
        if not type(self).succeed:
            return False
        # All three hemisphere values, because BatchRunner formats them for the
        # log the moment a subject completes.
        values = {
            "method": "ALPS-LAB",
            "LAB_ALPS_left": 1.38,
            "LAB_ALPS_right": 1.42,
            "LAB_ALPS_bilateral": 1.4,
        }
        self.state.alps_results = values
        self.state.alps_results_by_shape = {"rois": values}
        return True


def _batch(tmp_path, succeed=True, **config_kwargs):
    """A one-subject batch whose pipeline is faked."""

    class Runner(_FakePipelineRunner):
        pass

    Runner.succeed = succeed

    config = BatchConfig(output_dir=str(tmp_path / "out"), **config_kwargs)
    subject = SubjectFiles(
        folder_path=str(tmp_path / "sub-01"),
        subject_id="sub-01",
        dwi_path="dwi.nii.gz",
        bvec_path="dwi.bvec",
        bval_path="dwi.bval",
    )
    state = BatchState(config=config, subjects=[subject])
    return BatchRunner(state), Runner, config


class TestBatchRunnerWritesTheMarker:
    """The marker lands as each subject finishes, not at the end of the batch."""

    def test_written_on_success(self, tmp_path, monkeypatch):
        runner, fake_runner_cls, config = _batch(tmp_path)
        monkeypatch.setattr(
            "dti_alps.processing.pipeline.PipelineRunner", fake_runner_cls, raising=False
        )
        runner.run_batch()

        marker = read_completion_marker(tmp_path / "out" / "sub-01")
        assert marker is not None
        assert marker.status == "completed"
        assert marker.protocol_hash == protocol_hash(config)

    def test_written_on_failure_too(self, tmp_path, monkeypatch):
        """A failed subject is recorded as failed, so resume retries it."""
        runner, fake_runner_cls, config = _batch(tmp_path, succeed=False)
        monkeypatch.setattr(
            "dti_alps.processing.pipeline.PipelineRunner", fake_runner_cls, raising=False
        )
        runner.run_batch()

        marker = read_completion_marker(tmp_path / "out" / "sub-01")
        assert marker.status == "failed"
        assert is_complete_for_protocol(tmp_path / "out" / "sub-01", protocol_hash(config)) is False

    def test_carries_the_per_shape_alps_values(self, tmp_path, monkeypatch):
        """The durability half: the numbers survive a kill before the CSV."""
        runner, fake_runner_cls, _ = _batch(tmp_path)
        monkeypatch.setattr(
            "dti_alps.processing.pipeline.PipelineRunner", fake_runner_cls, raising=False
        )
        runner.run_batch()

        marker = read_completion_marker(tmp_path / "out" / "sub-01")
        assert marker.alps_by_shape["rois"]["alps_lab_bilateral"] == 1.4

    def test_marker_failure_does_not_fail_the_subject(self, tmp_path, monkeypatch):
        """Losing resumability is a smaller loss than discarding a result."""
        runner, fake_runner_cls, _ = _batch(tmp_path)
        monkeypatch.setattr(
            "dti_alps.processing.pipeline.PipelineRunner", fake_runner_cls, raising=False
        )

        def boom(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(results_layout, "write_completion_marker", boom)

        assert runner.run_batch() is True
        assert runner.batch_state.results[0].status == "completed"

    def test_hash_ignores_the_output_directory(self, tmp_path, monkeypatch):
        """
        Resuming into the same directory from a different machine must match:
        placement is not part of the protocol.
        """
        runner, fake_runner_cls, config = _batch(tmp_path)
        monkeypatch.setattr(
            "dti_alps.processing.pipeline.PipelineRunner", fake_runner_cls, raising=False
        )
        runner.run_batch()

        elsewhere = BatchConfig(output_dir="/some/other/place")
        assert is_complete_for_protocol(tmp_path / "out" / "sub-01", protocol_hash(elsewhere))
