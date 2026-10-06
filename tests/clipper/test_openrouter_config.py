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

    def test_missing_key_is_falsy_not_a_placeholder(self, monkeypatch):
        """With nothing configured, the key must be falsy: AISelector raises on
        falsy, which turns "no key" into a clear job error instead of a 401 from
        OpenRouter.

        Asserted on a re-imported config rather than the live one - a developer
        machine with a real key in clipper/.env would otherwise fail this.
        """
        import importlib
        import sys

        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False,
                            raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        sys.modules.pop("clipper.config", None)
        fresh = importlib.import_module("clipper.config")

        assert not fresh.OPENROUTER_API_KEY

        sys.modules.pop("clipper.config", None)
        importlib.import_module("clipper.config")

    def test_a_real_key_is_never_committed_as_a_default(self):
        """config.py must not ship a key. Placeholders in docs are fine; a
        73-character sk-or-v1 value is not."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        source = (root / "clipper" / "config.py").read_text(encoding="utf-8")
        assert "sk-or-v1-" not in source


class TestAISelectorKeyHandling:
    """AISelector raises when the key is missing, and VideoProcessor builds it
    inside the job's try block, so the GUI shows an error rather than crashing.

    Note the patch target: ai_selector does ``from ..config import
    OPENROUTER_API_KEY``, so the value is bound into its own namespace at import
    time. Patching clipper.config has no effect once that import has happened.
    """

    @pytest.fixture()
    def selector_module(self):
        return pytest.importorskip("clipper.services.ai_selector")

    def test_construction_without_key_raises_valueerror(self, selector_module, monkeypatch):
        monkeypatch.setattr(selector_module, "OPENROUTER_API_KEY", "")
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            selector_module.AISelector()

    def test_whitespace_key_also_raises(self, selector_module, monkeypatch):
        # A key made of spaces must not reach OpenRouter as a bearer token.
        monkeypatch.setattr(selector_module, "OPENROUTER_API_KEY", "   ")
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            selector_module.AISelector()

    def test_the_message_tells_the_user_what_to_do(self, selector_module,
                                                  monkeypatch):
        """This string is what a non-developer sees in the GUI when Generate
        fails. Naming the variable without saying where to put it, or how to
        get one, leaves them stuck: the setting is named OPENROUTER_API_KEY and
        has to go in a file called .env, neither of which is guessable from the
        name alone. Every failure of the clip pipeline on a fresh install is
        this one error, so it has to carry the whole remedy.
        """
        monkeypatch.setattr(selector_module, "OPENROUTER_API_KEY", "")

        with pytest.raises(ValueError) as caught:
            selector_module.AISelector()

        message = str(caught.value)
        assert ".env" in message, "the message does not say which file to edit"
        assert "OPENROUTER_API_KEY" in message, "the variable is not named"
        assert "openrouter.ai" in message.lower(), (
            "the message does not say where to obtain a key"
        )

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