# Cara Kerja Pustaka RCH

Dokumen ini menjelaskan arsitektur, alur kerja, dan cara pengujian pustaka
**RCH** (`rch/`) — lapisan akuisisi metadata, thumbnail, konfigurasi, dan
riwayat.

> Untuk generator clip viral, lihat [`CLIPPER.md`](CLIPPER.md).

> Riwayat proyek ditulis ulang penuh ke Python pada Juli 2026, jadi tidak ada
> sisa kode Node.js di repo ini.

---

## 1. Ringkasan

Ridikc Content Harvester adalah **alat Python 3.10+** untuk mengunduh media dan
thumbnail dari YouTube. Modular per fitur (`rch/youtube/`), dengan lapisan
inti yang tidak bergantung pada jaringan sehingga seluruh test bisa
berjalan offline.

### Teknologi

| Komponen | Keterangan |
| --- | --- |
| Runtime | Python ≥ 3.10 |
| CLI | `click` |
| HTTP client | `requests` (`rch/core/http.py`) |
| HTML parsing | `beautifulsoup4` (playlist, fallback metadata) |
| Backend video/audio | `yt-dlp` (CLI lokal, direkomendasikan) |
| GUI | `flask` (server lokal, dibuka di browser) |
| ZIP | `zipfile` (standar library) |
| Test | `pytest` + `pytest-cov` |

---

## 1.1 Sumber Data & Peran yt-dlp

Sistem ini **TIDAK memakai API resmi YouTube (Data API v3)**. Sebagian besar
data diambil lewat **`yt-dlp`** (CLI eksternal) yang membaca struktur halaman
video YouTube secara langsung.

### Mengapa yt-dlp, bukan API resmi?

| Perbandingan | API resmi (Data API v3) | yt-dlp |
| --- | --- | --- |
| Butuh API key | Ya | Tidak |
| Ada quota harian | Ya (10.000 unit/hari) | Tidak |
| Biaya | Berbayar untuk skala besar | Gratis |
| Ambil deskripsi massal | Makan banyak quota | Gratis, tanpa batas |
| Download file video | Tidak menyediakan | Bisa |

### Sumber data per fitur

| Data | Sumber | Lewat yt-dlp? | Butuh API key? |
| --- | --- | --- | --- |
| **Judul & durasi** | `yt-dlp --dump-single-json` | Ya | Tidak |
| **Deskripsi** | fallback: scrape halaman video | Sebagian | Tidak |
| **Daftar video channel** | `--flat-playlist` | Ya | Tidak |
| **Link video** | konstruksi `youtu.be/<ID>` | Tidak | Tidak |
| **Thumbnail** | CDN `i.ytimg.com/vi/<ID>/<size>.jpg` | Tidak | Tidak |
| **File video/audio** | resolve format + download | Ya | Tidak |

### Alur teknis pengambilan metadata

File: `rch/youtube/metadata.py` → `get_video_info_batch()`

```
get_video_info_batch(ids, run_ytdlp=...)
   └─ yt-dlp --dump-single-json --no-playlist <url...>
        └─ yt-dlp membuka halaman tiap video YouTube
        └─ field diambil per video, dipisah delimiter
        └─ parse_batch_output() → [{id, title, duration}, ...]
```

Karena data dikembalikan per-batch, request dipecah menjadi potongan
(`_chunked`) agar output yt-dlp tidak membengkak. Bila yt-dlp gagal,
`get_video_info_fallback()` memproses halaman HTML secara langsung
(oEmbed → `<title>`).

### Titik rawan utama

Karena yt-dlp membaca struktur halaman YouTube (bukan API resmi yang stabil),
yt-dlp **bisa berhenti bekerja jika YouTube mengubah struktur halamannya**.
Solusinya: perbarui yt-dlp secara berkala.

```bash
python -m pip install --upgrade yt-dlp
```

### Auto-update yt-dlp

RCH menangani ini otomatis di dua lapis:

1. **Saat runtime** — fitur yang butuh yt-dlp memanggil `ensure_ytdlp_updated()`
   (`rch/youtube/metadata.py`) yang menjalankan `yt-dlp -U` sekali per proses.
   Kegagalan update ditoleransi (di-`try/except`) agar download tetap berjalan.
2. **Saat install** — `setup-gui.bat` (Windows) dan `setup-gui.sh` (macOS/Linux)
   memasang `yt-dlp` terbaru lewat `pip install --upgrade yt-dlp`, lalu memverifikasi
   dengan `python -m yt_dlp --version`.

---

## 1.2 Mitigasi Anti-Bot & Rate-Limit

