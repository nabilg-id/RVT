"""Tests for rch.core.checkpoint — crash-safe resume state.

The checkpoint file is the only thing standing between a crash and a
restarted download, so the invariants asserted here are: writes are atomic
(never a partially-written JSON file is left behind), the file name is
stable, corrupt data degrades to ``None`` instead of raising, and marking is
idempotent.
"""
from __future__ import annotations

import json

from rch.core.checkpoint import (
    CHECKPOINT_FILE,
    clear_checkpoint,
    load_checkpoint,
    mark_completed,
    mark_failed,
    save_checkpoint,
)


def _state_path(output_dir):
    return output_dir / CHECKPOINT_FILE


class TestConstants:
    def test_checkpoint_file_name_is_stable(self):
        assert CHECKPOINT_FILE == ".rch-checkpoint.json"


class TestSaveCheckpoint:
    def test_writes_json_to_expected_filename(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 3})

        assert _state_path(tmp_path).exists()

    def test_round_trips_through_load(self, tmp_path):
        payload = {"channel": "Chan", "total": 2, "completed": ["a"], "failed": []}

        save_checkpoint(tmp_path, payload)

        assert load_checkpoint(tmp_path) == payload

    def test_creates_missing_output_directory(self, tmp_path):
        nested = tmp_path / "deeply" / "nested" / "out"

        save_checkpoint(nested, {"total": 1})

        assert _state_path(nested).exists()

    def test_file_content_is_indented_json(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 1, "completed": []})

        raw = _state_path(tmp_path).read_text(encoding="utf-8")
        assert json.loads(raw) == {"total": 1, "completed": []}
        assert "\n  " in raw

    def test_overwrites_previous_state(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 1})
        save_checkpoint(tmp_path, {"total": 9})

        assert load_checkpoint(tmp_path) == {"total": 9}

    def test_leaves_no_temporary_file_behind(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 1})

        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_accepts_path_objects(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 4})

        assert load_checkpoint(tmp_path) == {"total": 4}

    def test_preserves_non_ascii_payload(self, tmp_path):
        save_checkpoint(tmp_path, {"channel": "Kanal Kita"})

        assert load_checkpoint(tmp_path) == {"channel": "Kanal Kita"}


class TestLoadCheckpoint:
    def test_returns_none_when_file_absent(self, tmp_path):
        assert load_checkpoint(tmp_path) is None

    def test_returns_none_when_directory_absent(self, tmp_path):
        assert load_checkpoint(tmp_path / "never-created") is None

    def test_returns_none_for_corrupt_json(self, tmp_path):
        _state_path(tmp_path).write_text("{not json", encoding="utf-8")

        assert load_checkpoint(tmp_path) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        _state_path(tmp_path).write_text("", encoding="utf-8")

        assert load_checkpoint(tmp_path) is None

    def test_returns_none_when_path_is_a_directory(self, tmp_path):
        _state_path(tmp_path).mkdir()

        assert load_checkpoint(tmp_path) is None

    def test_returns_list_payload_unchanged(self, tmp_path):
        save_checkpoint(tmp_path, ["a", "b"])

        assert load_checkpoint(tmp_path) == ["a", "b"]

    def test_recovering_from_corrupt_file_is_possible(self, tmp_path):
        _state_path(tmp_path).write_text("<<<corrupt>>>", encoding="utf-8")

        mark_completed(tmp_path, "vid00000001")

        assert load_checkpoint(tmp_path)["completed"] == ["vid00000001"]


class TestMarkCompleted:
    def test_creates_default_state_when_no_checkpoint_exists(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")

        state = load_checkpoint(tmp_path)
        assert state["completed"] == ["vid00000001"]
        assert state["failed"] == []
        assert state["total"] == 0
        assert state["channel"] is None

    def test_appends_to_existing_list(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")
        mark_completed(tmp_path, "vid00000002")

        assert load_checkpoint(tmp_path)["completed"] == ["vid00000001", "vid00000002"]

    def test_is_idempotent_for_repeated_id(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")
        mark_completed(tmp_path, "vid00000001")

        assert load_checkpoint(tmp_path)["completed"] == ["vid00000001"]

    def test_preserves_other_fields(self, tmp_path):
        save_checkpoint(tmp_path, {"channel": "Chan", "total": 5, "completed": [], "failed": ["x"]})

        mark_completed(tmp_path, "vid00000001")

        state = load_checkpoint(tmp_path)
        assert state["channel"] == "Chan"
        assert state["total"] == 5
        assert state["failed"] == ["x"]

    def test_does_not_touch_failed_list(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")
        mark_failed(tmp_path, "vid00000002")

        mark_completed(tmp_path, "vid00000003")

        state = load_checkpoint(tmp_path)
        assert state["completed"] == ["vid00000001", "vid00000003"]
        assert state["failed"] == ["vid00000002"]

    def test_creates_missing_output_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"

        mark_completed(nested, "vid00000001")

        assert load_checkpoint(nested)["completed"] == ["vid00000001"]


class TestMarkFailed:
    def test_creates_default_state_when_no_checkpoint_exists(self, tmp_path):
        mark_failed(tmp_path, "vid00000001")

        state = load_checkpoint(tmp_path)
        assert state["failed"] == ["vid00000001"]
        assert state["completed"] == []

    def test_appends_to_existing_list(self, tmp_path):
        mark_failed(tmp_path, "vid00000001")
        mark_failed(tmp_path, "vid00000002")

        assert load_checkpoint(tmp_path)["failed"] == ["vid00000001", "vid00000002"]

    def test_is_idempotent_for_repeated_id(self, tmp_path):
        mark_failed(tmp_path, "vid00000001")
        mark_failed(tmp_path, "vid00000001")

        assert load_checkpoint(tmp_path)["failed"] == ["vid00000001"]

    def test_same_id_can_be_failed_after_completed(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")

        mark_failed(tmp_path, "vid00000001")

        state = load_checkpoint(tmp_path)
        assert state["completed"] == ["vid00000001"]
        assert state["failed"] == ["vid00000001"]


class TestClearCheckpoint:
    def test_removes_existing_file(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 1})

        clear_checkpoint(tmp_path)

        assert not _state_path(tmp_path).exists()

    def test_returns_none_and_is_safe_when_absent(self, tmp_path):
        assert clear_checkpoint(tmp_path) is None

    def test_safe_when_directory_absent(self, tmp_path):
        assert clear_checkpoint(tmp_path / "never-created") is None

    def test_safe_when_path_is_a_directory(self, tmp_path):
        _state_path(tmp_path).mkdir()

        assert clear_checkpoint(tmp_path) is None

    def test_state_restarts_after_clear(self, tmp_path):
        mark_completed(tmp_path, "vid00000001")

        clear_checkpoint(tmp_path)
        mark_completed(tmp_path, "vid00000002")

        assert load_checkpoint(tmp_path)["completed"] == ["vid00000002"]

    def test_accepts_path_objects(self, tmp_path):
        save_checkpoint(tmp_path, {"total": 1})

        clear_checkpoint(tmp_path)

        assert load_checkpoint(tmp_path) is None
