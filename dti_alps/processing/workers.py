"""
Background threading workers for DTI-ALPS pipeline.

This module provides thread-safe workers for running the pipeline
and batch processing in the background, communicating with the GUI
via queues.
"""

import queue
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from .messages import BatchCancelled, BatchPartial, BatchSuccess, Error, WorkerMessage

if TYPE_CHECKING:
    from .batch import BatchRunner


class BatchWorker(threading.Thread):
    """
    Background thread for running batch processing.

    Communicates with GUI via queue for thread-safe updates.
    """

    def __init__(
        self,
        batch_runner: "BatchRunner",
        result_queue: queue.Queue,
        cancel_event: threading.Event,
        message_sink: Callable[[WorkerMessage], None] | None = None,
    ):
        """
        Initialize the batch worker thread.

        Parameters
        ----------
        batch_runner : BatchRunner
            Configured batch runner
        result_queue : queue.Queue
            Queue for sending results back to GUI
        cancel_event : threading.Event
            Event for signaling cancellation
        message_sink : callable, optional
            Where every message is delivered. Defaults to ``result_queue.put``.
            A front end passes a *composed* sink here -- typically
            ``LogFileSink.wrap(result_queue.put)`` -- so the log file sees the
            same stream the GUI does. Supplying it as a parameter rather than
            setting ``batch_runner.progress_callback`` is deliberate: this
            worker owns that attribute (it must interleave the cancellation
            check), so a callback assigned by the caller would be overwritten.
        """
        super().__init__(daemon=True)
        self.batch_runner = batch_runner
        self.result_queue = result_queue
        self.cancel_event = cancel_event
        self.message_sink = message_sink or result_queue.put

    def run(self):
        """Execute batch processing in background."""
        try:
            # Deliver progress to the sink, checking cancellation as we go.
            def progress_callback(message: WorkerMessage):
                self.message_sink(message)

                # Check cancellation after each message
                if self.cancel_event.is_set():
                    self.batch_runner.cancelled = True

            self.batch_runner.progress_callback = progress_callback

            # Run batch
            success = self.batch_runner.run_batch()

            if self.cancel_event.is_set():
                self.message_sink(BatchCancelled())
            elif success:
                self.message_sink(BatchSuccess(self.batch_runner.batch_state))
            else:
                self.message_sink(BatchPartial(self.batch_runner.batch_state))

        except Exception as e:
            self.message_sink(Error(str(e)))
