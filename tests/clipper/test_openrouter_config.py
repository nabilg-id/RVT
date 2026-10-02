"""Defaults that reach an external service, and the guards around them.

The previous default OpenRouter model, arcee-ai/trinity-large-preview:free, was
retired from the catalogue. Nothing noticed: select_clips() catches every
exception and falls back to picking segments at random, so a dead model name
means clips get produced that nobody chose. These tests pin the parts that
would have surfaced it.
"""
from __future__ import annotations

import sys
import types

import pytest

from clipper import config as C


class TestOpenRouterDefaults:
    def test_default_model_is_the_stable_router(self):
        # Not a specific model id: openrouter/free is OpenRouter's own router to
        # whatever free model is currently served, so it survives catalogue
        # churn that a hardcoded id does not.
        assert C.OPENROUTER_MODEL == "openrouter/free"

    def test_default_model_is_never_a_retired_specific_id(self):
        # A concrete ":free" id has a shelf life. This one was gone from the
        # catalogue within days of being set as the default.
        assert ":" not in C.OPENROUTER_MODEL

    def test_missing_key_is_empty_not_a_placeholder(self):
        # An unset key must stay falsy: AISelector raises on falsy, which is what
        # turns "no key" into a clear job error instead of a 401 from OpenRouter.
        assert C.OPENROUTER_API_KEY in (None, "")


class TestAISelectorKeyHandling:
    """AISelector raises when the key is missing, and VideoProcessor builds it
    inside the job's try block, so the GUI shows an error rather than crashing.
    """

    @pytest.fixture()
    def selector_module(self, monkeypatch):
        monkeypatch.setattr(C, "OPENROUTER_API_KEY", "")
        mod = pytest.importorskip("clipper.services.ai_selector")
        return mod

    def test_construction_without_key_raises_valueerror(self, selector_module):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            selector_module.AISelector()

    @pytest.fixture()
    def real_selector(self, selector_module, monkeypatch):
        """A genuine AISelector, so the real select_clips() logic is under test."""
        monkeypatch.setattr(selector_module, "OPENROUTER_API_KEY", "sk-or-v1-test")
        return selector_module.AISelector()

    def test_failure_falls_back_instead_of_propagating(self, real_selector):
        """A dead model name or a 402 must not kill the clip: the pipeline
        switches to random selection and still produces output."""

        def _explode(*a, **k):
            raise RuntimeError("model not found")

        real_selector.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(create=_explode)
            )
        )

        segments = [{"start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"kalimat {i}"}
                    for i in range(10)]
        out = real_selector.select_clips(segments, video_duration=50.0, n=2,
                                         min_dur=4, max_dur=6)
        assert len(out) == 2
        assert all("start" in c and "end" in c for c in out)

    def test_fallback_respects_the_requested_bounds(self, real_selector):
        real_selector.client = types.SimpleNamespace(
            chat=types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
                )
            )
        )
        segments = [{"start": i * 5.0, "end": i * 5.0 + 4.0, "text": f"kalimat {i}"}
                    for i in range(10)]
        out = real_selector.select_clips(segments, video_duration=50.0, n=3,
                                         min_dur=4, max_dur=6)
        assert len(out) == 3
        for c in out:
            duration = c["end"] - c["start"]
            assert 0 < duration <= 6
            assert 0 <= c["start"] < c["end"] <= 50.0


class TestOpenRouterDocsStayConsistent:
    """The model id is written down in four places. They drifted before."""

    FILES = ("README.md", "clipper/.env.example", "docs/PANDUAN-TIM.md")

    def test_no_doc_still_names_a_retired_model(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for rel in self.FILES:
            path = root / rel
            assert path.exists(), f"{rel} hilang"
            assert "trinity-large-preview" not in path.read_text(encoding="utf-8"), \
                f"{rel} masih menyebut model OpenRouter yang sudah dihapus"

    def test_doc_example_uses_the_configured_default(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        env_example = (root / "clipper" / ".env.example").read_text(encoding="utf-8")
        assert f"OPENROUTER_MODEL={C.OPENROUTER_MODEL}" in env_example

    def test_env_example_documents_why_a_silent_fallback_matters(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        text = (root / "clipper" / ".env.example").read_text(encoding="utf-8").lower()
        assert "acak" in text, "konsekuensi fallback acak harus ditulis di .env.example"


class TestStaticGuard:
    def test_no_module_reads_the_legacy_gemini_key(self):
        """GEMINI_API_KEY is dead configuration; nothing should consume it."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        offenders = []
        for path in (root / "clipper").rglob("*.py"):
            if "GEMINI_API_KEY" in path.read_text(encoding="utf-8", errors="ignore"):
                if path.name != "config.py":
                    offenders.append(str(path.relative_to(root)))
        assert not offenders, f"masih ada yang membaca GEMINI_API_KEY: {offenders}"

    def test_python_is_executable(self):
        assert sys.executable