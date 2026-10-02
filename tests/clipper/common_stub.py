"""Stubbing helpers for the clipper web tests.

``/api/preview`` reaches into the RCH metadata and thumbnail modules, which
would otherwise perform real network calls during the test run.
"""
from __future__ import annotations

VIDEO_ID = "dQw4w9WgXcQ"


def stub_thumbnail(monkeypatch) -> None:
    """Ganti ``rch.youtube.thumbnail`` dan ``common`` dengan versi offline."""
    import rch.youtube.common as common
    import rch.youtube.thumbnail as thumb

    monkeypatch.setattr(common, "extract_video_id", lambda url: VIDEO_ID)
    monkeypatch.setattr(
        thumb, "thumbnail",
        lambda url: {
            "status": True,
            "result": {
                "id": VIDEO_ID,
                "title": "Judul Uji",
                "thumbnails": {"maxresdefault": f"https://i.ytimg.com/vi/{VIDEO_ID}/max.jpg"},
            },
        },
    )


def stub_metadata_boom(monkeypatch) -> None:
    """Buat pengambilan metadata meledak, untuk menguji jalur degradasi."""
    import rch.youtube.metadata as meta

    def _boom(video_id):
        raise RuntimeError("metadata offline")

    monkeypatch.setattr(meta, "get_video_info", _boom)
