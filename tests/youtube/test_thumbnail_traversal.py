"""Security tests: a caller-supplied filename must never escape ``output_dir``.

``download_thumbnail`` accepts an optional ``filename``. Before the fix that
value was used verbatim, so ``../../evil.jpg`` resolved outside the output
directory and the write landed wherever the process could reach. The CLI
reaches this through ``rch download <url> <name> <out>``, which forwards
``name`` straight through, so an untrusted name could overwrite an arbitrary
file the user can write to.

``slugify`` alone is not enough: it is applied to a name that is then joined
onto the output directory, so containment is asserted explicitly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from rch.youtube.thumbnail import download_thumbnail

VIDEO_URL = "https://youtu.be/dQw4w9WgXcQ"
FAKE = lambda url: b"DATA"  # noqa: E731


def _download(tmp_path, filename, size="maxresdefault"):
    return download_thumbnail(
        VIDEO_URL,
        filename=filename,
        output_dir=str(tmp_path / "downloads"),
        http_get=FAKE,
    )


class TestFilenameCannotEscapeOutputDir:
    @pytest.mark.parametrize(
        "hostile",
        [
            "../../pwned.jpg",
            "../pwned.jpg",
            "..\\..\\pwned.jpg",
            "/etc/pwned.jpg",
            "a/../../pwned.jpg",
            "....//....//pwned.jpg",
        ],
    )
    def test_traversal_is_refused_and_nothing_is_written(self, tmp_path, hostile):
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        result = download_thumbnail(
            VIDEO_URL,
            filename=hostile,
            output_dir=str(out),
            http_get=FAKE,
        )

        assert result["status"] is False
        assert "filename" in result["message"].lower()
        assert not [p for p in tmp_path.rglob("*") if p.is_file()]

    def test_nothing_is_written_outside_the_sandbox(self, tmp_path):
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        _download(sandbox, "../../pwned.jpg")

        written = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert not written, f"wrote outside output_dir: {written}"

    def test_absolute_filename_cannot_overwrite_an_existing_file(self, tmp_path):
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)
        target = tmp_path / "outside.jpg"
        target.write_bytes(b"PRE-EXISTING")

        result = download_thumbnail(
            VIDEO_URL,
            filename=str(target),
            output_dir=str(out),
            http_get=FAKE,
        )

        assert result["status"] is False
        assert target.read_bytes() == b"PRE-EXISTING", "overwrote a file outside output_dir"


class TestFilenameIsStillUsable:
    def test_plain_name_is_kept_verbatim(self, tmp_path):
        """An explicit filename is honoured as-is, matching the legacy contract.

        Only the *location* is constrained; the name itself is the caller's
        choice, so a requested extension survives.
        """
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        result = _download(sandbox, "My Cover Art.jpg")

        assert result["status"] is True
        assert Path(result["result"]["path"]).name == "My Cover Art.jpg"

    def test_explicit_name_wins_over_fetched_title(self, tmp_path):
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        result = download_thumbnail(
            VIDEO_URL,
            filename="chosen.jpg",
            output_dir=str(out),
            http_get=FAKE,
            fetch_title=lambda vid: "Fetched Title",
        )

        assert Path(result["result"]["path"]).name == "chosen.jpg"

    def test_no_filename_still_uses_the_title(self, tmp_path):
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        result = download_thumbnail(
            VIDEO_URL,
            output_dir=str(out),
            http_get=FAKE,
            fetch_title=lambda vid: "Fetched Title",
        )

        assert Path(result["result"]["path"]).name == (
            "fetched-title-maxresdefault.jpg"
        )

    def test_traversal_is_rejected_rather_than_silently_rewritten(self, tmp_path):
        """A name that escapes is refused, so the caller learns it was dropped."""
        sandbox = tmp_path / "work"
        out = sandbox / "downloads"
        out.mkdir(parents=True)

        result = _download(sandbox, "../../pwned.jpg")

        assert result["status"] is False
        assert "filename" in result["message"].lower()
