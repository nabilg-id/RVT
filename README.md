<div align="center">

# Ridikc Video Toolkit

**Generator clip viral YouTube dengan GUI web lokal, plus pustaka akuisisi RCH.**

_Tempel satu URL, dapatkan clip pendek siap upload — dibangun untuk penggunaan internal R&D Ridikc._

[![python version](https://img.shields.io/badge/python-3.10%2B-61afef.svg?style=flat-square)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

> 🔒 **INTERNAL USE ONLY** — proprietary, hanya untuk tim R&D internal Ridikc.

</div>

---

## ✨ Features

- 🎬 **Generator Clip Viral** — pilih momen terbaik otomatis lewat AI (OpenRouter), transkripsi Whisper, face tracking, caption kata-per-kata, dan transisi hook → main.
- 🌐 **GUI Web Lokal** — buka `127.0.0.1:8787` di browser, tidak perlu terminal. Preview metadata + thumbnail sebelum generate, progres live, riwayat job.
- 🖼️ **Akuisisi dari RCH** — metadata (judul, deskripsi, durasi, tanggal) dan thumbnail diambil sebelum clip dibuat.
- 🎨 **7 gaya caption** — Clean White, Viral Yellow/Red/Green, Neon Cyan/Pink, Bold Black BG.
- 📋 **Riwayat** — setiap job clip tercatat di `temp/clip-history.log`.

---

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python -m clipper.app        # GUI web di http://127.0.0.1:8787
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
(torch, moviepy, Whisper, mediapipe), lalu membuat shortcut desktop.

> ⚠️ **Instalasi penuh itu besar.** `torch` + `faster-whisper` + model Whisper
> menambah sekitar 3 GB dan bisa memakan 15–30 menit.

### Manual

```bash
python -m pip install -r requirements.txt
```

### Syarat penting

- **Python 3.10+**
- **moviepy 1.x** — `moviepy.editor` dihapus di moviepy 2.x, dan pipeline clip
  masih memakainya. Jangan naikkan batas `<2.0` tanpa memindahkan API.
- **`OPENROUTER_API_KEY`** wajib di `.env`; tanpa itu `AISelector` gagal saat
  pemilihan momen.

---

## ⚙️ Konfigurasi

Salin `clipper/.env.example` ke `.env` di root repo.

| Key | Default | Keterangan |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | **Wajib** untuk pemilihan momen AI |
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