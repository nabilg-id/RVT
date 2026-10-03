"""Tests for the job registry in rch.core.jobs.

The registry started as a bare module-level dict that every worker thread
mutated in place. Two things were wrong with it, and neither showed up as a
failing test because the suite only ever created a handful of jobs:

- Nothing was ever removed. Every channel run left its record behind, holding
  every per-video row it had accumulated. A server left running for a week would
  carry every harvest it had ever done.
- ``/api/status`` deep-copied the whole record to answer one poll. The browser
  polls that endpoint repeatedly while a run is in flight, and on a large channel
  it was copying hundreds of rows each time.

The snapshot is only needed because ``items`` grows while the response is being
serialised. Rows are built fresh and appended, never mutated after that, so
copying the list is enough - and it is what makes a torn row impossible.
"""
from __future__ import annotations

from rch.core.jobs import (
    DEFAULT_MAX_JOBS,
    append_item,
    create_job,
    finished_ids,
    prune,
    snapshot,
    update,
)

REG = {}


def _reg():
    return {}


class TestCreate:
    def test_a_new_job_reports_running(self):
        reg = _reg()

        create_job(reg, 1)

        job = reg[1]
        assert job["status"] == "running"
        assert job["progress"] == 0
        assert job["phase"] == "Mulai"
        assert job["items"] == []
        assert job["result"] is None
        assert job["error"] is None

    def test_it_stamps_when_it_was_created(self):
        reg = _reg()

        create_job(reg, 1)

        assert reg[1]["createdAt"]

    def test_two_jobs_do_not_share_their_item_list(self):
        """The bug this guards is invisible until a second run starts appending
        into the first run's rows."""
        reg = _reg()
        create_job(reg, 1)
        create_job(reg, 2)

        append_item(reg, 1, {"videoId": "a"})

        assert reg[2]["items"] == []


class TestUpdate:
    def test_fields_are_replaced(self):
        reg = _reg()
        create_job(reg, 1)

        update(reg, 1, progress=0.5, phase="2/10")

        assert reg[1]["progress"] == 0.5
        assert reg[1]["phase"] == "2/10"

    def test_updating_an_unknown_job_is_ignored(self):
        """A worker can outlive its job - the registry may have been cleared or
        pruned. That must not raise inside the worker's own error handling."""
        reg = _reg()

        update(reg, 99, progress=1)

        assert 99 not in reg

    def test_finishing_stamps_the_end_and_the_duration(self):
        reg = _reg()
        create_job(reg, 1)

        update(reg, 1, status="done", progress=1, phase="Selesai")

        job = reg[1]
        assert job["finishedAt"]
        assert job["durationMs"] >= 0


class TestAppendItem:
    def test_the_row_is_added_in_order(self):
        reg = _reg()
        create_job(reg, 1)

        append_item(reg, 1, {"videoId": "a"})
        append_item(reg, 1, {"videoId": "b"})

        assert [i["videoId"] for i in reg[1]["items"]] == ["a", "b"]

    def test_appending_to_an_unknown_job_is_ignored(self):
        reg = _reg()

        append_item(reg, 42, {"videoId": "a"})

        assert reg == {}

    def test_the_appended_row_is_the_one_stored(self):
        """Not a copy: building a row is the caller's cost and copying it per
        item would show up on a large channel."""
        reg = _reg()
        create_job(reg, 1)
        row = {"videoId": "a"}

        append_item(reg, 1, row)

        assert reg[1]["items"][0] is row


class TestSnapshot:
    def test_it_detaches_the_item_list(self):
        """This is the whole point. The browser polls while a worker is still
        appending, and without this the list can grow mid-iteration."""
        reg = _reg()
        create_job(reg, 1)
        append_item(reg, 1, {"videoId": "a"})

        snap = snapshot(reg, 1)
        append_item(reg, 1, {"videoId": "b"})

        assert [i["videoId"] for i in snap["items"]] == ["a"]

    def test_it_carries_every_field_the_status_route_serves(self):
        reg = _reg()
        create_job(reg, 1)

        snap = snapshot(reg, 1)

        assert set(snap) >= {"status", "progress", "phase", "items",
                             "result", "error"}

    def test_a_missing_job_snapshots_to_none(self):
        assert snapshot({}, 7) is None

    def test_a_snapshot_is_json_ready(self):
        reg = _reg()
        create_job(reg, 1)
        append_item(reg, 1, {"videoId": "a", "ok": True})

        import json

        json.dumps(snapshot(reg, 1))


