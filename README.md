<div align="center">

# Ridikc Video Toolkit

### Generator clip viral YouTube dengan GUI web lokal, plus pengunduh channel.

**Tempel satu URL, dapatkan clip pendek siap upload.**
Dibangun untuk penggunaan internal R&D Ridikc.

[![version](https://img.shields.io/badge/version-2.2.1-61afef.svg?style=flat-square)](pyproject.toml)
[![python](https://img.shields.io/badge/python-3.10%2B-61afef.svg?style=flat-square)](https://www.python.org)
[![tests](https://img.shields.io/badge/tests-2045%20passed-3fb950.svg?style=flat-square)](#-testing)
[![coverage](https://img.shields.io/badge/coverage-97%25-3fb950.svg?style=flat-square)](#-testing)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

> 🔒 **INTERNAL USE ONLY** — proprietary, hanya untuk tim R&D internal Ridikc.

</div>

---

## 📑 Daftar Isi

| # | Bagian | Untuk siapa |
| --- | --- | --- |
| 1 | [✨ Fitur](#-fitur) | semua orang |
| 2 | [🚀 Mulai dari sini](#-mulai-dari-sini-pengguna-baru) | pengguna baru |
| 3 | [💻 CLI](#-cli-untuk-yang-terbiasa-dengan-terminal) | yang suka mengetik |
| 4 | [👨‍💻 Detail teknis](#-detail-teknis-untuk-developer) | developer |
| ↳ | [🌐 API HTTP](#-api-http-lokal) | developer / integrasi |
| ↳ | [🔧 Konfigurasi](#-konfigurasi) | developer |
| ↳ | [🧑‍💻 Struktur](#-struktur) | developer |
| ↳ | [🧪 Testing](#-testing) | developer |
| ↳ | [🔐 Keamanan](#-keamanan) | developer / audit |
| 5 | [📚 Dokumen lain](#-dokumen-lain) | semua orang |

---

## ✨ Fitur

| Fitur | Apa artinya |
| --- | --- |
| 🎬 **Clip otomatis** | Tempel URL, klik **Generate**, clip jadi. AI memilih momen terbaiknya sendiri. |
| 📥 **Unduh apa saja** | Satu video, satu channel, atau video + thumbnail + metadata sekaligus. |
| 🗂️ **Papan Video** | Satu tabel yang tahu status tiap video: sudah download, sudah clip, gagal, atau antri. |
| 📊 **Progres live** | Persentase, phase, dan log stdout pipeline tampil langsung di halaman. |
| 🛑 **Bisa dibatalkan** | Tombol **Cancel** menghentikan proses di antara video, tanpa merusak yang sedang berjalan. |
| 🎨 **7 gaya caption** | Clean White, Viral Yellow/Red/Green, Neon Cyan/Pink, Bold Black BG. |
| 📋 **Riwayat** | Semua clip dan semua harvest channel tersimpan dan bisa dicari lagi. |

<details>
<summary><b>🔍 Rincian teknis fitur (untuk developer)</b></summary>

| Komponen | Keterangan |
| --- | --- |
| 🤖 Pemilihan momen | OpenRouter lewat klien OpenAI; model default `openrouter/free` |
| 🎙️ Transkripsi | faster-whisper (`tiny` … `large-v2`), keluaran kata-per-kata untuk caption |
| 👤 Face tracking | mediapipe — titik wajah dipakai untuk zoom & blur |
| ✂️ Render | moviepy 1.x + ffmpeg, transisi hook ke main clip |
| 🌐 GUI web | Satu proses Flask di `127.0.0.1:8787`, dua halaman (`/` dan `/download`) yang berbagi satu registry job |
| 🗃️ Ledger | `video-tracker.jsonl` menyatukan status unduhan dan clip per video |
| 📜 Riwayat clip | Tiap job clip masuk `temp/clip-history.jsonl` |

Pipeline clip berjalan: unduh → transkripsi → pilih momen → face tracking → caption → render.

</details>

---

## 🚀 Mulai dari sini (pengguna baru)

Kamu **tidak perlu** tahu apa itu Python, pip, atau terminal. Cukup 4 langkah.

### 1. Pastikan Python ada

Buka **Command Prompt** (tekan `Win`, ketik `cmd`, Enter), lalu ketik:

```
py -3 --version
```

Kalau muncul `Python 3.10` atau lebih baru, **lanjut ke langkah 2**.
Kalau error, pasang dulu dari <https://www.python.org/downloads/> (pilih versi
3.12), **centang "Add Python to PATH"** saat memasang, lalu *buka ulang*
Command Prompt dan coba lagi.

### 2. Jalankan installer

Di folder proyek ini, **klik dua kali** `setup-gui.bat`.

 Muncul jendela hitam yang bekerja sendiri. Tunggu sampai selesai.

> ⏳ **Ini lama.** Installer mengunduh library besar (torch, Whisper) sekitar
> **3 GB**, bisa **15–30 menit**. Biarkan saja, jangan tutup jendelanya.
>
> 💡 **Mau coba cepat dulu?** Tekan `Ctrl+C` saat installer sedang mengunduh
> library besar. Sisa langkah tetap berjalan dan kamu tetap bisa memakai fitur
> **unduh YouTube**; pipeline clip AI tidak akan aktif.

<details>
<summary><b>macOS / Linux?</b></summary>

Jalankan `setup-gui.sh` dari folder repo:

```bash
chmod +x setup-gui.sh launchers/vclip.sh
./setup-gui.sh
```

Di macOS launcher dibuat di Desktop sebagai `VCLIP-GUI.command` (klik pertama
kali akan minta konfirmasi Gatekeeper). Di Linux tinggal jalankan
`./launchers/vclip.sh`.

> ⚠️ Pakai `setup-gui.sh`, **bukan** `setup-gui.command`. File `.command` itu
> versi lama yang belum memasang project-nya sendiri, jadi perintah `rch` dan
> `vclip` tidak pernah dibuat.

</details>

### 3. Buka aplikasinya

Installer membuat tiga launcher di **Desktop**:

| Launcher | Fungsi |
| --- | --- |
| **VCLIP-GUI** | Buka GUI web di browser |
| **RCH-GUI** | Sama dengan VCLIP-GUI (nama alternatif) |
| **RCH-CLI** | Terminal dengan perintah `rch` siap pakai |

Klik dua kali **VCLIP-GUI**. Browser akan terbuka sendiri ke
`http://127.0.0.1:8787`.

> **Kalau halaman tidak terbuka**, klik kanan `VCLIP-GUI` lalu **Run as
> administrator**. Pesan errornya muncul di jendela itu, foto dan kirim ke tim
> R&D.

### 4. Pakai

- **Halaman `/`** — tempel URL YouTube, klik **Generate**, clip jadi.
- **Halaman `/download`** — tempel URL, klik **Unduh**, video tersimpan.

Selesai. 🎉

> 🔑 **Penting untuk fitur clip:** isi `OPENROUTER_API_KEY` di `.env` (lihat
> [Konfigurasi](#-konfigurasi)). Tanpa key itu, halaman `/` dan
> `/download` tetap jalan normal, tetapi **Generate** akan gagal saat
> menjalankan pipeline.

---

## 💻 CLI (untuk yang terbiasa dengan terminal)

Aktif **Command Prompt** dulu: tekan `Win`, ketik `cmd`, Enter.

```bash
rch --help                 # lihat semua perintah
rch video URL --mp3        # unduh audio saja
rch channel-full URL       # unduh semua video satu channel
rch web                    # jalankan GUI
```

### Semua perintah

| Perintah | Fungsi |
| --- | --- |
| `rch thumbnail URL [SIZE] [--out] [--zip]` | Unduh thumbnail satu video |
| `rch video URL [NAME] [--mp3] [--quality] [--out] [--cookies] [--subtitles] [--sub-lang]` | Unduh satu video / audio |
| `rch download URL [NAME] [OUT] [--zip]` | Unduh video + thumbnail sekaligus |
| `rch channel URL [SIZE]` | Unduh semua thumbnail dari channel |
| `rch channel-info URL [SIZE]` | Info channel, tanpa mengunduh video |
| `rch channel-full URL [SIZE]` | Video + thumbnail + metadata → 1 ZIP |
| `rch channel-video URL` | Unduh video saja dari channel |
| `rch list URL [OUT] [--limit N]` | Tampilkan daftar ID video channel |
| `rch info ID [ID ...] [--json] [--csv]` | Info video, ekspor JSON/CSV |
| `rch playlist URL [--limit N] [--csv] [--json]` | Info playlist |
| `rch web` / `rch gui` / `rch serve` | Jalankan GUI web lokal (`--host`, `--port`) |

Perintah channel (`channel`, `channel-info`, `channel-full`, `channel-video`)
juga menerima `--limit`, `--concurrency`, `--min-duration`, `--max-duration`,
`--after`, `--shorts`, `--subtitles`, `--resume`, `--csv`, `--json`, dan
`--out`.

### Contoh

```bash
rch thumbnail https://youtu.be/ID_VIDEO maxresdefault
rch video https://youtu.be/ID_VIDEO --quality 1080p
rch video https://youtu.be/ID_VIDEO --mp3                # audio saja
rch download https://youtu.be/ID_VIDEO --zip
rch channel https://www.youtube.com/@namachannel
rch channel-full https://www.youtube.com/@namachannel --resume
```

Ukuran thumbnail (`SIZE`): `maxresdefault`, `hqdefault`, `mqdefault`,
`sddefault`, `default`. Kualitas video (`--quality`): `360p`, `480p`, `720p`
(default), `1080p`.

Semua hasil unduhan tersimpan di satu folder berbranded di dalam folder
**Downloads** sistem:

```
Downloads/Ridikc Video Toolkit/       # video + thumbnail + metadata
Downloads/Ridikc Video Toolkit/clips/ # clip hasil render
```

Ubah dengan `--out <folder>` atau variabel `RCH_DOWNLOADS_DIR`.

> 💡 CLI tambahan yang tersedia kalau butuh kontrol lebih detail:
> `python -m clipper.main` (mode interaktif clip), `vclip` (GUI), dan
> `python -m rch` (kalau `rch` tidak ada di PATH).

---

## 👨‍💻 Detail teknis (untuk developer)

Mulai dari sini semua yang khusus developer: API, konfigurasi, struktur, dan
testing.

### 🌐 API HTTP lokal

Semua endpoint dilayani satu proses Flask di `127.0.0.1:8787`. Halaman dan API
berbagi **satu registry job**, jadi `jobId` dari clip maupun dari unduhan dibaca
dari `/api/status/<id>` yang sama.

#### Halaman

| Method | Path | Keterangan |
| --- | --- | --- |
| GET | `/` | Halaman Clipper |
| GET | `/download` | Halaman Downloader |
| GET | `/clips/<name>` | Sajikan file clip dari `OUTPUT_DIR` |

#### Clipper

| Method | Path | Body / Query | Keterangan |
| --- | --- | --- | --- |
| GET | `/api/styles` | — | Daftar `{key, name}` gaya caption |
| POST | `/api/preview` | `{url}` | Metadata + thumbnail sebelum generate |
| POST | `/api/clip` | `{url, numClips, minDur, maxDur, style}` | Mulai job clip, balas `{jobId, status}` |
| GET | `/api/status/<job_id>` | — | Status job: `running`/`done`/`error`/`cancelled`, progress, log |
| POST | `/api/cancel/<job_id>` | — | Batalkan job, balas snapshot terbaru |
| GET | `/api/history` | — | Riwayat clip (default 20 terbaru) |
| POST | `/api/quit` | — | Matikan server |

#### Papan Video

| Method | Path | Body / Query | Keterangan |
| --- | --- | --- | --- |
| GET | `/api/videos` | `?status=&q=&limit=` | Daftar video + status + jumlah per status |
| POST | `/api/videos/queue` | `{videoId}` | Tandai antri clip |
| POST | `/api/videos/unqueue` | `{videoId}` | Batalkan tanda antri |

#### Downloader

| Method | Path | Body | Keterangan |
| --- | --- | --- | --- |
| POST | `/api/info` | `{url}` | Metadata + thumbnail satu video |
| POST | `/api/download` | `{url, format, quality, outputDir}` | Unduh satu video (sinkron) |
| POST | `/api/playlist` | `{url, limit}` | Metadata playlist |
| POST | `/api/channel-info` | `{url, size, limit, ...}` | Job: info channel |
| POST | `/api/channel-video` | `{url, limit, ...}` | Job: unduh video channel |
| POST | `/api/channel-full` | `{url, size, limit, ...}` | Job: video + thumbnail + metadata |
| GET | `/api/runs` | `?out=` | Riwayat harvest, plus status per video |

**Kode status:** `200` sukses, `400` input tidak valid, `404` job atau video
tidak ditemukan, `409` antrean tidak berlaku (clip sudah jalan), `500` kegagalan
internal. Semua kegagalan dikembalikan sebagai JSON `{"error": ...}` atau
`{"status": false, "message": ...}` — tidak pernah menampilkan traceback.

**Contoh:**

```bash
curl -X POST http://127.0.0.1:8787/api/preview \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/ID_VIDEO"}'
```

---

### 🔧 Konfigurasi

#### `.env`

Berkas `.env`-nya sendiri opsional — tanpa itu aplikasi tetap jalan. Salin
`clipper/.env.example` ke `.env` di root repo **atau** di `clipper/`; keduanya
dibaca.

Path relatif di-anchor ke root repo, bukan ke folder dari mana perintah
dijalankan. Path absolut juga boleh.

| Key | Default | Keterangan |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | — | **Wajib untuk fitur clip.** Kosong = pipeline clip gagal |
| `OPENROUTER_MODEL` | `openrouter/free` | Router ke model gratis |
| `WHISPER_MODEL` | `medium` | `tiny` / `base` / `small` / `medium` / `large-v2` |
| `WHISPER_LANGUAGE` | auto | `id` / `en`, kosongkan untuk auto |
| `OUTPUT_DIR` | `Downloads/Ridikc Video Toolkit/clips` | Folder hasil clip |
| `TEMP_DIR` | `./temp` | Folder kerja + riwayat |
| `ASSET_DIR` | `./asset` | Aset transition (`transisi.mp4`) |
| `COOKIES_FILE` | `./cookies.txt` | Cookie YouTube bila perlu |
| `YOUTUBE_COOKIES_BROWSER` | — | `chrome` / `firefox` / `edge` |
| `YOUTUBE_COOKIES_CONTENT` | — | Cookie mentah sebagai string |
| `YOUTUBE_USER_AGENT` | — | UA kustom bila YouTube memblokir |
| `RCH_HOST` / `RCH_PORT` | `127.0.0.1` / `8787` | Alamat GUI web |
| `RCH_HISTORY_LIMIT` | `20` | Jumlah riwayat clip yang ditampilkan |

Nilai environment selalu dipangkas spasi di kedua ujung. `WHISPER_MODEL=medium `
(trailing space) tidak akan lagi ditolak faster-whisper dengan "Invalid model
size".

> ⚠️ `clipper/.env.example` menyertakan `OUTPUT_DIR=./clips`, yang **menimpa**
> default branded di atas. Hapus baris itu kalau kamu ingin clip otomatis masuk
> ke `Downloads/Ridikc Video Toolkit/clips`.

> `GEMINI_API_KEY` di `.env.example` adalah warisan dan tidak dipakai kode mana
> pun. Yang aktif adalah `OPENROUTER_API_KEY`.

#### Konfigurasi mesin RCH

Variabel ini dibaca `rch/config.py` (CLI dan GUI). Semuanya bisa juga ditulis di
file `.rchrc.json` di folder kerja atau di home.

| Key | Default | Keterangan |
| --- | --- | --- |
| `RCH_QUALITY` | `720p` | Kualitas video default |
| `RCH_CONCURRENCY` | `2` | Jumlah worker paralel |
| `RCH_RETRIES` | `3` | Jumlah percobaan ulang |
| `RCH_SLEEP_REQUESTS` | `0.5` | Jeda antar request (detik) |
| `RCH_SLEEP_INTERVAL` | `0.5` | Jeda rentang request |
| `RCH_MAX_SLEEP_INTERVAL` | `2` | Batas atas jeda |
| `RCH_COOKIES` | — | Browser untuk `--cookies-from-browser` |
| `RCH_USER_AGENT` | UA Chrome 128 | User agent |
| `RCH_PROXY` | — | Proxy untuk yt-dlp |
| `RCH_LIMIT_RATE` | — | Batasi bandwidth unduhan |
| `RCH_DOWNLOADS_DIR` | folder Downloads OS | Override folder output |

Folder Downloads sistem dibaca lewat `SHGetKnownFolderPath` di Windows, jadi
folder yang dipindahkan OneDrive tetap terdeteksi. Kalau tidak ada yang cocok,
aplikasi jatuh ke `downloads/` di dalam repo.

---

### 🧑‍💻 Struktur

```
rch/                        # pustaka akuisisi (dipakai CLI dan GUI)
├── cli.py                  # perintah click: thumbnail, video, channel, ...
├── config.py               # env + .rchrc.json
├── core/
│   ├── paths.py            # folder Downloads per OS + branded product folder
│   ├── jobs.py             # registry job bersama
│   ├── tracker.py          # ledger video-tracker.jsonl
│   ├── report.py           # laporan + riwayat harvest
│   ├── events.py           # emitter progress/phase
│   ├── checkpoint.py       # --resume
│   ├── export.py           # ekspor CSV/JSON
│   ├── http.py             # HTTP dengan retry
│   └── zip_util.py
└── youtube/                # metadata, thumbnail, video, channel, playlist

clipper/
├── app.py                  # Flask: 2 halaman + semua route API
├── config.py               # konfigurasi & path clipper
├── progress.py             # tangkap stdout pipeline jadi progres
├── main.py                 # CLI interaktif clip
├── services/               # pipeline: downloader, whisper, ai_selector,
│                           #          caption_maker, face_tracker, video_processor
├── styles/caption_styles.py# 7 gaya caption
├── templates/ static/      # frontend GUI
├── utils/helpers.py        # klip acak, bersih-bersih file
└── tests/                  # test pipeline berat (auto-skip bila deps kurang)

launchers/                  # vclip.sh / vclip.bat / rch-cli.bat / VCLIP.command
tests/                      # test RCH, clipper web, launcher
docs/                       # PENGGUNAAN, CLIPPER, CARA_KERJA, PANDUAN-TIM
```

#### Instalasi manual

<details>
<summary>Linux / macOS / Windows</summary>

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

Butuh Python 3.10+.

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

---

### 🧪 Testing

```bash
python -m pip install -r requirements-dev.txt
python -m pytest                                        # semua test
python -m pytest --cov --cov-report=term-missing        # + coverage
python -m pytest --cov --cov-fail-under=97              # gerbang CI
```

**2045 test, semuanya lulus, coverage 97%.** Test pipeline clip otomatis
**di-skip** bila dependency berat belum terpasang (`pytest.importorskip`), jadi
suite inti tetap hijau di mesin ringan maupun di CI.

> `requirements-dev.txt` sengaja tidak memakai `requirements.txt`: torch +
> faster-whisper + mediapipe akan membuat runner CI jauh lebih besar dan lambat.

> Catatan: coverage meng-exclude `clipper/services/*` — pipeline media berat
> (torch, Whisper, mediapipe) tidak dipasang di CI. Test pipeline-nya ada di
> `clipper/tests/` dan skip sendiri bila dependency belum ada.

---

### 🔐 Keamanan

- Guard **cross-origin** pada semua request yang mengubah data — GUI tidak punya
  autentikasi, jadi header `Origin` harus cocok dengan `Host`.
- Validasi URL membatasi skema `http`/`https` dan domain YouTube.
- Input angka di-clamp (`numClips` 1–20, durasi 5–600 detik) dan `minDur` harus
  lebih kecil dari `maxDur`.
- Nama file thumbnail dikunci di dalam folder output (anti path traversal).
- `videoId` divalidasi sebagai pola 11 karakter sebelum masuk ledger.
- Ekspor CSV dari RCH memproteksi formula spreadsheet.
- Server hanya berjalan lokal; tidak ada autentikasi, jadi jangan bind ke
  `0.0.0.0`.

---

## 📚 Dokumen lain

| Dokumen | Isi |
| --- | --- |
| [`docs/PENGGUNAAN.md`](docs/PENGGUNAAN.md) | Panduan pakai lengkap |
| [`docs/CLIPPER.md`](docs/CLIPPER.md) | Detail pipeline clip |
| [`docs/CARA_KERJA.md`](docs/CARA_KERJA.md) | Cara kerja internal |
| [`docs/PANDUAN-TIM.md`](docs/PANDUAN-TIM.md) | Panduan untuk tim |

---

## ⚠️ Disclaimer

Untuk penggunaan internal dan edukasi. Unduhan video bergantung pada `yt-dlp`
yang ketersediaannya bisa berubah. Hormati Terms of Service masing-masing
platform. Untuk permintaan penghapusan konten, hubungi pemilik platform.

---

## 📄 License

MIT — (c) 2026 Ridikc
