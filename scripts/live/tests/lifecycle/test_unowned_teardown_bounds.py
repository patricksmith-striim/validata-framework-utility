"""Each call of a block-less case's best-effort teardown is bounded, and so is the whole teardown.

``teardown_unowned`` runs after the ownership ledger's cleanup, outside its deadline. Its calls -- an
engine's ``drop_test_tables``, a Kafka topic or GCS bucket delete, the op.upload delete, a FileWriter
output clear -- had no bound of their own, so one hung driver or admin call hung the case's teardown
and the run with it. Each call now gets the ledger's per-operation bound (``CLEANUP_OP_S``), the
whole teardown its deadline (``CLEANUP_DEADLINE_S``); a call that outlives either is a failure that
names the bound, and the calls after it still run while the deadline lasts.
"""
from __future__ import annotations

import inspect
import threading
import time
from types import SimpleNamespace

import pytest

from livetest import ownership
from livetest.plugin import teardown_unowned


@pytest.fixture
def gate():
    released = threading.Event()
    yield released
    released.set()                      # let every abandoned call finish


def _hang(gate):
    def call(*_a):
        gate.wait(30)
    return call


def _admin(**methods):
    return SimpleNamespace(**methods)


def test_a_hung_engine_drop_fails_at_its_bound_and_the_other_deletes_still_run(gate):
    deleted = []
    admins = {"oracle-source": {"admin": _admin(drop_test_tables=_hang(gate))}}
    kafka = _admin(delete_topic=deleted.append)
    gcs = _admin(delete_bucket=deleted.append)
    started = time.monotonic()
    out = teardown_unowned(admins, [("oracle-source", "o.sql")], {"TID_ORACLE": "T1_"},
                           [(gcs, "b1")], [(kafka, "t1")], None, lambda p: None, ["u1"], lambda n: None,
                           op_s=0.3, deadline_s=20)
    assert time.monotonic() - started < 5
    assert "did not finish within 0.3s" in out["failed"]["engine-tables oracle-source"]
    assert deleted == ["b1", "t1"]
    assert set(out["failed"]) == {"engine-tables oracle-source"}


@pytest.mark.parametrize("kind", ["bucket", "topic", "upload", "file-output"])
def test_each_unowned_delete_is_bounded(gate, kind):
    hang = _hang(gate)
    args = dict(admins={}, ddl_files=[], tokens={}, gcs_cleanup=[], kafka_cleanup=[], file_spec=None,
                clear_files=lambda p: None, upload_names=[], delete_uploads=lambda n: None)
    if kind == "bucket":
        args["gcs_cleanup"] = [(_admin(delete_bucket=hang), "b1")]
        subject = "bucket b1"
    elif kind == "topic":
        args["kafka_cleanup"] = [(_admin(delete_topic=hang), "t1")]
        subject = "topic t1"
    elif kind == "upload":
        args.update(upload_names=["u1"], delete_uploads=hang)
        subject = "upload u1"
    else:
        args.update(file_spec=[{"path": "/out/f", "min_events": 1}], clear_files=hang)
        subject = "file-output /out/f*"
    started = time.monotonic()
    out = teardown_unowned(**args, op_s=0.3, deadline_s=20)
    assert time.monotonic() - started < 5
    assert "did not finish within 0.3s" in out["failed"][subject]


def test_the_whole_teardown_stops_at_its_deadline(gate):
    hang = _hang(gate)
    kafka = _admin(delete_topic=hang)
    started = time.monotonic()
    out = teardown_unowned({}, [], {}, [], [(kafka, f"t{i}") for i in range(10)], None, lambda p: None,
                           [], lambda n: None, op_s=0.3, deadline_s=0.8)
    assert time.monotonic() - started < 3
    assert len(out["attempted"]) == 10                       # every subject is accounted for
    assert set(out["failed"]) == {f"topic t{i}" for i in range(10)}
    not_attempted = [s for s, e in out["failed"].items() if e == "cleanup-deadline: not attempted"]
    assert 5 <= len(not_attempted) <= 8


def test_a_call_that_raises_is_still_recorded_with_its_error():
    def boom(_t):
        raise RuntimeError("no such topic")
    out = teardown_unowned({}, [], {}, [], [(_admin(delete_topic=boom), "t1")], None, lambda p: None,
                           [], lambda n: None)
    assert out["failed"] == {"topic t1": "RuntimeError('no such topic')"}


def test_the_bounds_are_the_ledgers():
    params = inspect.signature(teardown_unowned).parameters
    assert params["op_s"].default == ownership.CLEANUP_OP_S == 60.0
    assert params["deadline_s"].default == ownership.CLEANUP_DEADLINE_S == 600.0
