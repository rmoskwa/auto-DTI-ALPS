"""
The typed worker→GUI message contract.

Background workers push progress events onto a ``queue.Queue`` that the GUI
drains. Each event is one of the frozen dataclasses below; together they form the
closed :data:`WorkerMessage` union. The producers (``PipelineRunner``,
``BatchRunner``, ``BatchWorker``) construct instances and the GUI's
``ResultModel.handle`` dispatches over the whole union — so the legal messages
and their payloads are named in exactly one place, and a misnamed or
wrong-shaped message fails at construction rather than mismatching downstream.

Stdlib-only (``dataclasses``): this module carries the ``gui → processing``
dependency arrow and must stay Qt-free. Payloads that carry domain objects import
them from ``state`` (same package, no cycle — ``state`` does not import
``messages``).
"""

from dataclasses import dataclass

from .state import BatchState, SubjectResult


@dataclass(frozen=True)
class Log:
    """A line for the log console. Emitted by ``PipelineRunner._log``."""

    text: str


@dataclass(frozen=True)
class Stage:
    """A pipeline-stage status change. Emitted by ``PipelineRunner._update_stage``."""

    stage: str
    status: str


@dataclass(frozen=True)
class BatchStart:
    """A batch run is beginning with ``total`` subjects. Emitted by ``BatchRunner``."""

    total: int


@dataclass(frozen=True)
class SubjectStart:
    """Subject ``index`` (``subject_id``) is starting. Emitted by ``BatchRunner``."""

    index: int
    subject_id: str


@dataclass(frozen=True)
class SubjectComplete:
    """Subject ``index`` finished with ``result``. Emitted by ``BatchRunner``."""

    index: int
    result: SubjectResult


@dataclass(frozen=True)
class BatchComplete:
    """The batch loop finished (CSV written). Emitted by ``BatchRunner``."""

    batch_state: BatchState


@dataclass(frozen=True)
class BatchSuccess:
    """Every subject succeeded. Emitted by ``BatchWorker``."""

    batch_state: BatchState


@dataclass(frozen=True)
class BatchPartial:
    """The batch finished with at least one failure. Emitted by ``BatchWorker``."""

    batch_state: BatchState


@dataclass(frozen=True)
class BatchCancelled:
    """The batch was cancelled by the user. Emitted by ``BatchWorker``."""


@dataclass(frozen=True)
class Error:
    """An unhandled exception aborted the run. Emitted by ``BatchWorker``."""

    message: str


# The closed set of worker→GUI messages. ``ResultModel.handle`` dispatches over
# exactly these and raises on anything else.
WorkerMessage = (
    Log
    | Stage
    | BatchStart
    | SubjectStart
    | SubjectComplete
    | BatchComplete
    | BatchSuccess
    | BatchPartial
    | BatchCancelled
    | Error
)
