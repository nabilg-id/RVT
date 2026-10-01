"""Event emitter for progress tracking, and the shared event vocabulary.

``ENGINE_EVENTS`` is the single declaration of what ``rch.youtube.channel``
emits. Both consumers — the CLI (``_print_progress``) and the web GUI
(``_emitter``) — subscribe against it, so an event rename has one place to
change. ``test_events.py`` asserts the tuple still matches the ``.emit(...)``
call sites in the engine.
"""

ENGINE_EVENTS = (
    "phase",
    "start",
    "progress",
    "video:start",
    "video:done",
    "complete",
)


def phase_label(payload) -> str:
    """Human-readable text for a ``phase`` payload.

    ``channel.py`` sends ``{"phase": "list", "msg": "..."}``; the message is
    the user-facing text and the phase key is the fallback. Returns ``""`` for
    a missing or non-dict payload so a consumer never renders a raw payload
    repr to the terminal or the browser.
    """
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("msg") or payload.get("phase") or "")


class EventEmitter:
    """Simple pub/sub emitter."""

    def __init__(self):
        self._listeners = {}

    def on(self, event, fn):
        self._listeners.setdefault(event, []).append(fn)
        return lambda: self._off(event, fn)

    def _off(self, event, fn):
        arr = self._listeners.get(event)
        if arr:
            try:
                arr.remove(fn)
            except ValueError:
                pass

    def emit(self, event, payload=None):
        arr = self._listeners.get(event, [])
        for fn in list(arr):
            try:
                fn(payload)
            except Exception:
                pass


def create_emitter():
    return EventEmitter()