Untuk mengurangi risiko blokir IP / HTTP 429 / terdeteksi bot, RCH menyisipkan
flag mitigasi otomatis ke setiap panggilan yt-dlp (lewat `rch/config.py` →
`build_ytdlp_args()`).

### Flag yang otomatis dipakai

| Flag yt-dlp | Nilai default | Tujuan |
| --- | --- | --- |
| `--retries` | 3 | Ulangi otomatis saat error sesaat |
| `--sleep-requests` | 0.5 | Jeda antar request (detik) |
| `--sleep-interval` | 0.5 | Jeda antar download |
| `--max-sleep-interval` | 2 | Batas atas jeda acak |
| `--user-agent` | UA Chrome 128 | Samarkan sebagai browser asli |

Flag opsional (hanya aktif bila diisi): `--proxy`, `--limit-rate`,
`--cookies-from-browser`.

Selain itu, pemanggilan HTTP di `rch/core/http.py` (`http_get` / `http_post`)
punya **retry dengan exponential backoff** saat kena 429/5xx.

### Konfigurasi lewat environment variable

Semua nilai bisa di-override tanpa ubah kode. Urutan resolusi:
**environment variable → `.rchrc.json` → default**.

| Env var | Key `.rchrc.json` | Default | Keterangan |
| --- | --- | --- | --- |
| `RCH_SLEEP_REQUESTS` | `sleep_requests` | 0.5 | Jeda antar request (detik) |
| `RCH_SLEEP_INTERVAL` | `sleep_interval` | 0.5 | Jeda antar download |
| `RCH_MAX_SLEEP_INTERVAL` | `max_sleep_interval` | 2 | Batas atas jeda acak |
| `RCH_RETRIES` | `retries` | 3 | Jumlah percobaan ulang |
| `RCH_USER_AGENT` | `user_agent` | UA Chrome | User-Agent kustom |
| `RCH_PROXY` | `proxy` | (kosong) | Proxy (mis. `http://127.0.0.1:8080`) |
| `RCH_COOKIES` | `cookies` | (kosong) | Browser sumber cookie |
| `RCH_LIMIT_RATE` | `limit_rate` | (kosong) | Batas kecepatan (mis. `2M`) |
| `RCH_CONCURRENCY` | `concurrency` | 2 | Jumlah worker paralel |
| `RCH_QUALITY` | `quality` | 720p | Kualitas video default |

`.rchrc.json` dibaca dari direktori kerja, lalu `$HOME`.

### Cookies browser (opsional)

Flag CLI `--cookies <browser>` (atau env `RCH_COOKIES`) memakai cookie browser untuk:
- Mengurangi deteksi bot.
- Mengakses video **unlisted/private** yang tidak bisa diambil tanpa login.

```bash
rch channel-info https://www.youtube.com/@namachannel --cookies chrome
```

> Catatan: `--cookies-from-browser` butuh browser yang sudah login YouTube di mesin yang sama.

---

## 2. Struktur Direktori

```
#6downloader/
├── setup.py                  # packaging, entry point rch=rch.cli:main
├── requirements.txt
├── rch/
│   ├── cli.py                # perintah click
│   ├── config.py             # env + .rchrc.json + default
│   ├── core/
│   │   ├── checkpoint.py     # state resume (JSON, atomic write)
│   │   ├── events.py         # event emitter (progress)
│   │   ├── export.py         # ekspor CSV/JSON
│   │   ├── http.py           # requests + retry backoff
│   │   ├── report.py         # report.json + history.jsonl
│   │   └── zip_util.py       # helper ZIP (zipfile)
│   ├── web/
│   │   ├── server.py         # GUI Flask
│   │   ├── static/           # app.js, style.css
│   │   └── templates/        # index.html
│   └── youtube/
│       ├── common.py         # extract_video_id, slugify
│       ├── channel.py        # channel-full / -video / -info
│       ├── metadata.py       # judul, durasi, deskripsi
│       ├── playlist.py       # parse playlist YouTube
│       ├── thumbnail.py      # resolve + download thumbnail
│       └── video.py          # unduh MP4/MP3
└── tests/                    # 1248 test, mirror struktur rch/
```

---

## 3. Alur Kerja

