"""
Construction tests for the worker→GUI message dataclasses.

These pin the shape of the contract — the fields each message carries and that
every member is frozen — without wiring any producer or consumer. The
consumer-side exhaustiveness (that ``ResultModel.handle`` accepts one of every
member) lives in ``tests/test_result_model.py``.
"""

import dataclasses

import pytest

from dti_alps.processing.messages import (
    BatchCancelled,
    BatchComplete,
    BatchPartial,
    BatchStart,
    BatchSuccess,
    Error,
    Log,
    Stage,
    SubjectComplete,
    SubjectStart,
)
from dti_alps.processing.state import BatchConfig, BatchState, SubjectResult


def test_messages_carry_their_payloads():
    """Each message exposes exactly its documented fields."""
    batch_state = BatchState(config=BatchConfig(), subjects=[], results=[])
    result = SubjectResult(subject_id="s1", folder_path="/d/s1", status="completed")

    assert Log("hi").text == "hi"

    stage = Stage("denoise", "running")
    assert (stage.stage, stage.status) == ("denoise", "running")

    assert BatchStart(3).total == 3

    start = SubjectStart(0, "s1")
    assert (start.index, start.subject_id) == (0, "s1")

    complete = SubjectComplete(1, result)
    assert (complete.index, complete.result) == (1, result)

    assert BatchComplete(batch_state).batch_state is batch_state
    assert BatchSuccess(batch_state).batch_state is batch_state
    assert BatchPartial(batch_state).batch_state is batch_state
    assert Error("boom").message == "boom"

    # BatchCancelled is a payload-free marker.
    assert BatchCancelled() == BatchCancelled()


def test_messages_are_frozen():
    """Messages are immutable — assigning a field raises."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        Log("hi").text = "bye"
