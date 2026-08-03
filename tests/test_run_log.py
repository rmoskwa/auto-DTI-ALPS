"""
Unit tests for the run log sink (``processing/run_log.py``).

The whole point of moving the log file into the engine is that it is testable
without a Qt application, so nothing here instantiates a window or imports a
toolkit -- the sink is exercised as what it is: a callable wrapping a callable.
"""

from datetime import datetime

from dti_alps.processing.messages import BatchStart, Log, SubjectStart
from dti_alps.processing.run_log import LogFileSink, format_log_line, log_file_name
from dti_alps.processing.state import OutputConfig

WHEN = datetime(2026, 8, 2, 14, 30, 5)


class TestNaming:
    """The filename and line format are shared by both front ends."""

    def test_log_file_name_is_timestamped(self):
        assert log_file_name(WHEN) == "dti_alps_20260802_143005.log"

    def test_line_carries_a_wall_clock_stamp(self):
        assert format_log_line("hello", WHEN) == "[14:30:05] hello"


class TestPassThrough:
    """The sink wraps a callback; it never swallows or reorders the stream."""

    def test_every_message_reaches_the_inner_callback(self, tmp_path):
        seen = []
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)
        emit = sink.wrap(seen.append)

        emit(BatchStart(2))
        emit(Log("denoising"))
        emit(SubjectStart(0, "sub-01"))
        sink.close()

        # The first message is the sink's own "log file created" announcement,
        # then the three in the order they were emitted.
        assert isinstance(seen[0], Log)
        assert seen[1:] == [BatchStart(2), Log("denoising"), SubjectStart(0, "sub-01")]

    def test_wrapping_with_no_inner_callback_is_allowed(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)
        sink.wrap()(Log("still filed"))
        sink.close()

        assert "still filed" in (tmp_path / log_file_name(WHEN)).read_text()


class TestFileContent:
    """Only ``Log`` messages have text, so only they are filed."""

    def test_log_lines_are_written_stamped(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)
        emit = sink.wrap()
        emit(Log("Running: dwidenoise in.mif out.mif"))
        sink.close()

        content = (tmp_path / log_file_name(WHEN)).read_text()
        assert "Running: dwidenoise in.mif out.mif" in content
        # Each line carries a [HH:MM:SS] stamp, like the GUI console.
        for line in content.splitlines():
            assert line.startswith("[") and line[9] == "]"

    def test_non_log_messages_are_not_filed(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)
        emit = sink.wrap()
        emit(BatchStart(7))
        emit(SubjectStart(0, "sub-01"))
        sink.close()

        content = (tmp_path / log_file_name(WHEN)).read_text()
        assert "BatchStart" not in content
        assert "sub-01" not in content

    def test_announcement_names_the_file(self, tmp_path):
        seen = []
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)
        sink.wrap(seen.append)
        sink.close()

        assert log_file_name(WHEN) in seen[0].text

    def test_output_directory_is_created(self, tmp_path):
        target = tmp_path / "does" / "not" / "exist"
        sink = LogFileSink(str(target), OutputConfig(), when=WHEN)
        sink.wrap()(Log("line"))
        sink.close()

        assert (target / log_file_name(WHEN)).exists()


class TestRetention:
    """``OutputConfig.log_file`` decides whether the file survives the run."""

    def test_log_kept_when_flag_is_true(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(log_file=True), when=WHEN)
        sink.wrap()(Log("keep me"))
        sink.close()

        assert (tmp_path / log_file_name(WHEN)).exists()

    def test_log_deleted_when_flag_is_false(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(log_file=False), when=WHEN)
        sink.wrap()(Log("discard me"))
        sink.close()

        assert not (tmp_path / log_file_name(WHEN)).exists()

    def test_log_is_written_during_the_run_even_when_it_will_be_deleted(self, tmp_path):
        """
        Retention is honoured at close, not at open, so a run killed partway
        still leaves a record -- which is exactly when a log matters most.
        """
        sink = LogFileSink(str(tmp_path), OutputConfig(log_file=False), when=WHEN)
        sink.wrap()(Log("mid-run"))

        assert (tmp_path / log_file_name(WHEN)).exists()
        sink.close()

    def test_close_is_idempotent(self, tmp_path):
        sink = LogFileSink(str(tmp_path), OutputConfig(log_file=False), when=WHEN)
        sink.wrap()(Log("x"))
        sink.close()
        sink.close()  # must not raise


class TestDegradesWhenTheFileCannotBeOpened:
    """Losing the log is never a reason to lose the analysis."""

    def test_unopenable_path_still_forwards_messages(self, tmp_path):
        # A file where the output directory should be: makedirs fails.
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("")

        seen = []
        sink = LogFileSink(str(blocked), OutputConfig(), when=WHEN)
        emit = sink.wrap(seen.append)
        emit(Log("work continues"))
        sink.close()

        assert sink.path is None
        assert "Could not create log file" in seen[0].text
        assert seen[1] == Log("work continues")


class TestContextManager:
    """The `with` form closes even when the run raises."""

    def test_closes_on_exception(self, tmp_path):
        try:
            with LogFileSink(str(tmp_path), OutputConfig(log_file=False), when=WHEN) as sink:
                sink.wrap()(Log("boom incoming"))
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        assert not (tmp_path / log_file_name(WHEN)).exists()


class TestBatchWorkerComposesTheSink:
    """
    ``BatchWorker`` owns ``progress_callback`` (it interleaves the cancellation
    check), so a front end composes its sink through ``message_sink`` instead --
    otherwise the wrapper would be silently overwritten and nothing would be
    filed.
    """

    def test_message_sink_receives_the_stream(self, tmp_path):
        import queue
        import threading

        from dti_alps.processing.batch import BatchRunner
        from dti_alps.processing.state import BatchConfig, BatchState
        from dti_alps.processing.workers import BatchWorker

        result_queue = queue.Queue()
        sink = LogFileSink(str(tmp_path), OutputConfig(), when=WHEN)

        state = BatchState(config=BatchConfig(output_dir=str(tmp_path)), subjects=[])
        worker = BatchWorker(
            BatchRunner(state),
            result_queue,
            threading.Event(),
            message_sink=sink.wrap(result_queue.put),
        )
        worker.start()
        worker.join(timeout=10)
        sink.close()

        # The empty batch still logs its start and its CSV write; both are filed
        # *and* still reach the queue the GUI drains.
        content = (tmp_path / log_file_name(WHEN)).read_text()
        assert "Starting batch processing for 0 subjects" in content
        assert not result_queue.empty()

    def test_default_sink_is_the_queue(self, tmp_path):
        """Omitting ``message_sink`` keeps the historical queue-only behaviour."""
        import queue
        import threading

        from dti_alps.processing.batch import BatchRunner
        from dti_alps.processing.state import BatchConfig, BatchState
        from dti_alps.processing.workers import BatchWorker

        result_queue = queue.Queue()
        state = BatchState(config=BatchConfig(output_dir=str(tmp_path)), subjects=[])
        worker = BatchWorker(BatchRunner(state), result_queue, threading.Event())
        worker.start()
        worker.join(timeout=10)

        assert not result_queue.empty()
