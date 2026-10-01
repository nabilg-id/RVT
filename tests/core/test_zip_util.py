"""Tests for rch.core.zip_util — ZIP creation and the streaming writer.

Every test works inside pytest's ``tmp_path`` and validates the resulting
archive by reading it back with :mod:`zipfile`, so assertions are about the
bytes a consumer would actually receive (names, payloads, sizes).
"""
from __future__ import annotations

import zipfile

import pytest

from rch.core.zip_util import ZipStream, create_zip_from_files


def _read_names(path):
    with zipfile.ZipFile(str(path)) as zf:
        return zf.namelist()


class TestCreateZipFromFiles:
    def test_writes_single_file_with_arcname(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("hello", encoding="utf-8")
        out = tmp_path / "out" / "bundle.zip"

        size = create_zip_from_files([(str(src), "greeting.txt")], str(out))

        assert out.exists()
        assert size == out.stat().st_size
        assert _read_names(out) == ["greeting.txt"]

    def test_preserves_file_contents(self, tmp_path):
        src = tmp_path / "source.bin"
        src.write_bytes(b"\x00\x01\x02payload")
        out = tmp_path / "bundle.zip"

        create_zip_from_files([(str(src), "source.bin")], str(out))

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("source.bin") == b"\x00\x01\x02payload"

    def test_creates_missing_parent_directories(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "deeply" / "nested" / "path" / "bundle.zip"

        create_zip_from_files([(str(src), "a.txt")], str(out))

        assert out.exists()

    def test_uses_supplied_arcname_not_source_basename(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("x", encoding="utf-8")
        out = tmp_path / "bundle.zip"

        create_zip_from_files([(str(src), "nested/custom-name.txt")], str(out))

        assert _read_names(out) == ["nested/custom-name.txt"]

    def test_skips_missing_source_files(self, tmp_path):
        present = tmp_path / "present.txt"
        present.write_text("y", encoding="utf-8")
        absent = tmp_path / "absent.txt"
        out = tmp_path / "bundle.zip"

        create_zip_from_files(
            [(str(present), "present.txt"), (str(absent), "absent.txt")],
            str(out),
        )

        assert _read_names(out) == ["present.txt"]

    def test_all_missing_files_still_produces_readable_archive(self, tmp_path):
        out = tmp_path / "bundle.zip"

        size = create_zip_from_files([(str(tmp_path / "nope.txt"), "nope.txt")], str(out))

        assert size == out.stat().st_size
        assert _read_names(out) == []

    def test_empty_file_list_produces_valid_archive(self, tmp_path):
        out = tmp_path / "empty.zip"

        size = create_zip_from_files([], str(out))

        assert size > 0
        assert _read_names(out) == []

    def test_handles_multiple_files_and_paths(self, tmp_path):
        first = tmp_path / "a.txt"
        first.write_text("alpha", encoding="utf-8")
        second = tmp_path / "b.txt"
        second.write_text("beta", encoding="utf-8")
        out = tmp_path / "bundle.zip"

        create_zip_from_files([(str(first), "a.txt"), (str(second), "dir/b.txt")], str(out))

        assert sorted(_read_names(out)) == ["a.txt", "dir/b.txt"]

    def test_accepts_path_objects(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("z", encoding="utf-8")
        out = tmp_path / "bundle.zip"

        create_zip_from_files([(src, "source.txt")], out)

        assert _read_names(out) == ["source.txt"]

    def test_uses_deflate_compression(self, tmp_path):
        src = tmp_path / "big.txt"
        src.write_text("a" * 4096, encoding="utf-8")
        out = tmp_path / "bundle.zip"

        create_zip_from_files([(str(src), "big.txt")], str(out))

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.getinfo("big.txt").compress_type == zipfile.ZIP_DEFLATED

    def test_overwrites_existing_archive(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("new", encoding="utf-8")
        out = tmp_path / "bundle.zip"
        out.write_text("stale content", encoding="utf-8")

        create_zip_from_files([(str(src), "source.txt")], str(out))

        assert _read_names(out) == ["source.txt"]


class TestZipStream:
    def test_add_file_appends_entry(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("content", encoding="utf-8")
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_file(str(src), "source.txt")
        size = stream.finalize()

        assert size == out.stat().st_size
        assert _read_names(out) == ["source.txt"]

    def test_add_file_skips_missing_source(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_file(str(tmp_path / "missing.txt"), "missing.txt")
        stream.finalize()

        assert _read_names(out) == []

    def test_add_buffer_accepts_str_and_encodes_utf8(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_buffer("halo dunia", "note.txt")
        stream.finalize()

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("note.txt").decode("utf-8") == "halo dunia"

    def test_add_buffer_accepts_bytes_unchanged(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_buffer(b"\x00\xffraw", "raw.bin")
        stream.finalize()

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("raw.bin") == b"\x00\xffraw"

    def test_add_buffer_accepts_bytearray(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_buffer(bytearray(b"abc"), "ba.bin")
        stream.finalize()

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("ba.bin") == b"abc"

    def test_combines_files_and_buffers(self, tmp_path):
        src = tmp_path / "source.txt"
        src.write_text("from-disk", encoding="utf-8")
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_file(str(src), "from-disk.txt")
        stream.add_buffer("from-memory", "from-memory.txt")
        stream.finalize()

        assert sorted(_read_names(out)) == ["from-disk.txt", "from-memory.txt"]

    def test_multiple_buffers_preserve_order_and_content(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        for index in range(3):
            stream.add_buffer(f"entry-{index}", f"e{index}.txt")
        stream.finalize()

        with zipfile.ZipFile(str(out)) as zf:
            assert [zf.read(f"e{i}.txt").decode("utf-8") for i in range(3)] == [
                "entry-0",
                "entry-1",
                "entry-2",
            ]

    def test_creates_missing_parent_directories(self, tmp_path):
        out = tmp_path / "deeply" / "nested" / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_buffer("x", "x.txt")
        stream.finalize()

        assert out.exists()

    def test_accepts_path_objects(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(out)
        stream.add_buffer("x", "x.txt")
        stream.finalize()

        assert _read_names(out) == ["x.txt"]

    def test_finalize_returns_archive_size_in_bytes(self, tmp_path):
        out = tmp_path / "stream.zip"
        payload = "x" * 1000

        stream = ZipStream(str(out))
        stream.add_buffer(payload, "big.txt")
        size = stream.finalize()

        assert size == out.stat().st_size
        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("big.txt").decode("utf-8") == payload

    def test_finalize_on_empty_stream_returns_size(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        size = stream.finalize()

        assert size == out.stat().st_size
        assert _read_names(out) == []

    def test_file_is_open_until_finalize(self, tmp_path):
        out = tmp_path / "stream.zip"

        stream = ZipStream(str(out))
        stream.add_buffer("pending", "pending.txt")
        with pytest.raises(Exception):
            with zipfile.ZipFile(str(out)) as zf:
                zf.read("pending.txt")

        stream.finalize()

        with zipfile.ZipFile(str(out)) as zf:
            assert zf.read("pending.txt").decode("utf-8") == "pending"
