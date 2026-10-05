"""Penangkapan keluaran pipeline clip menjadi progres untuk GUI web.

``services.video_processor`` melaporkan kemajuan lewat ``print()``, bukan lewat
event. Menulis ulang 236 baris pipeline berisiko tinggi, jadiKeluar dari
pembuat job diarahkan ke buffer thread-safe dan baris yang sudah ada diparse
menjadi persen progres.

Dengan begitu GUI bisa menampilkan progres nyata tanpa menyentuh logika
pemotongan video sama sekali.
"""
from __future__ import annotations

import io
import re
import sys
import threading
from typing import Callable, List, Optional, Tuple

# (pola, fase, persen)
_PHASES: Tuple[Tuple[re.Pattern, str, float], ...] = (
    (re.compile(r"Downloading video|Downloading video with yt-dlp"), "Mengunduh video", 0.05),
    (re.compile(r"Starting transcription|Transcribing"), "Transkripsi Whisper", 0.15),
    (re.compile(r"Using AI to select the most viral|AI to select"), "AI memilih momen", 0.34),
    (re.compile(r"Processing (\d+) viral clips"), "Memotong clip", 0.40),
    (re.compile(r"Encoding with optimized"), "Encoding", 0.80),
    (re.compile(r"Video encoding complete"), "Encoding", 0.90),
    (re.compile(r"Done! Enjoy your viral clips|VIRAL CLIPS GENERATED"), "Selesai", 1.0),
)

_CLIP_RE = re.compile(r"Clip (\d+)/(\d+):")
_CLIP_BASE = 0.40
_CLIP_SPAN = 0.45

_CLIPS_MARKER = "Generated Clips:"

#: Status a cancelled job settles in. Its own status rather than "done",
#: because reporting done would claim a completion the user never got.
CANCELLED = "cancelled"

#: Statuses that will never advance again. Used to decide whether a cancel
#: request still means anything.
_FINISHED_STATUSES = frozenset({"done", "error", CANCELLED})


class _Tee(io.TextIOBase):
    """Meneruskan keluaran ke underlying stream sambil menyalin ke buffer.

   moviepy menulis progres ke ``stderr`` saat ``write_videofile`` berjalan,
    jadi kedua stream harus ditangkap, bukan hanya stdout.
    """

    def __init__(self, original, sink: Callable[[str], None]):
        self._original = original
        self._sink = sink

    def write(self, text: str) -> int:
        if text and text.strip():
            self._sink(text.rstrip("\n"))
        try:
            return self._original.write(text)
        except (ValueError, OSError):
            return len(text)

    def flush(self) -> None:
        try:
            self._original.flush()
        except (ValueError, OSError):
            pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def parse_progress(line: str) -> Optional[Tuple[str, float]]:
    """Terjemahkan satu baris log menjadi ``(fase, persen)``.

    Mengembalikan ``None`` untuk baris yang bukan penanda kemajuan, sehingga
    pemanggil bisa membiarkan baris itu lewat tanpa mengubah progres.
    """
    for pattern, phase, pct in _PHASES:
        if pattern.search(line):
            return phase, pct

    m = _CLIP_RE.search(line)
    if m:
        index, total = int(m.group(1)), int(m.group(2))
        if total > 0:
            frac = min(1.0, max(0.0, (index - 1) / total))
            return "Memotong clip", _CLIP_BASE + _CLIP_SPAN * frac
    return None


