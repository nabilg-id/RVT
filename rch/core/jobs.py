"""The job registry behind the web UI.

This started as a module-level dict that every worker thread mutated in place,
which was fine until two things that the tests never exercised:

**Nothing was removed.** Every channel run left its record behind, still holding
every per-video row it had accumulated. A server left running for a week would
be carrying every harvest it had ever run.

**Every status poll deep-copied the record.** The browser polls ``/api/status``
repeatedly while a run is in flight, and on a channel with hundreds of videos
that meant copying hundreds of rows per poll to answer "how far along are you".

The snapshot only exists because ``items`` grows while the response is being
serialised - a shallow ``dict(job)`` hands the serialiser a list that can change
mid-iteration and render a torn row. Rows are built fresh and appended, never
mutated afterwards, so copying the list is enough, and that is what makes the
copy cheap.

The records stay plain dicts on purpose. They are the response body, they are
poked directly by a lot of tests, and a dataclass here would be ceremony around
a structure that is genuinely just a bag of fields.
"""
from __future__ import annotations

import time
from typing import Any, Dict, MutableMapping, Optional

#: A job is finished when it will never advance again. Used to decide what is
#: safe to drop, and both terminal statuses count.
_DONE_STATUSES = frozenset({"done", "error"})

#: How many finished jobs to keep by default. Enough for the browser to have
#: something to show after a reload, small enough that the registry cannot grow
#: without bound.
DEFAULT_MAX_JOBS = 50

_NEW_JOB: Dict[str, Any] = {
    "status": "running",
    "progress": 0,
    "phase": "Mulai",
    "items": [],
    "result": None,
    "error": None,
}


def create_job(registry: MutableMapping[int, Dict[str, Any]], job_id: int,
               **fields: Any) -> Dict[str, Any]:
    """Register a new running job and return its record.

    The field list is copied per job. A shared default list would let the second
    run append into the first run's rows, which is invisible until it happens.
    """
    record = dict(_NEW_JOB)
    record["items"] = []
    record["createdAt"] = _stamp()
    record.update(fields)
    registry[job_id] = record
    return record


def update(registry: MutableMapping[int, Dict[str, Any]], job_id: int,
           **fields: Any) -> None:
    """Merge ``fields`` into a job.

    Updating a job that is gone is ignored rather than an error: a worker thread
    can outlive its record - the registry may have been cleared, or the job
    pruned - and raising here would turn that into a failure inside the worker's
    own error handling.
    """
    job = registry.get(job_id)
    if job is None:
        return
    job.update(fields)
    if fields.get("status") in _DONE_STATUSES:
        job.setdefault("finishedAt", _stamp())
        created = job.get("createdAt")
        if created is not None:
            job.setdefault("durationMs", _since(created))


def append_item(registry: MutableMapping[int, Dict[str, Any]], job_id: int,
                row: Dict[str, Any]) -> None:
    """Append one finished-video row to a job."""
    job = registry.get(job_id)
    if job is None:
        return
    job["items"].append(row)


def snapshot(registry: MutableMapping[int, Dict[str, Any]],
             job_id: int) -> Optional[Dict[str, Any]]:
    """A detached, consistent copy of one job, or ``None`` if it is gone.

    Cheap on purpose: only the lists that a worker can still be appending to are
    copied. A deep copy was only ever needed because those lists grow while the
    caller holds the record - and every row is built fresh and appended, never
    mutated afterwards, so a list copy is enough.

    ``result`` is detached the same way for the same reason: the aggregate
    summary carries its own ``items`` list, and that one is read from the same
    response.
    """
    job = registry.get(job_id)
    if job is None:
        return None
    snap = dict(job)
    snap["items"] = list(job.get("items") or [])
    result = job.get("result")
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        result = dict(result)
        result["items"] = list(result["items"])
        snap["result"] = result
    return snap


def finished_ids(registry: MutableMapping[int, Dict[str, Any]]) -> set:
    """Ids of jobs that will never advance again."""
    return {job_id for job_id, job in registry.items()
            if job.get("status") in _DONE_STATUSES}


def prune(registry: MutableMapping[int, Dict[str, Any]],
          max_jobs: int = DEFAULT_MAX_JOBS) -> int:
    """Drop the oldest finished jobs once there are more than ``max_jobs``.

    Running jobs are never dropped. Removing one would leave its worker updating
    a record that no longer exists, and the browser polling it would get a 404 in
    the middle of a run. Returns how many were removed.
    """
    try:
        limit = int(max_jobs)
    except (TypeError, ValueError):
        return 0
    if limit <= 0:
        # A non-positive limit means pruning is switched off. Coercing it to 1
        # would silently keep almost nothing and look like a data-loss bug.
        return 0

    excess = len(registry) - limit
    if excess <= 0:
        return 0

    # Oldest first, so a limit that has to make room drops the stalest runs.
    # Computed once: calling finished_ids per candidate would be quadratic on a
    # registry that has grown.
    candidates = sorted(finished_ids(registry))
    removed = 0
    for job_id in candidates:
        if removed >= excess:
            break
        del registry[job_id]
        removed += 1
    return removed


def _stamp() -> float:
    return time.monotonic()


def _since(created: float) -> int:
    return max(0, int((time.monotonic() - created) * 1000))
