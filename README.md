<div align="center">

# Ridikc Content Harvester

**YouTube Downloader & Media Harvester for Python.**

_Extract direct MP4, MP3, thumbnail, and metadata links from YouTube — built for internal R&D use by Ridikc._

[![python version](https://img.shields.io/badge/python-3.10%2B-61afef.svg?style=flat-square)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

> 🔒 **INTERNAL USE ONLY** — This tool is proprietary and intended solely for Ridikc's internal R&D team. Do not distribute outside the organization.

</div>

---

## ✨ Features

- 🎬 **YouTube Video** — download MP4 / MP3 files to disk via `yt-dlp`.
- 🖼️ **YouTube Thumbnails** — resolve every thumbnail size (`default` → `maxresdefault`) and download them in bulk.
- 📝 **YouTube Metadata** — extract title, description, and duration for videos & channels.
- 📦 **Channel Harvester** — download all thumbnails / metadata / videos from a channel into a single ZIP.
- 📃 **Playlist** — list the videos of a playlist, with optional CSV/JSON export.
- 🌐 **Local Web GUI** — a Flask app on `127.0.0.1` with light/dark theme and live progress.
- 🧩 **Anti-bot & rate-limit mitigation** — retries, sleep, User-Agent, proxy, and cookies support.
- ♻️ **Crash-safe resume** — checkpoint file lets an interrupted run continue where it stopped.

---

## 📦 Installation

### Untuk tim (tanpa menulis kode) — installer

| Platform | Perintah |
| --- | --- |
| Windows | `setup-gui.bat` |
| macOS / Linux | `./setup-gui.sh` |

Installer memeriksa Python, memasang dependency + `yt-dlp`, lalu membuat shortcut
`RCH-GUI` di Desktop. Buka shortcut tersebut untuk menjalankan GUI web.

### Manual (CLI)

```bash
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-

python -m pip install -r requirements.txt   # atau: pip install -e .
```

Perintah `rch` tersedia global bila diinstal dengan `pip install -e .`.
Tanpa instalasi, jalankan lewat `python -m rch <perintah>`.

### Sebagai library (untuk developer)

```bash
pip install -e .
```

---

## 🚀 Quick Start

```bash
rch thumbnail https://youtu.be/VIDEO_ID maxresdefault   # satu thumbnail
rch channel   https://www.youtube.com/@namachannel       # semua thumbnail → ZIP
rch list      https://www.youtube.com/@namachannel       # daftar ID video
rch info      https://youtu.be/VIDEO_ID                  # metadata video
rch video     https://youtu.be/VIDEO_ID --quality 1080p  # unduh video
rch video     https://youtu.be/VIDEO_ID --mp3            # unduh audio
rch download  https://youtu.be/VIDEO_ID                  # video + thumbnail
rch playlist  "https://www.youtube.com/playlist?list=ID" # isi playlist
rch web                                            # GUI web lokal
```

Tanpa instalasi global, tambahkan `python -m` di depan setiap perintah.

> Panduan lengkap tim: [`docs/PENGGUNAAN.md`](docs/PENGGUNAAN.md) · Cara kerja internal: [`docs/CARA_KERJA.md`](docs/CARA_KERJA.md)

---

## 📚 CLI Reference

Semua perintah menyediakan `--help` untuk opsi lengkap.

### Perintah utama

| Perintah | Kegunaan |
| --- | --- |
| `thumbnail URL [SIZE]` | Unduh satu thumbnail (`maxresdefault` bila `SIZE` kosong). |
| `channel URL [SIZE]` | Unduh semua thumbnail sebuah channel → satu ZIP. |
| `list URL` | Tampilkan daftar ID video dari channel. |
| `info URL...` | Ambil metadata (judul, deskripsi, durasi) dari ID video. |
| `video URL [NAME]` | Unduh satu video sebagai MP4 atau MP3. |
| `download URL [NAME]` | Unduh video dan thumbnail sekaligus. |
| `playlist URL` | Tampilkan daftar video dari sebuah playlist. |
| `channel-info URL [SIZE]` | Metadata channel saja (tanpa video) → satu ZIP. |
| `channel-video URL` | Video saja dari channel → satu ZIP. |
| `channel-full URL [SIZE]` | Video + thumbnail + metadata lengkap → satu ZIP. |
| `web` / `gui` / `serve` | Jalankan GUI web lokal di browser. |

### Opsi channel (berlaku untuk `channel-full`, `channel-video`, `channel-info`)

| Opsi | Default | Keterangan |
| --- | --- | --- |
| `--out` | `./downloads` | Direktori output. |
| `--limit` | — | Batas jumlah video. |
| `--quality` | `720p` | Kualitas video. |
| `--concurrency` | `2` | Jumlah worker paralel. |
| `--min-duration` / `--max-duration` | — | Filter durasi (detik). |
| `--after` | — | Filter tanggal upload (`YYYYMMDD`). |
| `--csv` / `--json` | — | Simpan metadata ke CSV / JSON. |
| `--sub-lang` | — | Bahasa subtitle. |
| `--shorts` | — | Sertakan Shorts. |
| `--subtitles` | — | Unduh subtitle. |
| `--resume` | — | Lanjutkan dari checkpoint. |
| `--cookies` | — | Browser untuk cookies (`chrome`, `firefox`, `edge`). |

### Library (Python)

```python
from rch.youtube.thumbnail import download_thumbnail, thumbnail_url
from rch.youtube.video import download
from rch.youtube.playlist import playlist_metadata

# Resolusi URL thumbnail (tanpa network bila ID diketahui)
print(thumbnail_url("dQw4w9WgXcQ", "maxresdefault"))

# Unduh satu thumbnail ke disk
print(download_thumbnail("https://youtu.be/dQw4w9WgXcQ", size="maxresdefault",
                         output_dir="./downloads"))

# Unduh satu video
print(download("https://youtu.be/dQw4w9WgXcQ",
               {"format": "mp4", "quality": "720p", "outputDir": "./downloads"}))

# Isi playlist
print(playlist_metadata("https://www.youtube.com/playlist?list=PLxxxx"))
```

Setiap fungsi mengembalikan amplop seragam:

```python
# sukses
{"status": True, "result": {...}}
# gagal
{"status": False, "message": "..."}
```

---

## ⚙️ Konfigurasi

Nilai diambil dari **environment variable**, lalu **`.rchrc.json`**, lalu default.

| Key | Environment | Default |
| --- | --- | --- |
| `sleep_requests` | `RCH_SLEEP_REQUESTS` | `0.5` |
| `sleep_interval` | `RCH_SLEEP_INTERVAL` | `0.5` |
| `max_sleep_interval` | `RCH_MAX_SLEEP_INTERVAL` | `2` |
| `retries` | `RCH_RETRIES` | `3` |
| `user_agent` | `RCH_USER_AGENT` | UA Chrome 128 |
| `proxy` | `RCH_PROXY` | — |
| `cookies` | `RCH_COOKIES` | — |
| `limit_rate` | `RCH_LIMIT_RATE` | — |
| `concurrency` | `RCH_CONCURRENCY` | `2` |
| `quality` | `RCH_QUALITY` | `720p` |

`.rchrc.json` dibaca dari direktori kerja, lalu `$HOME`.

```json
{
  "retries": 5,
  "concurrency": 3,
  "quality": "1080p"
}
```

---

## 🧪 Testing

```bash
python -m pytest                                          # semua test
python -m pytest --cov=rch --cov-report=term-missing       # + coverage
python -m pytest --cov=rch --cov-fail-under=100            # gerbang CI
```

1188 test, coverage 100%. Seluruh test berjalan tanpa network — HTTP, `yt-dlp`,
dan sistem file di-inject sebagai dependency agar deterministik.

---

## 📁 Struktur

```
rch/
├── cli.py          # perintah click
├── config.py       # env + .rchrc.json + default
├── core/           # checkpoint, events, export, http, report, zip_util
├── web/            # GUI Flask + static + template
└── youtube/        # channel, playlist, thumbnail, video, metadata
legacy-node/        # implementasi Node.js lama (read-only, referensi)
```

---

## ⚠️ Disclaimer

This project is intended for internal R&D and educational use. Video/audio
downloading relies on `yt-dlp`, whose availability can change at any time.
Respect each platform's Terms of Service. If you are the owner, operator, or
authorized representative of any service supported here and wish for removal,
please contact us.

> **Note:** Video/audio download requires `yt-dlp` (installed automatically by the
> setup scripts). Thumbnail download is fully self-contained — it reads directly
> from YouTube's CDN and needs no external tool.

---

## 📄 License

MIT — (c) 2026 Ridikc