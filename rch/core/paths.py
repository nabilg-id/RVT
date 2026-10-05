"""Where downloaded videos go, per operating system.

The harvest output used to default to ``./downloads``, a path relative to
whatever directory the command happened to be launched from. That is a bug the
clipper already learned about once - see the ``_env_path`` docstring in
clipper/config.py, where a relative OUTPUT_DIR made Flask serve downloads from
the wrong place - so the harvester should not repeat it.

This resolves the operating system's own Downloads folder instead:

- Windows: through ``SHGetKnownFolderPath``. Reading
  ``%USERPROFILE%\\Downloads`` is not good enough, because OneDrive commonly
  relocates the real folder and the plain path then points at nothing.
- macOS: ``~/Downloads``.
- Linux: ``XDG_DOWNLOAD_DIR`` from ``~/.config/user-dirs.dirs`` when the user
  has set one, otherwise ``~/Downloads``.

Every step degrades rather than raises: an unknown platform, a registry that
refuses to answer, or a container with no home directory all fall back to the
repo-local ``downloads/`` so the app still works.

``RCH_DOWNLOADS_DIR`` overrides everything, which is how the tests aim it at
``tmp_path`` rather than writing into a developer's real Downloads folder.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

#: FOLDERID_Downloads - the GUID, not the path, because the path moves.
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"

_XDG_LINE = re.compile(r'^\s*XDG_DOWNLOAD_DIR\s*=\s*"(?P<path>[^"]*)"')
_USER_DIRS_RELATIVE = Path(".config") / "user-dirs.dirs"

#: Everything the toolkit writes lands in this one folder inside the OS
#: Downloads folder. The two features - the clip generator and the channel
#: harvester - would otherwise drop loose ``clips`` and ``downloads`` folders
#: next to each other in a folder the user also fills with unrelated files.
PRODUCT_FOLDER = "Ridikc Video Toolkit"

#: Subfolder for rendered clips, so clip output and harvested video stay apart
#: inside the product folder.
CLIP_SUBDIR = "clips"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _from_environment() -> Path | None:
    """Honour an explicit override, expanded so ``~`` works."""
    raw = (os.getenv("RCH_DOWNLOADS_DIR") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _windows_downloads() -> Path | None:
    """Ask the shell for the real Downloads folder.

    ``SHGetKnownFolderPath`` is the supported way to resolve a known folder, and
    the only one that follows OneDrive redirection. The ctypes binding lives in
    :func:`_shell_downloads_path` so this decision can be tested on any
    platform and on CI, where the shell is never called.
    """
    raw = _shell_downloads_path()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _shell_downloads_path() -> str | None:
    """Resolve FOLDERID_Downloads to a path string, or None if unavailable.

    Returns None rather than raising on every failure mode: not Windows, no
    ctypes, an unregistered GUID, a shell that refuses, or an empty answer.
    Every caller has a working fallback.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - not on Windows, or ctypes unavailable
        return None

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    try:
        folder_id = _GUID()
        ole32 = ctypes.WinDLL("ole32")
        shell32 = ctypes.WinDLL("shell32")

        if ole32.CLSIDFromString(
            ctypes.c_wchar_p(_FOLDERID_DOWNLOADS), ctypes.byref(folder_id)
        ) != 0:
            return None

        out = ctypes.c_wchar_p()
        # KNOWNFOLDERID flag 0; passing the default is safe because the
        # per-user override for Downloads already exists by definition.
        if shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(out)
        ) != 0:
            return None
        try:
            return out.value
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:  # noqa: BLE001 - any shell failure falls through
        return None


def _linux_xdg_downloads() -> Path | None:
    """Read the XDG user-dirs setting, if the desktop has written one."""
    try:
        home = Path.home()
        config = home / _USER_DIRS_RELATIVE
        if not config.is_file():
            return None
        for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue
            match = _XDG_LINE.match(line)
            if not match:
                continue
            raw = match.group("path").replace("$HOME", str(home)).replace(
                "${HOME}", str(home)
            )
            path = Path(raw).expanduser()
            if path.is_dir():
                return path
    except Exception:  # noqa: BLE001 - a broken config is not fatal
        return None
    return None


def default_downloads_dir() -> Path:
    """The operating system's Downloads folder, or a repo-local fallback.

    Always returns an absolute path. Callers get the same directory whether the
    command was started from the repo, from ``~``, or from a service working
    directory.
    """
    override = _from_environment()
    if override is not None:
        return override if override.is_absolute() else (_repo_root() / override)

    candidates = []
    if sys.platform == "win32":
        candidates.append(_windows_downloads)
        home = os.environ.get("USERPROFILE")
        if home:
            candidates.append(lambda: Path(home) / "Downloads")
    else:
        if sys.platform.startswith("linux"):
            candidates.append(_linux_xdg_downloads)
        try:
            candidates.append(lambda: Path.home() / "Downloads")
        except Exception:  # noqa: BLE001 - no home directory in this container
            pass

    for candidate in candidates:
        try:
            path = candidate()
        except Exception:  # noqa: BLE001 - try the next strategy
            continue
        if path is not None and path.is_dir():
            return path

    # Nothing usable: stay inside the repo rather than writing somewhere
    # surprising or failing outright.
    return _repo_root() / "downloads"


def default_product_dir() -> Path:
    """The toolkit's own folder inside the OS Downloads folder.

    Channel harvests and rendered clips are different kinds of output, so they
    get their own subfolder under one branded parent rather than both landing
    loose in a folder the user also fills with unrelated files.

    Falls back with :func:`default_downloads_dir`, which itself falls back to
    the repo, so this never returns a path that cannot be created.
    """
    return default_downloads_dir() / PRODUCT_FOLDER


def default_clip_output_dir() -> Path:
    """Where rendered clips go by default."""
    return default_product_dir() / CLIP_SUBDIR


def ensure_dir(path: Path) -> Path:
    """Create the directory if needed, falling back to a repo-local one.

    A read-only or unavailable Downloads folder should not stop a harvest, so
    an unusable target moves the work into ``<repo>/downloads`` and says so,
    rather than failing later inside yt-dlp with an opaque error.
    """
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        fallback = _repo_root() / "downloads"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    return path