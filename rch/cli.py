"""RCH command-line interface (click).

Parity target: the Node.js ``bin/rch.js``. Every legacy subcommand is
preserved — ``thumbnail``, ``channel``, ``list``, ``info``, ``video``,
``download``, ``channel-full``, ``channel-info``, ``channel-video``, and
``web``/``gui``/``serve``.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional

import click

from . import __version__
from .config import CONFIG
from .core.events import phase_label
from .core.export import export_metadata
from .core.paths import default_product_dir
from .core.report import append_history, write_report

#: Harvest output defaults to a branded folder inside the operating system's
#: Downloads folder, not to ./downloads relative to whatever directory the
#: command was started from.
DEFAULT_OUT = str(default_product_dir())
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8787

_PROGRESS_BAR_WIDTH = 30
_PROGRESS_LABEL_MAX = 40


def _abs(path: str) -> str:
    return os.path.abspath(path)


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _progress_line(done, total, label: str = "") -> str:
    """Render one progress update, mirroring legacy ``progressBar``."""
    ratio = min(1.0, done / total) if total else 0.0
    filled = round(ratio * _PROGRESS_BAR_WIDTH)
    bar = "█" * filled + "░" * (_PROGRESS_BAR_WIDTH - filled)
    pct = f"{round(ratio * 100):3d}"
    text = str(label or "")
    if len(text) > _PROGRESS_LABEL_MAX:
        text = text[:_PROGRESS_LABEL_MAX] + "..."
    return f"  [{bar}] {pct}%  ({done}/{total})  {text}".rstrip()


def _print_progress(emitter) -> None:
    """Render engine events on stderr.

    Subscribes to the events ``rch.youtube.channel`` actually emits —
    ``progress``, ``phase`` and ``video:done``. ``video:done`` carries an
    ``ok`` flag rather than arriving as separate ``item:ok``/``item:fail``
    events, so the success and failure lines are both driven from it.
    """
    if emitter is None:
        return

    def on_progress(payload) -> None:
        data = payload or {}
        done, total = data.get("done"), data.get("total")
        if not total:
            return
        click.echo(_progress_line(done, total, data.get("label", "")), err=True)

    def on_phase(payload) -> None:
        click.echo(f"[*] {phase_label(payload)}", err=True)

    def on_video_done(payload) -> None:
        data = payload or {}
        if data.get("ok"):
            mark, detail = "[OK]", data.get("title", "")
        else:
            mark, detail = "[!!]", data.get("error") or ""
        click.echo(f"  {mark} {data.get('id')} {detail}".rstrip(), err=True)

    emitter.on("progress", on_progress)
    emitter.on("phase", on_phase)
    emitter.on("video:done", on_video_done)


def _export(metadata: Dict[str, Any], csv_path: Optional[str], json_path: Optional[str]) -> None:
    if csv_path:
        click.echo(f"CSV: {_abs(export_metadata(list(metadata.values()), 'csv', csv_path))}")
    if json_path:
        click.echo(f"JSON: {_abs(export_metadata(list(metadata.values()), 'json', json_path))}")


def _shared_options(func):
    """Attach the legacy global flags to a command."""
    decorators = [
        click.option("--out", default=DEFAULT_OUT, show_default=True, help="Direktori output."),
        click.option("--limit", type=int, default=None, help="Batas jumlah video."),
        click.option("--cookies", default=None, help="Browser untuk cookies (chrome, firefox, edge)."),
        click.option("--quality", default=CONFIG["quality"], show_default=True, help="Kualitas video."),
        click.option("--concurrency", type=int, default=CONFIG["concurrency"], show_default=True, help="Jumlah worker paralel."),
        click.option("--min-duration", type=int, default=None, help="Durasi minimum (detik)."),
        click.option("--max-duration", type=int, default=None, help="Durasi maksimum (detik)."),
        click.option("--after", default=None, help="Filter tanggal upload (YYYYMMDD)."),
        click.option("--csv", "csv_path", default=None, help="Simpan metadata ke CSV."),
        click.option("--json", "json_path", default=None, help="Simpan metadata ke JSON."),
        click.option("--sub-lang", default=None, help="Bahasa subtitle."),
        click.option("--shorts", is_flag=True, help="Sertakan Shorts."),
        click.option("--subtitles", is_flag=True, help="Unduh subtitle."),
        click.option("--resume", is_flag=True, help="Lanjutkan dari checkpoint."),
    ]
    for deco in reversed(decorators):
        func = deco(func)
    return func


def _build_options(**kwargs) -> Dict[str, Any]:
    """Translate click params into the option dict the engine expects."""
    out = kwargs.get("out") or DEFAULT_OUT
    cookies = kwargs.get("cookies") or ""
    if cookies:
        os.environ["RCH_COOKIES"] = cookies
    return {
        "outputDir": out,
        "limit": kwargs.get("limit"),
        "cookies": cookies,
        "quality": kwargs.get("quality") or CONFIG["quality"],
        "concurrency": kwargs.get("concurrency") or CONFIG["concurrency"],
        "minDuration": kwargs.get("min_duration"),
        "maxDuration": kwargs.get("max_duration"),
        "after": kwargs.get("after"),
        "csv": kwargs.get("csv_path"),
        "json": kwargs.get("json_path"),
        "subLang": kwargs.get("sub_lang"),
        "shorts": bool(kwargs.get("shorts")),
        "subtitles": bool(kwargs.get("subtitles")),
        "resume": bool(kwargs.get("resume")),
        "size": kwargs.get("size") or None,
    }


@click.group(invoke_without_command=True)
@click.version_option(__version__, "-V", "--version", message="%(version)s")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Ridikc Video Toolkit — unduh video, thumbnail, dan metadata YouTube."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
@click.argument("url")
@click.argument("size", required=False, default=None)
@click.option("--out", default=None, help="Direktori output.")
@click.option("--zip", "make_zip", is_flag=True, help="Kemas hasil ke ZIP.")
def thumbnail(url: str, size: Optional[str], out: Optional[str], make_zip: bool) -> None:
    """Unduh thumbnail dari URL video YouTube.

    SIZE opsional: maxresdefault, sddefault, hqdefault, mqdefault.
    """
    from .youtube import thumbnail as thumb

    result = thumb.download_thumbnail(
        url,
        size=size or "maxresdefault",
        output_dir=out or f"./downloads/{_timestamp()}",
    )
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)
    res = result["result"]
    click.echo(f"[OK] {res.get('title', res.get('id', ''))}")
    click.echo(f"     Disimpan: {_abs(res.get('path', ''))}")
    if res.get("zipPath"):
        click.echo(f"ZIP: {_abs(res['zipPath'])}")


@cli.command()
@click.argument("url")
@click.argument("size", required=False, default=None)
@_shared_options
def channel(url: str, size: Optional[str], **kwargs) -> None:
    """Unduh semua thumbnail dari sebuah channel.

    SIZE opsional: maxresdefault, sddefault, hqdefault, mqdefault.
    """
    from .youtube import metadata as meta
    from .youtube import thumbnail as thumb

    thumb_size = size or "maxresdefault"
    options = _build_options(**kwargs)
    limit = options.get("limit")

    click.echo("Mengambil daftar video dari channel...")
    try:
        ids = meta.get_channel_ids(url)
    except Exception as exc:  # noqa: BLE001 - user-facing CLI message
        click.echo(
            f"[!] Gagal ambil daftar video: {exc}\n"
            "    Pastikan yt-dlp terpasang (yt-dlp --version).",
            err=True,
        )
        sys.exit(1)

    if limit:
        ids = ids[: int(limit)]

    click.echo(f"Ditemukan {len(ids)} video. Mendownload thumbnail ({thumb_size})...")

    output_dir = options["outputDir"] or f"./downloads/channel-{_timestamp()}"
    result = thumb.download_thumbnails(
        [f"https://youtu.be/{vid}" for vid in ids],
        size=thumb_size,
        output_dir=output_dir,
        concurrency=options["concurrency"],
        zip=True,
        zip_name=f"channel-thumbnails-{thumb_size}.zip",
    )
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)

    res = result["result"]
    click.echo("\n=== HASIL ===")
    click.echo(f"Total: {res.get('total')}")
    click.echo(f"Sukses: {res.get('success')}")
    click.echo(f"Gagal: {res.get('failed')}")
    if res.get("zipPath"):
        click.echo(f"ZIP: {_abs(res['zipPath'])}")

    fails = [i for i in res.get("items", []) if not i.get("status")]
    if fails:
        click.echo("\nGagal (biasanya video dihapus/private):")
        for f in fails:
            click.echo(f"  {f.get('url', '-')}")

    report = {
        "command": "channel",
        "channel": url,
        "total": res.get("total"),
        "success": res.get("success"),
        "failed": res.get("failed"),
        "zipPath": res.get("zipPath"),
        "fails": [{"id": f.get("url", "-"), "error": f.get("message", "")} for f in fails],
    }
    click.echo(f"Report: {_abs(write_report(output_dir, report))}")
    append_history(output_dir, report)


@cli.command("list")
@click.argument("url")
@click.argument("out", required=False, default=None)
@click.option("--limit", type=int, default=None, help="Batas jumlah video.")
def list_cmd(url: str, out: Optional[str], limit: Optional[int]) -> None:
    """Tampilkan daftar ID video dari channel."""
    from .youtube import channel as chan

    ids = chan.list_ids(url, {"limit": limit})
    click.echo("\n".join(ids))
    if out:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(ids))
        click.echo(f"Ditulis: {_abs(out)}")


@cli.command()
@click.argument("ids", nargs=-1, required=True)
@click.option("--json", "json_path", default=None, help="Simpan metadata ke JSON.")
@click.option("--csv", "csv_path", default=None, help="Simpan metadata ke CSV.")
def info(ids, json_path: Optional[str], csv_path: Optional[str]) -> None:
    """Ambil metadata (judul, deskripsi, durasi) dari ID video."""
    from .youtube import metadata as meta

    records = meta.get_video_info_batch(list(ids))
    for vid, rec in records.items():
        click.echo(f"{vid}\t{rec.get('title', '')}\t{rec.get('duration', '')}")
    _export(records, csv_path, json_path)


@cli.command()
@click.argument("url")
@click.option("--limit", type=int, default=None, help="Batas jumlah video.")
@click.option("--csv", "csv_path", default=None, help="Simpan metadata ke CSV.")
@click.option("--json", "json_path", default=None, help="Simpan metadata ke JSON.")
def playlist(url: str, limit: Optional[int], csv_path: Optional[str],
             json_path: Optional[str]) -> None:
    """Tampilkan daftar video dari sebuah playlist YouTube."""
    from .youtube import playlist as pl

    result = pl.playlist_metadata(url, {"limit": limit})
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)

    res = result["result"]
    items = res.get("items") or []
    click.echo(f"Playlist: {res.get('title', '')}")
    click.echo(f"Author : {res.get('author', '')}")
    click.echo(f"Total  : {res.get('itemCount', len(items))}")
    click.echo("")

    # One normalisation pass feeds both the listing and the export, so a row on
    # screen and a row in the CSV can never disagree.
    records = pl.to_metadata(items)
    for record in records.values():
        click.echo(f"{record['id']}\t{record['title']}\t{record['duration'] or ''}")

    _export(records, csv_path, json_path)


@cli.command()
@click.argument("url")
@click.argument("name", required=False)
@click.option("--mp3", is_flag=True, help="Unduh sebagai MP3.")
@click.option("--quality", default=CONFIG["quality"], show_default=True, help="Kualitas video.")
@click.option("--out", default=DEFAULT_OUT, show_default=True, help="Direktori output.")
@click.option("--cookies", default=None, help="Browser untuk cookies.")
@click.option("--subtitles", is_flag=True, help="Unduh subtitle.")
@click.option("--sub-lang", default=None, help="Bahasa subtitle.")
def video(url: str, name: Optional[str], mp3: bool, quality: str, out: str,
          cookies: Optional[str], subtitles: bool, sub_lang: Optional[str]) -> None:
    """Unduh satu video sebagai MP4 atau MP3."""
    from .youtube import video as vid

    if cookies:
        os.environ["RCH_COOKIES"] = cookies
    result = vid.download(url, {
        "outputDir": out,
        "format": "mp3" if mp3 else "mp4",
        "quality": quality,
        "cookies": cookies or "",
        "filename": name or "",
        "subtitles": subtitles,
        "subLang": sub_lang,
    })
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)
    res = result["result"]
    click.echo(f"[OK] {res.get('title')} -> {res.get('path')}")


@cli.command()
@click.argument("url")
@click.argument("name", required=False)
@click.argument("out", required=False, default=DEFAULT_OUT)
@click.option("--zip", "make_zip", is_flag=True, help="Kemas hasil ke ZIP.")
def download(url: str, name: Optional[str], out: str, make_zip: bool) -> None:
    """Unduh video dan thumbnail sekaligus."""
    from .youtube import thumbnail as thumb
    from .youtube import video as vid

    vid_result = vid.download(url, {"outputDir": out, "filename": name or ""})
    if not vid_result.get("status"):
        click.echo(f"[!] Video: {vid_result.get('message')}", err=True)
        sys.exit(1)
    click.echo(f"[OK] Video: {vid_result['result'].get('path')}")

    thumb_result = thumb.download_thumbnail(url, name=name, output_dir=out, zip=make_zip)
    if thumb_result.get("status"):
        click.echo(f"[OK] Thumbnail: {thumb_result['result'].get('path')}")
    else:
        click.echo(f"[!] Thumbnail: {thumb_result.get('message')}", err=True)


@cli.command("channel-full")
@click.argument("url")
@click.argument("size", required=False)
@_shared_options
def channel_full_cmd(url: str, size: Optional[str], **kwargs) -> None:
    """Unduh video + thumbnail + metadata lengkap dari channel."""
    from .youtube import channel as chan

    options = _build_options(size=size, **kwargs)
    emitter = _make_emitter()
    _print_progress(emitter)
    result = chan.channel_full(url, options, emitter=emitter)
    _finish_channel_result(result, "channel-full", url, options["outputDir"])


@cli.command("channel-video")
@click.argument("url")
@_shared_options
def channel_video_cmd(url: str, **kwargs) -> None:
    """Unduh video saja dari channel."""
    from .youtube import channel as chan

    options = _build_options(**kwargs)
    emitter = _make_emitter()
    _print_progress(emitter)
    result = chan.channel_video(url, options, emitter=emitter)
    _finish_channel_result(result, "channel-video", url, options["outputDir"])


@cli.command("channel-info")
@click.argument("url")
@click.argument("size", required=False)
@_shared_options
def channel_info_cmd(url: str, size: Optional[str], **kwargs) -> None:
    """Tampilkan informasi channel tanpa mengunduh."""
    from .youtube import channel as chan

    options = _build_options(size=size, **kwargs)
    result = chan.channel_info(url, options)
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)
    click.echo(json.dumps(result["result"], indent=2, ensure_ascii=False))
    items = result["result"].get("items") or []
    _export(
        {item.get("videoId") or item.get("id"): item for item in items},
        kwargs.get("csv_path"),
        kwargs.get("json_path"),
    )


@cli.command()
@click.option("--host", default=DEFAULT_WEB_HOST, show_default=True, help="Host bind.")
@click.option("--port", type=int, default=DEFAULT_WEB_PORT, show_default=True, help="Port.")
def web(host: str, port: int) -> None:
    """Jalankan GUI web lokal di browser.

    Menjalankan app yang sama dengan ``python -m clipper.app``: satu proses
    melayani halaman clipper dan halaman downloader, jadi ``/api/status`` membaca
    kedua jenis job dari satu registry.
    """
    from clipper.app import run_server

    run_server(host=host, port=port)


@cli.command()
@click.option("--host", default=DEFAULT_WEB_HOST, show_default=True)
@click.option("--port", type=int, default=DEFAULT_WEB_PORT, show_default=True)
def gui(host: str, port: int) -> None:
    """Alias dari 'web'."""
    from clipper.app import run_server

    run_server(host=host, port=port)


@cli.command()
@click.option("--host", default=DEFAULT_WEB_HOST, show_default=True)
@click.option("--port", type=int, default=DEFAULT_WEB_PORT, show_default=True)
def serve(host: str, port: int) -> None:
    """Alias dari 'web'."""
    from clipper.app import run_server

    run_server(host=host, port=port)


def _make_emitter():
    from .core.events import create_emitter

    return create_emitter()


def _finish_channel_result(result: Dict, command: str, url: str, out_dir: str) -> None:
    if not result.get("status"):
        click.echo(f"[!] {result.get('message', 'Gagal')}", err=True)
        sys.exit(1)
    res = result["result"]
    click.echo(f"Total: {res.get('total')}  Sukses: {res.get('success')}  Gagal: {res.get('failed')}")
    if res.get("zipPath"):
        click.echo(f"ZIP: {_abs(res['zipPath'])}")
    fails = [i for i in res.get("items", []) if not i.get("ok")]
    if fails:
        click.echo("\nGagal pada:")
        for f in fails:
            click.echo(f"  {f.get('videoId')} -> {f.get('error')}")
    report = {
        "command": command,
        "channel": url,
        "total": res.get("total"),
        "success": res.get("success"),
        "failed": res.get("failed"),
        "zipPath": res.get("zipPath"),
        "fails": [{"id": f.get("videoId"), "error": f.get("error")} for f in fails],
        # Recorded so the dashboard can show which videos a run covered rather
        # than only the aggregate counts.
        "videoIds": [i.get("videoId") for i in res.get("items", [])
                     if i.get("videoId")],
    }
    click.echo(f"Report: {_abs(write_report(out_dir, report))}")
    append_history(out_dir, report)


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
