"""Tests for rch.core.events — the pub/sub emitter and the event vocabulary.

The emitter is the progress-reporting backbone, so the two properties that
matter are (a) unsubscribing actually stops delivery and (b) a throwing
listener never breaks the emit loop for its siblings. Both are asserted here.

``ENGINE_EVENTS`` and ``phase_label`` pin the contract shared by the producer
(``rch.youtube.channel``) and its two consumers (the CLI and the web GUI), so a
rename cannot silently leave one side listening to nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

from rch.core.events import ENGINE_EVENTS, EventEmitter, create_emitter, phase_label


class TestOn:
    def test_returns_callable_unsubscribe(self):
        emitter = EventEmitter()
        assert callable(emitter.on("progress", lambda payload: None))

    def test_registers_multiple_listeners_for_same_event(self):
        emitter = EventEmitter()
        seen = []
        emitter.on("progress", lambda p: seen.append("a"))
        emitter.on("progress", lambda p: seen.append("b"))
        emitter.emit("progress", 1)
        assert seen == ["a", "b"]

    def test_listeners_are_scoped_per_event(self):
        emitter = EventEmitter()
        seen = []
        emitter.on("a", lambda p: seen.append("a"))
        emitter.on("b", lambda p: seen.append("b"))
        emitter.emit("b", None)
        assert seen == ["b"]


class TestEmit:
    def test_passes_payload_to_listener(self):
        emitter = EventEmitter()
        received = []
        emitter.on("progress", received.append)
        emitter.emit("progress", {"pct": 50})
        assert received == [{"pct": 50}]

    def test_payload_defaults_to_none(self):
        emitter = EventEmitter()
        received = []
        emitter.on("done", received.append)
        emitter.emit("done")
        assert received == [None]

    def test_unknown_event_is_a_noop(self):
        emitter = EventEmitter()
        assert emitter.emit("nobody-listens") is None

    def test_listeners_called_in_registration_order(self):
        emitter = EventEmitter()
        seen = []
        for name in ("first", "second", "third"):
            emitter.on("e", lambda payload, name=name: seen.append(name))
        emitter.emit("e")
        assert seen == ["first", "second", "third"]

    def test_raising_listener_does_not_stop_siblings(self):
        emitter = EventEmitter()
        seen = []

        def boom(payload):
            raise RuntimeError("listener exploded")

        emitter.on("e", boom)
        emitter.on("e", lambda p: seen.append("survivor"))
        emitter.emit("e", None)
        assert seen == ["survivor"]

    def test_emit_iterates_a_snapshot_so_unsubscribe_during_emit_is_safe(self):
        emitter = EventEmitter()
        seen = []
        off_holder = {}

        def first(payload):
            seen.append("first")
            off_holder["off"]()

        emitter.on("e", first)
        emitter.on("e", lambda p: seen.append("second"))
        off_holder["off"] = emitter.on("e", lambda p: seen.append("third"))
        emitter.emit("e")
        assert seen == ["first", "second", "third"]


class TestUnsubscribe:
    def test_stops_future_delivery(self):
        emitter = EventEmitter()
        seen = []
        off = emitter.on("e", seen.append)
        emitter.emit("e", 1)
        off()
        emitter.emit("e", 2)
        assert seen == [1]

    def test_unsubscribing_twice_is_idempotent(self):
        emitter = EventEmitter()
        seen = []
        off = emitter.on("e", seen.append)
        off()
        off()
        emitter.emit("e", 1)
        assert seen == []

    def test_unsubscribing_unknown_listener_does_not_remove_siblings(self):
        emitter = EventEmitter()
        seen = []
        off_a = emitter.on("e", lambda p: seen.append("a"))
        emitter.on("e", lambda p: seen.append("b"))
        off_a()
        off_a()
        emitter.emit("e")
        assert seen == ["b"]

    def test_unsubscribe_on_event_with_no_listeners_is_a_noop(self):
        emitter = EventEmitter()
        emitter._off("never-used", lambda p: None)

    def test_only_targeted_listener_is_removed(self):
        emitter = EventEmitter()
        seen = []
        emitter.on("e", lambda p: seen.append("keep"))
        off = emitter.on("e", lambda p: seen.append("drop"))
        off()
        emitter.emit("e")
        assert seen == ["keep"]


class TestCreateEmitter:
    def test_returns_event_emitter_instance(self):
        assert isinstance(create_emitter(), EventEmitter)

    def test_instances_do_not_share_listener_state(self):
        first = create_emitter()
        second = create_emitter()
        seen = []
        first.on("e", seen.append)
        second.emit("e", "leaked")
        assert seen == []


class TestEngineEvents:
    def test_vocabulary_matches_what_channel_py_emits(self):
        """Every event the engine emits is listed, and nothing extra is claimed."""
        source = Path("rch/youtube/channel.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'\.emit\(\s*"([^"]+)"', source))

        assert emitted == set(ENGINE_EVENTS)

    def test_list_is_a_tuple_of_non_empty_strings(self):
        assert isinstance(ENGINE_EVENTS, tuple)
        assert all(isinstance(name, str) and name for name in ENGINE_EVENTS)

    def test_progress_and_phase_and_video_done_are_present(self):
        """The three events both consumers actually render."""
        assert {"progress", "phase", "video:done"} <= set(ENGINE_EVENTS)

    def test_no_legacy_item_events_are_declared(self):
        """``item:ok``/``item:fail`` are never emitted; see the CLI/UI history."""
        assert "item:ok" not in ENGINE_EVENTS
        assert "item:fail" not in ENGINE_EVENTS


class TestPhaseLabel:
    def test_prefers_the_human_message(self):
        assert phase_label({"phase": "list", "msg": "Mengambil daftar"}) == "Mengambil daftar"

    def test_falls_back_to_the_phase_key(self):
        assert phase_label({"phase": "download"}) == "download"

    def test_empty_message_falls_back_to_the_phase_key(self):
        assert phase_label({"phase": "download", "msg": ""}) == "download"

    def test_none_payload_is_empty_string(self):
        assert phase_label(None) == ""

    def test_empty_dict_is_empty_string(self):
        assert phase_label({}) == ""

    def test_non_dict_payload_is_empty_string(self):
        assert phase_label("phase") == ""
        assert phase_label([1, 2]) == ""

    def test_message_is_coerced_to_string(self):
        assert phase_label({"msg": 42}) == "42"

    def test_never_returns_a_raw_payload_repr(self):
        result = phase_label({"phase": "list", "msg": "M"})

        assert "{" not in result
        assert "'" not in result
