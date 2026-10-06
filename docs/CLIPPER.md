# Panduan Tim — YouTube Viral Clipper

Cara menjalankan generator clip viral untuk tim R&D.

---

## 1. Instalasi sekali

### Otomatis (disarankan)

| Platform | Perintah |
| --- | --- |
| Windows | dobel-klik `setup-gui.bat` |
| macOS | dobel-klik `setup-gui.command` |
| Linux | `./setup-gui.sh` |

Installer akan:
1. Memeriksa Python 3.10+, memasang lewat winget/brew bila belum ada
2. Memasang dependency dasar (`requirements-dev.txt`)
3. Memasang dependensi clip (`requirements.txt` — torch, moviepy, Whisper, mediapipe)
4. Membuat shortcut `VCLIP-GUI` di Desktop

> ⚠️ Tahap 3 mengunduh sekitar **3 GB** dan bisa memakan **15–30 menit**.
> Tekan `Ctrl+C` untuk melewatinya; GUI tetap jalan, hanya pipeline clip yang
> tidak bisa dipakai.

### Manual

```bash
pip install -r requirements.txt
```

---

## 2. Konfigurasi API key

Salin `clipper/.env.example` ke `.env` di root repo, lalu isi:

```
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx
```

**`OPENROUTER_API_KEY` itu wajib** untuk fitur clip. Tanpa itu, `AISelector()`
melempar `ValueError` saat pipeline dijalankan, jadi job clip berhenti dengan
error — bukan fell back ke klip acak.

Yang *boleh* fell back ke pemilihan acak adalah key yang **ada tapi gagal
dipakai**: nama model salah, respons kosong, atau JSON tidak bisa dibaca.
`select_clips()` menangkap error itu dan memakai `_fallback_selection()`, jadi
clip tetap keluar albeit tanpa analisis AI.

`GEMINI_API_KEY` yang ada di `.env.example` versi lama **tidak dipakai lagi**.

---

## 3. Menjalankan

### GUI web (cara utama)

```bash
python -m clipper.app
```

Browser terbuka otomatis di `http://127.0.0.1:8787`.
Atau dobel-klik shortcut `VCLIP-GUI` di Desktop.

### CLI

```bash
python -m clipper.main
# atau langsung dengan URL:
python -m clipper.main "https://www.youtube.com/watch?v=VIDEO_ID"
```

CLI akan menanyakan: jumlah clip, durasi min/maks, dan gaya caption.

---

## 4. Cara pakai GUI

1. **Tempel URL** video YouTube ke kolom paling atas
2. Klik **Cek Info** — judul, durasi, dan thumbnail muncul (diambil dari pustaka RCH)
3. Atur **Jumlah Clip**, **Durasi Min/Maks**, dan **Gaya Caption**
4. Klik **Generate Clip**
5. Progress bar dan log pipeline tampil langsung di halaman
6. Setelah selesai, daftar clip muncul dengan tautan unduh
7. Semua job tercatat di tabel **Riwayat**

### Gaya caption

| Kunci | Nama |
| --- | --- |
| `clean_white` | Clean White (default) |
| `viral_yellow` | Viral Yellow (Hormozi) |
| `viral_green` | Viral Green |
| `viral_red` | Viral Red |
| `neon_cyan` | Neon Cyan |
| `neon_pink` | Neon Pink |
| `bold_black_bg` | Bold Black BG |

---

## 5. Yang terjadi di balik layar

```
URL YouTube
  ↓  cek info      rch.youtube.metadata + rch.youtube.thumbnail
  ↓  unduh video   clipper.services.youtube_downloader  (yt-dlp)
  ↓  transkripsi   clipper.services.whisper_transcriber (faster-whisper)
  ↓  pilih momen   clipper.services.ai_selector        (OpenRouter)
  ↓  potong        face tracking + caption + hook → transisi → main
  ↓  hasil         ./clips/clip_<n>_<skor>pts_<video>.mp4
```

---

## 6. Output

| Lokasi | Isi |
| --- | --- |
| `./clips/` | File MP4 hasil |
| `./temp/` | Video sumber sementara + `clip-history.jsonl` |
| `./asset/transisi.mp4` | Video transisi opsional (tidak wajib) |

Nama file: `clip_<nomor>_<skor>pts_<id-video>.mp4`, misal
`clip_1_88pts_dQw4w9WgXcQ.mp4`.

---

## 7. Kalau ada masalah

### "yt-dlp tidak bisa mengunduh"
- Tambahkan `YOUTUBE_COOKIES_BROWSER=chrome` di `.env`
- Pastikan video publik (bukan private/age-restricted)
- `python -m pip install -U yt-dlp`

### "Pipeline clip tidak jalan"
Biasanya karena `moviepy` belum terpasang atau versinya salah:

```bash
python -c "import moviepy.editor; print('ok')"
```

Kalau error `No module named moviepy.editor`, moviepy versi 2.x terpasang.
Paksa versi 1.x:

```bash
pip install "moviepy>=1.0.3,<2.0"
```

### "OPENROUTER_API_KEY is not set"
Isi `.env` di root repo dengan API key OpenRouter.

### Face tracking tidak jalan
`mediapipe` kadang bentrok versi dengan numpy/OpenCV. Coba:

```bash
pip install "numpy<2" opencv-python mediapipe
```

Sistem.log menandai face tracking dilewati dan video tetap tergigit, hanya
framing-nya tidak mengikuti wajah.

### Whisper terlalu lambat
Turunkan model di `.env`:

```
WHISPER_MODEL=tiny
```

---

## 8. Perintah upkeep

```bash
# Perbarui yt-dlp (YouTube sering berubah)
python -m pip install -U yt-dlp

# Bersihkan video sementara
# (happus otomatis tiap clip selesai; paksa manual bila perlu)
python -c "from clipper.utils.helpers import cleanup_temp_files; cleanup_temp_files()"

# Jalankan test
python -m pytest
```

---

## 9. Keamanan

- GUI hanya mendengarkan di `127.0.0.1`, bukan jaringan publik.
- Server menolak POST lintas asal, jadi halaman web lain tidak bisa memicu
  clip di komputer Anda.
- Jangan buka GUI dengan `--host 0.0.0.0` di jaringan yang tidak dipercaya.
- `.env` dan `cookies.txt` tidak pernah di-commit ke git.