```
CLI (rch <perintah>)  atau  GUI web (rch web)        │
        ▼
rch/youtube/  (dispatcher per fitur)
        │
        ├─ thumbnail.py ──► extract_video_id (regex) ──► i.ytimg.com/vi/<ID>/<size>.jpg
        │                             │                              │
        │                             ▼                              ▼
        │                    judul via oEmbed            download / simpan / zip
        │
        ├─ video.py ──► extract_video_id ──► yt-dlp (lokal) ──► file MP4/MP3
        │                    │                    │
        │                    │                    └─ retry + backoff (403/429)
        │                    ▼
        │            judul via oEmbed, fallback ke video_id
        │
        ├─ metadata.py ──► yt-dlp --dump-single-json ──► judul/durasi
        │                       │ fallback: scrape HTML (oEmbed → <title>)
        │
        ├─ playlist.py ──► fetch HTML playlist ──► parse ytInitialData ──► daftar video
        │
        └─ channel.py ──► list_ids ──► metadata batch ──► worker paralel
                                 │
                                 └─ checkpoint (resume) + report.json
```

### Pola return (konsisten di semua fungsi)

```python
# sukses
{"status": True, "result": {...}}
# gagal
{"status": False, "message": "..."}
```

### Dependency injection (kunci test offline)

Setiap fungsi I/O menerima dependency sebagai parameter keyword-only, sehingga
test bisa menyuntikkan objek pengganti tanpa menyentuh jaringan:

| Fungsi | Parameter injeksi |
| --- | --- |
| `video.download` | `run_ytdlp`, `fetch_title`, `ensure_updated`, `sleep` |
| `video.download_once` | `run_ytdlp`, `fetch_title` |
| `metadata.get_video_info_batch` | `run_ytdlp` |
| `channel.channel_video` | `list_ids`, `metadata`, `download_video`, `sleep` |
| `thumbnail.download_thumbnail` | `http_get`, `fetch_title` |

---

## 4. Detail Tiap Modul

### 4.1 `rch/youtube/thumbnail.py`

Fokus utama produk. **Tanpa API key, tanpa layanan pihak ketiga.**

- `extract_video_id(url)` — ekstrak ID 11 karakter via regex (`rch/youtube/common.py`).
  Mendukung `youtube.com/watch?v=`, `youtu.be/`, `shorts/`, `embed/`, `live/`.
- `thumbnail_url(video_id, size)` — susun URL thumbnail dari CDN YouTube.
- Judul diambil dari oEmbed, dengan fallback ke video ID.

Ukuran yang didukung:
`default`, `mqdefault`, `hqdefault`, `sddefault`, `maxresdefault`.

Fungsi publik:

| Fungsi | Deskripsi |
| --- | --- |
| `thumbnail(url)` | Resolve semua ukuran thumbnail |
| `download_thumbnail(url, ...)` | Download satu thumbnail ke disk |
| `download_thumbnails(urls, ...)` | Download massal (konkuren) + opsional ZIP |
| `thumbnail_url(video_id, size)` | Susun URL CDN |

### 4.2 `rch/youtube/video.py`

Unduh satu video sebagai MP4 atau MP3.

1. Ekstrak video ID (gagal → `ValueError` untuk URL tidak valid).
2. Ambil judul via oEmbed, fallback ke video ID; `slugify()` jadi nama file aman.
3. Susun argumen yt-dlp (`build_args()`) — `-f <format_filter>`, plus subtitle bila diminta.
4. Jalankan yt-dlp (`default_run_ytdlp()`), dengan flag mitigasi dari `config.py`.
5. Verifikasi file hasil ada; retry/backoff bila error bersifat transient
   (`is_retryable_error()` mengenali 403 / 429 / timeout).

### 4.3 `rch/youtube/metadata.py`

1. `get_video_info_batch()` — judul + durasi banyak video sekaligus (dipecah per batch).
2. `get_video_info()` — satu video.
3. `get_video_info_fallback()` — scrape HTML: oEmbed → tag `<title>`.
4. `get_channel_ids()` — daftar ID video dari channel (`--flat-playlist`).
5. `ensure_ytdlp_updated()` — `yt-dlp -U` sekali per proses, kegagalan ditoleransi.

### 4.4 `rch/youtube/playlist.py`

1. Ekstrak `list=` dari URL (`extract_playlist_id()`).
2. Fetch HTML playlist.
3. Cari `var ytInitialData` (JSON tersembunyi di `<script>`).
4. Parse rekursif (`parse_playlist_data()`) untuk judul, author, thumbnail, dan item video.
5. `to_metadata()` menormalkan item menjadi record ekspor; `parse_length_text()`
   mengubah `lengthText` (mis. `4:05`) menjadi detik.

### 4.5 `rch/youtube/channel.py`

Tiga perintah channel berbagi satu pipeline:

| Fungsi | Isi ZIP |
| --- | --- |
| `channel_full()` | video + thumbnail + metadata |
| `channel_info()` | thumbnail + metadata (tanpa video) |
| `channel_video()` | video saja |

