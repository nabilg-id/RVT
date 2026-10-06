<div align="center">

# Ridikc Video Toolkit

**Generator clip viral YouTube dengan GUI web lokal, plus pengunduh channel YouTube.**

_Tempel satu URL, dapatkan clip pendek siap upload — dibangun untuk penggunaan internal R&D Ridikc._

[![python version](https://img.shields.io/badge/python-3.10%2B-61afef.svg?style=flat-square)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

> 🔒 **INTERNAL USE ONLY** — proprietary, hanya untuk tim R&D internal Ridikc.

</div>

---

## ✨ Features

- 🎬 **Generator Clip Viral** — pilih momen terbaik otomatis lewat AI (OpenRouter), transkripsi Whisper, face tracking, caption kata-per-kata, dan transisi hook → main.
- 🌐 **GUI Web Lokal** — buka `127.0.0.1:8787` di browser, tidak perlu terminal. Dua halaman dalam satu proses: **Clipper** (`/`) dan **Downloader** (`/download`), berbagi satu registry job. Preview metadata + thumbnail sebelum generate, progres live, riwayat job.
- 🖼️ **Akuisisi dari RCH** — metadata (judul, deskripsi, durasi, tanggal) dan thumbnail diambil sebelum clip dibuat.
- 🎨 **7 gaya caption** — Clean White, Viral Yellow/Red/Green, Neon Cyan/Pink, Bold Black BG.
- 📋 **Riwayat** — setiap job clip tercatat di `temp/clip-history.jsonl`.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python -m clipper.app        # GUI web di http://127.0.0.1:8787
rch web                      # sama persis, lewat CLI
```

Tanpa `pip install` penuh, gunakan `requirements-dev.txt` (mode ringan: GUI
jalan, pipeline clip tidak).

### CLI interaktif

```bash
python -m clipper.main "https://www.youtube.com/watch?v=VIDEO_ID"
```

---

## 📦 Installation

### Untuk tim (installer)

| Platform | Perintah |
| --- | --- |
| Windows | `setup-gui.bat` |
| macOS / Linux | `./setup-gui.sh` |

Installer memasang Python bila perlu, dependency dasar, dependensi clip
(torch, moviepy, Whisper, mediapipe), lalu memasang project itu sendiri
(`pip install -e .`) supaya perintah `rch` / `vclip` tersedia**, terakhir
membuat shortcut desktop.

> ⚠️ **Instalasi penuh itu besar.** `torch` + `faster-whisper` + model Whisper
> menambah sekitar 3 GB dan bisa memakan 15–30 menit.

### Manual

```bash
python -m pip install -r requirements.txt
python -m pip install -e .
```

> **Kedua perintah wajib.** Yang pertama memasang dependency (flask, yt-dlp,
> moviepy, torch…), yang kedua memasang **project-nya sendiri** sehingga
> perintah `rch` dan `vclip` benar-benar ada. Tanpa `pip install -e .`, semua
> dependency sudah terpasang tetapi `rch` **tidak dikenal** — karena kedua
> perintah itu datang dari `[project.scripts]` di `pyproject.toml`, bukan dari
> `requirements.txt`.

Setelah ter-install, `rch` dan `vclip` bisa dipanggil **dari folder mana saja**:

```bash
rch --help          # CLI akuisisi
vclip               # GUI
```

### Tanpa install sama sekali

Bisa juga jalan langsung dari folder repo, selama kamu berada di dalamnya:

```bash
python -m rch --help
python -m clipper.app
```

Perbedaannya: `rch` (tanpa `python -m`) tidak akan ada, dan shortcut
`RCH-CLI.bat` di Desktop tidak bisa dipakai.

### Syarat penting

- **Python 3.10+**
- **moviepy 1.x** — `moviepy.editor` dihapus di moviepy 2.x, dan pipeline clip
  masih memakainya. Jangan naikkan batas `<2.0` tanpa memindahkan API.
- **`OPENROUTER_API_KEY`** wajib di `.env`; tanpa itu `AISelector` gagal saat
  pemilihan momen.

---

## ⚙️ Konfigurasi

Opsional. Salin `clipper/.env.example` ke `.env` — bisa di root repo **atau**
di dalam folder `clipper/`, keduanya dibaca.

Tanpa `.env`, aplikasi tetap jalan: pipeline clip hanya akan memakai klip acak
dan **tidak** memanggil AI (OpenRouter), sedangkan semua fitur unduh YouTube
(`rch …`) tetap berfungsi penuh tanpa file ini.

| Key | Default | Keterangan |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Untuk **pemilihan momen AI** di pipeline clip. Kosong = pakai klip acak |
| `OPENROUTER_MODEL` | `openrouter/free` | Router ke model gratis; ganti bila perlu |
| `WHISPER_MODEL` | `medium` | tiny / base / small / medium / large-v2 |
| `WHISPER_LANGUAGE` | auto | `id` / `en`, kosongkan untuk deteksi otomatis |
| `OUTPUT_DIR` | `./clips` | Folder hasil clip |
| `TEMP_DIR` | `./temp` | Folder kerja + riwayat |
| `COOKIES_FILE` | `./cookies.txt` | Cookie YouTube bila perlu |
| `YOUTUBE_COOKIES_BROWSER` | — | `chrome` / `firefox` / `edge` |
| `YOUTUBE_USER_AGENT` | — | UA kustom bila YouTube memblokir |
| `RCH_HOST` / `RCH_PORT` | `127.0.0.1` / `8787` | Alamat GUI web |

> `GEMINI_API_KEY` di `.env.example` adalah warisan dan tidak dipakai kode
> mana pun. Yang aktif adalah `OPENROUTER_API_KEY`.

---

## 🖥️ Penggunaan

Dua cara pakai: **GUI web** (paling gampang) atau **CLI** (`rch`). Semua
perintah punya bantuan detail lewat `--help`.

### GUI web

```bash
rch web                 # atau python -m clipper.app
rch web --port 9000     # ganti port (default 127.0.0.1:8787)
```

Browser terbuka di `http://127.0.0.1:8787` dengan dua halaman dalam satu
proses: **Clipper** (`/`) dan **Downloader** (`/download`). Tema terang/gelap,
progres live, dan riwayat job. Bisa juga lewat shortcut **RCH-GUI** /
**VCLIP-GUI** di Desktop.

### CLI interaktif (clip)

```bash
python -m clipper.main "https://www.youtube.com/watch?v=VIDEO_ID"
```

CLI menanyakan jumlah clip, durasi min/maks, dan gaya caption.

### Perintah akuisisi (`rch`)

| Perintah | Fungsi |
| --- | --- |
| `rch thumbnail URL [SIZE] [--zip]` | Unduh thumbnail satu video |
| `rch video URL [--quality 1080p] [--mp3]` | Unduh satu video / audio |
| `rch download URL [NAME] [OUT] [--zip]` | Unduh video + thumbnail sekaligus |
| `rch channel URL [SIZE]` | Unduh semua video dari channel |
| `rch channel-info URL [SIZE]` | Info channel tanpa mengunduh video |
| `rch channel-full URL [SIZE]` | Video + thumbnail + metadata → 1 ZIP |
| `rch channel-video URL` | Unduh video saja dari channel |
| `rch list URL [--limit N]` | Tampilkan daftar ID video channel |
| `rch info ID [ID ...] [--json] [--csv]` | Info video, ekspor JSON/CSV |
| `rch playlist URL [--limit N] [--csv]` | Info playlist |
| `rch web` / `gui` / `serve` | Jalankan GUI web lokal |

Ukuran thumbnail (`SIZE`): `default`, `mqdefault`, `hqdefault`, `sddefault`,
`maxresdefault`. Kualitas video (`--quality`): `360p`, `480p`, `720p`
(default), `1080p`.

**Contoh cepat:**

```bash
rch thumbnail https://youtu.be/ID_VIDEO maxresdefault
rch video https://youtu.be/ID_VIDEO --quality 1080p
rch video https://youtu.be/ID_VIDEO --mp3                # audio saja
rch download https://youtu.be/ID_VIDEO --zip
rch channel https://www.youtube.com/@namachannel
rch channel-full https://www.youtube.com/@namachannel hqdefault
rch channel-full https://www.youtube.com/@namachannel --resume   # lanjutkan
```

Flag lanjutan untuk perintah channel: `--resume` (lanjut dari checkpoint
`.rch-checkpoint.json`), `--out <folder>`, `--quality <res>`, `--limit <n>`,
`--after YYYYMMDD`, `--shorts`, `--subtitles`, `--concurrency <n>`.

> Output default masuk ke folder **Downloads** sistem di
> `Downloads/Ridikc Video Toolkit/` (Windows, macOS, dan Linux alike). Ubah
> dengan `--out`, atau variabel `RCH_DOWNLOADS_DIR`.

> 📚 Detail lengkap (install per platform, opsi lanjutan, arsitektur) ada di
> [`docs/PENGGUNAAN.md`](docs/PENGGUNAAN.md), [`docs/CLIPPER.md`](docs/CLIPPER.md),
> dan [`docs/CARA_KERJA.md`](docs/CARA_KERJA.md).

---

## 🧪 Testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                                        # semua test
python -m pytest --cov --cov-report=term-missing        # + coverage
python -m pytest --cov --cov-fail-under=97              # gerbang CI
```

1369 test, coverage 99%. Test pipeline clip otomatis **di-skip** bila
dependensi berat belum terpasang (`pytest.importorskip`), jadi suite inti tetap
hijau di mesin ringan maupun di CI.

---

## 📁 Struktur

```
rch/                       # pustaka akuisisi (metadata, thumbnail, config, riwayat)
clipper/
├── app.py                 # Flask: job clip, preview, riwayat
├── progress.py            # tangkap stdout pipeline jadi progres
├── main.py                # CLI interaktif
├── config.py              # konfigurasi & path
├── services/              # pipeline: downloader, whisper, ai_selector, caption, face
├── styles/                # 7 gaya caption
├── templates/ static/     # frontend GUI
└── tests/                 # test pipeline (skip bila deps berat)
tests/                     # test RCH + test lapisan web clipper
```

---

## 🛡️ Security

- Guard **cross-origin** pada semua endpoint tulis — GUI tidak punya
  autentikasi, jadi `Origin` harus cocok dengan `Host`.
- Validasi URL membatasi skema `http`/`https` dan domain YouTube.
- Nama file thumbnail dikunci di dalam folder output (anti path traversal).
- Ekspor CSV dari RCH memproteksi formula spreadsheet.

---

## ⚠️ Disclaimer

Untuk penggunaan internal dan edukasi. Unduhan video bergantung pada
`yt-dlp` yang ketersediaannya bisa berubah. Hormati Terms of Service
masing-masing platform. Untuk permintaan penghapusan konten, hubungi pemilik
platform.

---

## 📄 License

MIT — (c) 2026 Ridikc