class TestPrune:
    def _many(self, reg, count, status="done"):
        for i in range(1, count + 1):
            create_job(reg, i)
            update(reg, i, status=status)

    def test_old_finished_jobs_are_dropped(self):
        reg = _reg()
        self._many(reg, 10)

        prune(reg, max_jobs=3)

        assert sorted(reg) == [8, 9, 10]

    def test_a_running_job_is_never_pruned(self):
        """Pruning a live job would leave its worker updating a record that is
        gone, and the browser polling it would get a 404 mid-run."""
        reg = _reg()
        self._many(reg, 10)
        update(reg, 1, status="running")

        prune(reg, max_jobs=3)

        assert 1 in reg

    def test_pruning_keeps_the_recent_ids(self):
        reg = _reg()
        self._many(reg, 6)

        prune(reg, max_jobs=2)

        assert sorted(reg) == [5, 6]

    def test_it_does_nothing_when_under_the_limit(self):
        reg = _reg()
        self._many(reg, 3)

        prune(reg, max_jobs=10)

        assert len(reg) == 3

    def test_a_registry_of_only_running_jobs_survives(self):
        reg = _reg()
        self._many(reg, 20, status="running")

        prune(reg, max_jobs=5)

        assert len(reg) == 20

    def test_the_limit_must_be_positive(self):
        reg = _reg()
        self._many(reg, 3)

        prune(reg, max_jobs=0)

        assert len(reg) == 3


class TestFinishedIds:
    def test_it_reports_only_finished_jobs(self):
        reg = _reg()
        create_job(reg, 1)
        update(reg, 1, status="done")
        create_job(reg, 2)

        assert finished_ids(reg) == {1}

    def test_an_error_job_counts_as_finished(self):
        """It will never progress again, so it is safe to make room for."""
        reg = _reg()
        create_job(reg, 1)
        update(reg, 1, status="error")

        assert finished_ids(reg) == {1}


class TestMixedRegistry:
    """One registry holds both download records and clip job objects.

    The prune and finished-set walks were written when only dicts lived here,
    and assuming the dict shape crashed the moment a clip Job went in. Both
    shapes have to be readable or a live clip job can take the status endpoint
    down with it.
    """

    class _FakeJob:
        def __init__(self, status="running"):
            self.status = status

    def test_a_running_object_job_is_recognised_as_unfinished(self):
        reg = {1: self._FakeJob("running"), 2: {"status": "done"}}

        assert finished_ids(reg) == {2}

    def test_a_finished_object_job_counts_as_finished(self):
        reg = {1: self._FakeJob("done")}

        assert finished_ids(reg) == {1}

    def test_pruning_leaves_object_jobs_alone(self):
        reg = {1: self._FakeJob("running"), 2: {"status": "done"}}

        prune(reg, max_jobs=1)

        assert 1 in reg
        assert 2 not in reg

    def test_an_object_job_is_not_dropped_while_it_runs(self):
        reg = {1: self._FakeJob("running")}
        for i in range(2, 12):
            reg[i] = {"status": "done"}

        prune(reg, max_jobs=3)

        assert 1 in reg


class TestTheLeakThisFixes:
    def test_a_long_running_server_stops_growing(self):
        """The reason this module exists. Before it, every run left its record
        behind forever, and each record held every per-video row it collected."""
        reg = _reg()
        for run in range(1, 501):
            create_job(reg, run)
            append_item(reg, run, {"videoId": f"v{run}"})
            update(reg, run, status="done")
            prune(reg)

        assert len(reg) == DEFAULT_MAX_JOBS

    def test_the_rows_a_pruned_job_held_are_released(self):
        """Not just the key - the per-video rows are the bulk of it."""
        reg = _reg()
        for run in range(1, 201):
            create_job(reg, run)
            for i in range(200):
                append_item(reg, run, {"videoId": f"v{run}-{i}"})
            update(reg, run, status="done")
            prune(reg)

        held = sum(len(job["items"]) for job in reg.values())
        assert held == DEFAULT_MAX_JOBS * 200

    def test_a_run_in_flight_is_never_the_one_dropped(self):
        reg = _reg()
        for run in range(1, 60):
            create_job(reg, run)
            update(reg, run, status="done")
            prune(reg)

        live = create_job(reg, 999)
        append_item(reg, 999, {"videoId": "sedang-jalan"})

        for _ in range(20):
            prune(reg, max_jobs=5)

        assert reg[999] is live
        assert reg[999]["items"] == [{"videoId": "sedang-jalan"}]