- Filter: `--limit`, `--min-duration`, `--max-duration`, `--after`, `--shorts`.
- `folder_name_for()` + `count_slug_collisions()` mencegah tabrakan nama folder
  antar video dengan slug identik (video ID ditambahkan sebagai akhiran).
- Checkpoint (`rch/core/checkpoint.py`) ditulis secara atomik sehingga `--resume`
  tetap aman meskipun proses mati mendadak.
- Event emitter (`rch/core/events.py`) menyiarkan progres ke CLI maupun GUI.

### 4.6 `rch/core/`

| Modul | Fungsi |
| --- | --- |
| `http.py` | `http_get`/`http_post` + retry exponential backoff untuk 429/5xx |
| `checkpoint.py` | state resume, atomic write, idempotent |
| `events.py` | emitter `progress` / `phase` / `video:done` |
| `export.py` | ekspor CSV/JSON, proteksi formula injection pada CSV |
| `report.py` | `report.json` per run + `history.jsonl` (append) |
| `zip_util.py` | pembungkus `zipfile` untuk arsip bulk |

---

## 5. Cara Pengujian

### Menjalankan test

```bash
# semua test (1248 test, tanpa network)
python -m pytest

# + coverage
python -m pytest --cov=rch --cov-report=term-missing

# gerbang CI (wajib 100%)
python -m pytest --cov=rch --cov-fail-under=100

# hanya satu modul
python -m pytest tests/youtube/test_video.py
```

### Struktur test

Test mencerminkan struktur `rch/`:

```
tests/
├── test_cli.py                     # helper CLI
├── test_cli_commands.py            # permukaan perintah
├── test_config.py                  # resolusi konfigurasi
├── test_defaults_and_entrypoints.py# default & entry point
├── core/                           # test_http, test_checkpoint, test_export, ...
├── web/                            # test_server, test_api_playlist, test_csrf_guard
└── youtube/                        # test_video, test_channel, test_playlist, ...
```

### Prinsip pengujian

- **Tanpa network.** Semua HTTP, `yt-dlp`, dan sleeper di-inject sebagai fake.
- **Tidak ada shared state.** Setiap test menyiapkan fixture-nya sendiri di `tmp_path`.
- **CLI diuji lewat `click.testing.CliRunner`**, bukan subprocess, kecuali test
  entry point yang memang menjalankan `python -m rch`.
- **Naming.** Test menjelaskan perilaku, bukan implementasi
  (mis. `test_writes_header_and_timestamp`, bukan `test_report_line_2`).

### Verifikasi manual (butuh network)

```bash
# Resolve thumbnail
python -m rch thumbnail https://youtu.be/dQw4w9WgXcQ hqdefault

# Download satu video
python -m rch video https://youtu.be/dQw4w9WgXcQ --quality 720p

# Audio
python -m rch video https://youtu.be/dQw4w9WgXcQ --mp3

# Channel → ZIP
python -m rch channel-full https://www.youtube.com/@namachannel --limit 5

# GUI
rch web                  # atau: python -m clipper.app
```

`rch web` menjalankan app yang sama dengan `python -m clipper.app`: satu proses
melayani halaman clipper (`/`) dan halaman downloader (`/download`).

---

## 6. Contoh Pemakaian Library

```python
from rch.youtube.thumbnail import download_thumbnails, thumbnail
from rch.youtube.video import download

# Resolve semua ukuran thumbnail
print(thumbnail("https://youtu.be/dQw4w9WgXcQ")["result"])

# Download massal + ZIP
batch = download_thumbnails(
    ["https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/9bZkp7q19f0"],
    size="hqdefault", output_dir="./downloads", concurrency=5, zip=True,
)
print(batch["result"]["zip"])  # { path, sizeBytes, fileCount }

# Unduh video
print(download("https://youtu.be/dQw4w9WgXcQ",
               {"format": "mp4", "quality": "720p", "outputDir": "./downloads"}))
```

---

## 7. Catatan / Keterbatasan

- **Thumbnail**: stabil, cepat, tanpa dependensi eksternal. Direkomendasikan untuk kebutuhan produksi.
- **Video/audio**: butuh `yt-dlp` terpasang (cek: `yt-dlp --version`). Installer
  sudah memasangnya secara otomatis.
- **Kecepatan**: `concurrency` default 2 cukup konservatif. Menaikkan terlalu tinggi
  memicu blokir rate-limit YouTube.
- **Disclaimer**: mengunduh stream video/audio dapat melanggar ToS platform terkait.
  Gunakan untuk keperluan R&D/internal yang diizinkan.