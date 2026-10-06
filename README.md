<div align="center">

# Ridikc Video Toolkit

**Generator clip viral YouTube dengan GUI web lokal, plus pengunduh channel YouTube.**

_Tempel satu URL, dapatkan clip pendek siap upload — dibangun untuk penggunaan internal R&D Ridikc._

[![python version](https://img.shields.io/badge/python-3.10%2B-61afef.svg?style=flat-square)](https://www.python.org)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

> 🔒 **INTERNAL USE ONLY** — proprietary, hanya untuk tim R&D internal Ridikc.

</div>

---

## ✨ Apa yang Bisa Dilakukan

- 🎬 **Buat clip pendek dari video YouTube** — tempel URL, klik Generate, jadi
  clip siap upload. AI memilih momen terbaiknya sendiri.
- 📥 **Unduh video & channel YouTube** — satu video, satu channel, atau
  thumbnail + deskripsi + metadata sekaligus.
- 🌐 **Berjalan di browser** — tidak perlu terminal. Dua halaman: **Clipper**
  (buat clip) dan **Downloader** (unduh video).
- 🎨 **7 gaya caption** — Clean White, Viral Yellow/Red/Green, Neon Cyan/Pink,
  Bold Black BG.
- 📋 **Riwayat** — semua pekerjaan yang pernah kamu jalankan tersimpan.

<details>
<summary>Detail teknis fitur (untuk developer)</summary>

- 🎬 **Generator Clip Viral** — pemilihan momen AI (OpenRouter), transkripsi
  Whisper, face tracking, caption kata-per-kata, transisi hook → main.
- 🌐 **GUI Web Lokal** — satu proses Flask di `127.0.0.1:8787` dengan dua
  halaman (`/` dan `/download`) yang berbagi satu registry job. Preview
  metadata + thumbnail sebelum generate, progres live, riwayat job.
- 🖼️ **Akuisisi dari RCH** — metadata (judul, deskripsi, durasi, tanggal) dan
  thumbnail diambil sebelum clip dibuat; ledger bersama `video-tracker.jsonl`
  menyatukan status unduhan & clip.
- 📋 **Riwayat clip** — tiap job tercatat di `temp/clip-history.jsonl`.

</details>

---

## 🚀 Mulai dari sini (pengguna baru)

Kamu **tidak perlu** tahu apa itu Python, pip, atau terminal. Cukup 4 langkah.

### 1. Pastikan Python ada di komputer

Buka **Command Prompt** (tekan `Win` → ketik `cmd` → Enter), lalu ketik:

```
py -3 --version
```

Kalau muncul `Python 3.10` atau lebih baru → **lanjut ke langkah 2**.
Kalau muncul error → pasang dulu dari <https://www.python.org/downloads/>
(pilih versi 3.12), **centang "Add Python to PATH"** saat memasang, lalu
*buka ulang* Command Prompt dan coba lagi.

### 2. Jalankan installer

Di folder proyek ini, **klik dua kali** `setup-gui.bat`.

 Akan muncul jendela hitam yang bekerja sendiri. Tunggu sampai selesai.

> ⏳ **Ini lama.** Installer mengunduh library besar (torch, Whisper) sekitar
> **3 GB**, bisa **15–30 menit**. Biarkan saja — jangan tutup jendelanya.
>
> 💡 **Mau coba cepat dulu?** Tekan `Ctrl+C` saat installer sedang mengunduh
> library besar. Sisa langkah tetap berjalan dan kamu tetap bisa memakai fitur
> **unduh YouTube** (Clipper AI-nya yang tidak aktif).

### 3. Buka aplikasinya

Di **Desktop** kamu sekarang ada **VCLIP-GUI**. Klik dua kali.

Browser akan terbuka sendiri ke `http://127.0.0.1:8787`.

### 4. Pakai

- **Halaman `/`** → tempel URL YouTube, klik **Generate** → clip jadi.
- **Halaman `/download`** → tempel URL, klik **Unduh** → video tersimpan.

Selesai. 🎉

> **Kalau halaman tidak terbuka**, klik kanan `VCLIP-GUI` → **Run as
> administrator**. Pesan errornya akan muncul di jendela itu — foto dan kirim
> ke tim R&D.

---

## 🖥️ Penggunaan CLI (opsional, untuk yang terbiasa)

Kalau lebih suka mengetik perintah daripada klik. Aktif **Command Prompt** dulu
tekan `Win` → ketik `cmd` → Enter.

```bash
rch --help              # lihat semua perintah
rch video URL --mp3     # unduh audio saja
rch channel-full URL    # unduh semua video satu channel
rch web                 # jalankan GUI
```

Semua perintah lengkap:

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

Contoh:

```bash
rch thumbnail https://youtu.be/ID_VIDEO maxresdefault
rch video https://youtu.be/ID_VIDEO --quality 1080p
rch video https://youtu.be/ID_VIDEO --mp3                # audio saja
rch download https://youtu.be/ID_VIDEO --zip
rch channel https://www.youtube.com/@namachannel
rch channel-full https://www.youtube.com/@namachannel --resume   # lanjutkan
```

Ukuran thumbnail (`SIZE`): `default`, `mqdefault`, `hqdefault`, `sddefault`,
`maxresdefault`. Kualitas video (`--quality`): `360p`, `480p`, `720p`
(default), `1080p`.

Semua hasil unduhan tersimpan otomatis ke folder **Downloads** sistem →
`Downloads/Ridikc Video Toolkit/`. Ubah dengan `--out <folder>` atau variabel
`RCH_DOWNLOADS_DIR`.

---

## 👨‍💻 Detail teknis (untuk developer)

### ⚙️ Installation

<details>
<summary>Instalasi manual (Linux/macOS/Windows)</summary>

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

</details>

<details>
<summary>Jalankan tanpa install</summary>

Bisa jalan langsung dari folder repo, selama kamu berada di dalamnya:

```bash
python -m rch --help
python -m clipper.app
```

`rch` (tanpa `python -m`) tidak akan ada di mode ini.

</details>

### Konfigurasi (`.env`)

Opsional. Salin `clipper/.env.example` ke `.env` — di root repo **atau** di
`clipper/`, keduanya dibaca.

Tanpa `.env`, aplikasi tetap jalan: pipeline clip hanya pakai klip acak dan
**tidak** memanggil AI (OpenRouter). Fitur unduh YouTube tetap berfungsi penuh.

| Key | Default | Keterangan |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | Untuk **pemilihan momen AI**. Kosong = klip acak |
| `OPENROUTER_MODEL` | `openrouter/free` | Router ke model gratis |
| `WHISPER_MODEL` | `medium` | tiny / base / small / medium / large-v2 |
| `WHISPER_LANGUAGE` | auto | `id` / `en`, kosongkan untuk auto |
| `OUTPUT_DIR` | `Downloads/Ridikc Video Toolkit/clips` | Folder hasil clip |
| `TEMP_DIR` | `./temp` | Folder kerja + riwayat |
| `COOKIES_FILE` | `./cookies.txt` | Cookie YouTube bila perlu |
| `YOUTUBE_COOKIES_BROWSER` | — | `chrome` / `firefox` / `edge` |
| `YOUTUBE_USER_AGENT` | — | UA kustom bila YouTube memblokir |
| `RCH_HOST` / `RCH_PORT` | `127.0.0.1` / `8787` | Alamat GUI web |
| `RCH_DOWNLOADS_DIR` | — | Override folder Downloads |

> `GEMINI_API_KEY` di `.env.example` adalah warisan dan tidak dipakai kode
> mana pun. Yang aktif adalah `OPENROUTER_API_KEY`.

---

### Testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                                        # semua test
python -m pytest --cov --cov-report=term-missing        # + coverage
python -m pytest --cov --cov-fail-under=97              # gerbang CI
```

2039 test, coverage 97,25%. Test pipeline clip otomatis **di-skip** bila
dependensi berat belum terpasang (`pytest.importorskip`), jadi suite inti tetap
hijau di mesin ringan maupun di CI.

> Catatan: coverage meng-exclude `clipper/services/*` — pipeline media berat
> (torch, Whisper, mediapipe) tidak dipasang di CI.

---

### Struktur

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

### Security

- Guard **cross-origin** pada semua endpoint tulis — GUI tidak punya
  autentikasi, jadi `Origin` harus cocok dengan `Host`.
- Validasi URL membatasi skema `http`/`https` dan domain YouTube.
- Nama file thumbnail dikunci di dalam folder output (anti path traversal).
- Ekspor CSV dari RCH memproteksi formula spreadsheet.

---

### 📄 Dokumentasi lain

> 📚 Detail lengkap ada di [`docs/PENGGUNAAN.md`](docs/PENGGUNAAN.md),
> [`docs/CLIPPER.md`](docs/CLIPPER.md), dan
> [`docs/CARA_KERJA.md`](docs/CARA_KERJA.md).

## ⚠️ Disclaimer

Untuk penggunaan internal dan edukasi. Unduhan video bergantung pada
`yt-dlp` yang ketersediaannya bisa berubah. Hormati Terms of Service
masing-masing platform. Untuk permintaan penghapusan konten, hubungi pemilik
platform.

---

## 📄 License

MIT — (c) 2026 Ridikc