"""Stubbing helpers for the clipper web tests.

``/api/preview`` reaches into the RCH metadata and thumbnail modules, which
would otherwise perform real network calls during the test run.

The metadata path is the dangerous one: ``rch.youtube.metadata.get_video_info``
shells out to the ``yt-dlp`` binary, so stubbing only the HTTP-shaped call is
not enough - it is a separate process and the test still waits on the network.
One preview test took 7.7 seconds for exactly this reason.
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


def stub_metadata(monkeypatch, duration=212, upload_date="20240102") -> None:
    """Replace metadata lookup with a canned record.

    Must be stubbed even when the test only cares about the thumbnail, because
    get_video_info spawns yt-dlp as a subprocess and would otherwise reach
    YouTube for real.
    """
    import rch.youtube.metadata as meta

    monkeypatch.setattr(
        meta, "get_video_info",
        lambda video_id: {
            "id": video_id,
            "title": "Judul Uji",
            "description": "Deskripsi uji",
            "duration": duration,
            "uploadDate": upload_date,
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "url": f"https://youtu.be/{video_id}",
        },
    )


def stub_metadata_boom(monkeypatch) -> None:
    """Buat pengambilan metadata meledak, untuk menguji jalur degradasi."""
    import rch.youtube.metadata as meta

    def _boom(video_id):
        raise RuntimeError("metadata offline")

    monkeypatch.setattr(meta, "get_video_info", _boom)