class Job:
    """Status satu proses clip yang sedang berjalan."""

    def __init__(self, job_id: int, request: dict):
        self.id = job_id
        self.request = request
        self.status = "running"
        self.phase = "Menyiapkan"
        self.progress = 0.0
        self.title: Optional[str] = None
        self.outputs: List[str] = []
        self.log: List[str] = []
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        self._truncated = False
        self._clips_block_seen = False
        self._history_written = False
        #: Source YouTube id, set once the job starts so the history row and the
        #: shared ledger can both name the video a clip came from.
        self.video_id: Optional[str] = None
        #: Set by ``cancel()`` once the user asks for this job to stop. Read by
        #: the workers between items, so cancelling stops the *next* one rather
        #: than killing the item already in flight.
        self.cancelled = False

    def claim_history(self) -> bool:
        """True sekali saja, saat job pertama kali dicatat ke riwayat."""
        with self._lock:
            if self._history_written:
                return False
            self._history_written = True
            return True

    def append_log(self, line: str) -> None:
        """Simpan baris log dan perbarui progres bila baris itu penanda."""
        with self._lock:
            self.log.append(line)
            parsed = parse_progress(line)
            if parsed is not None:
                self.phase, self.progress = parsed
            if _CLIPS_MARKER in line:
                self._clips_block_seen = True
            if self._clips_block_seen:
                # Daftar file menyusul setelah penanda, jadi harus diurai ulang
                # pada setiap baris berikutnya, bukan hanya saat penanda masuk.
                self._collect_outputs()
            if len(self.log) > 2000:
                self._truncated = True
                del self.log[:1000]

    def _collect_outputs(self) -> None:
        """Baca daftar file clip dari blok ``Generated Clips:`` di log."""
        start = next(
            (i for i, ln in enumerate(self.log) if _CLIPS_MARKER in ln),
            None,
        )
        if start is None:
            return
        found = []
        for line in self.log[start + 1:]:
            stripped = line.strip()
            if not stripped.startswith("-"):
                if found:
                    break
                continue
            name = stripped.lstrip("-").split("(")[0].strip()
            if name:
                found.append(name)
        self.outputs = found

    def set_title(self, title: Optional[str]) -> None:
        with self._lock:
            self.title = title

    def cancel(self) -> bool:
        """Ask this job to stop; True if this call raised the flag.

        A no-op on a job that already settled: a user clicking the button a
        moment late must not have a completed run rewritten as cancelled.
        """
        with self._lock:
            if self.status in _FINISHED_STATUSES:
                return False
            self.cancelled = True
            return True

    def finish(self, error: Optional[str] = None) -> None:
        """Settle the job, honouring a cancel that arrived before now.

        A cancelled job settles in its own status rather than "done" - reporting
        done would claim a completion the user never got - and it keeps whatever
        progress it had reached instead of jumping to 100%.
        """
        with self._lock:
            if error is not None:
                self.status = "error"
                self.error = error
                return
            if self.cancelled:
                self.status = CANCELLED
                self.phase = "Dibatalkan"
                return
            self.status = "done"
            self.error = None
            self.progress = 1.0
            self.phase = "Selesai"

    def snapshot(self, log_tail: int = 200) -> dict:
        """Salinan aman untuk diserialisasi ke JSON.

        List ``log`` ikut disalin; mengembalikan objek langsung akan membuat
        thread pekerja dan thread web membaca list yang sama.
        """
        with self._lock:
            return {
                "jobId": self.id,
                # One registry holds clip jobs and download jobs, so a client
                # needs to be able to say which kind it is looking at.
                "kind": "clip",
                "status": self.status,
                "phase": self.phase,
                "progress": round(self.progress, 4),
                "title": self.title,
                "outputs": list(self.outputs),
                "error": self.error,
                "cancelled": self.cancelled,
                "log": self.log[-log_tail:],
                "logTruncated": self._truncated,
            }


class StreamCapture:
    """Context manager yang mengarahkan stdout dan stderr ke ``sink``."""

    def __init__(self, sink: Callable[[str], None]):
        self._sink = sink
        self._saved: List = []

    def __enter__(self) -> "StreamCapture":
        for attr in ("stdout", "stderr"):
            original = getattr(sys, attr)
            self._saved.append((attr, original))
            setattr(sys, attr, _Tee(original, self._sink))
        return self

    def __exit__(self, *exc_info) -> None:
        for attr, original in reversed(self._saved):
            setattr(sys, attr, original)
        self._saved.clear()
