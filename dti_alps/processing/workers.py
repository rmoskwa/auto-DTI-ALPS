"""
Background threading workers for DTI-ALPS pipeline.

This module provides thread-safe workers for running the pipeline
and batch processing in the background, communicating with the GUI
via queues.
"""

import queue
import threading
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
        """
        super().__init__(daemon=True)
        self.batch_runner = batch_runner
        self.result_queue = result_queue
        self.cancel_event = cancel_event

    def run(self):
        """Execute batch processing in background."""
        try:
            # Set up progress callback to send to queue
            def progress_callback(message: WorkerMessage):
                self.result_queue.put(message)

                # Check cancellation after each message
                if self.cancel_event.is_set():
                    self.batch_runner.cancelled = True

            self.batch_runner.progress_callback = progress_callback

            # Run batch
            success = self.batch_runner.run_batch()

            if self.cancel_event.is_set():
                self.result_queue.put(BatchCancelled())
            elif success:
                self.result_queue.put(BatchSuccess(self.batch_runner.batch_state))
            else:
                self.result_queue.put(BatchPartial(self.batch_runner.batch_state))

        except Exception as e:
            self.result_queue.put(Error(str(e)))
