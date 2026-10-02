# Panduan Install & Penggunaan RCH (untuk Tim)

Dokumen ini berisi cara install dan pakai pustaka **RCH** — lapisan akuisisi
metadata, thumbnail, dan channel/playlist — tanpa perlu menulis kode.

> Untuk generator clip viral, lihat [`CLIPPER.md`](CLIPPER.md).

> Perintah `rch` adalah CLI Python. Semua perintah juga bisa dijalankan tanpa
> instalasi global dengan diawali `python -m rch`.

---

## 1. Persyaratan

| Kebutuhan | Cara cek | Wajib? |
| --- | --- | --- |
| Python ≥ 3.10 | `python --version` | Ya |
| yt-dlp | `yt-dlp --version` | Hanya untuk `channel*`, `video`, `download` |

> **Thumbnail** tidak butuh yt-dlp sama sekali — dibaca langsung dari CDN
> YouTube. yt-dlp hanya diperlukan untuk mengambil daftar video channel,
> metadata, dan mengunduh file video/audio.

> Installer sudah memasang Python (jika perlu) dan yt-dlp secara otomatis,
> jadi biasanya tim tidak perlu melakukan apa pun manual.

---

## 2. Install RCH

### Cara A — Installer (paling simpel untuk tim)

**Windows** — klik ganda `setup-gui.bat`, atau dari cmd:
```cmd
setup-gui.bat
```

**macOS** — klik ganda `setup-gui.command`, atau dari Terminal:
```bash
chmod +x setup-gui.command && ./setup-gui.command
```

**Linux** — dari Terminal:
```bash
chmod +x setup-gui.sh && ./setup-gui.sh
```

Installer otomatis: cek/pasang Python → pasang dependency dari
`requirements.txt` → perbarui yt-dlp → buat shortcut `RCH-GUI` di Desktop.

### Cara B — Manual (dari repo)

```bash
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-
python -m pip install -r requirements.txt
```

### Cara C — Sebagai paket yang bisa dipanggil `rch` di mana saja

```bash
python -m pip install -e .
```

Selesai. Perintah `rch` kini tersedia global.

### Cara D — Tanpa instalasi (langsung dari folder)

Setiap perintah cukup diawali `python -m rch`:
```bash
python -m rch channel https://www.youtube.com/@namachannel
```

---

## 3. GUI Web

```bash
rch web
```
Atau klik ganda shortcut **RCH-GUI** di Desktop.

GUI terbuka di browser pada `http://127.0.0.1:8787` dengan tema terang/gelap
dan tampilan progres langsung. Untuk mengganti port:
```bash
rch web --port 9000
```

> GUI hanya mendengarkan di `127.0.0.1` (lokal). Jangan dipakai di jaringan publik.

---

## 4. Perintah Sehari-hari

Setiap perintah punya bantuan detail:
```bash
rch --help              # semua perintah
rch channel --help      # opsi lengkap satu perintah
```

### Download thumbnail SATU video
```bash
rch thumbnail https://youtu.be/ID_VIDEO maxresdefault
```
- `[SIZE]` opsional: `default`, `mqdefault`, `hqdefault`, `sddefault`, `maxresdefault`.
- Tentukan folder tujuan: `rch thumbnail https://youtu.be/ID --out ./hasil`
- Kemas ke ZIP: `rch thumbnail https://youtu.be/ID --zip`

### Download SEMUA thumbnail dari sebuah channel (langsung ZIP)
```bash
rch channel https://www.youtube.com/@labrobotika1762 hqdefault
```
- Mengambil seluruh video channel → download thumbnail → otomatis jadi satu file ZIP.
- Gunakan `hqdefault` agar video lama (tanpa `maxresdefault`) ikut terdownload.
- Batasi jumlah: `rch channel https://www.youtube.com/@namachannel hqdefault --limit 20`

### Lihat daftar ID video dari channel
```bash
rch list https://www.youtube.com/@namachannel
rch list https://www.youtube.com/@namachannel daftar.txt   # simpan ke file
```

### Lihat info video
```bash
rch info https://youtu.be/ID_VIDEO
```
Menampilkan judul, deskripsi, dan durasi. Bisa lebih dari satu ID:
```bash
rch info https://youtu.be/ID1 https://youtu.be/ID2
rch info https://youtu.be/ID --json hasil.json --csv hasil.csv
```

### Lihat daftar video dari playlist
```bash
rch playlist "https://www.youtube.com/playlist?list=PLxxxx"
```
Menampilkan judul playlist, author, jumlah video, lalu satu baris per video
(`ID`, judul, durasi dalam detik).

| Flag | Keterangan |
| --- | --- |
| `--limit N` | Batasi jumlah video yang ditampilkan |
| `--csv <file>` | Simpan metadata ke CSV |
| `--json <file>` | Simpan metadata ke JSON |

```bash
rch playlist "https://www.youtube.com/playlist?list=PLxxxx" --limit 20 --json hasil.json
```

> ℹ️ Playlist **tidak butuh yt-dlp** — diambil langsung dari halaman playlist.

### Download satu video
```bash
rch video https://youtu.be/ID_VIDEO
rch video https://youtu.be/ID_VIDEO --quality 1080p
rch video https://youtu.be/ID_VIDEO --mp3              # audio
rch video https://youtu.be/ID_VIDEO --out ./hasil
rch video https://youtu.be/ID_VIDEO --subtitles --sub-lang id
```
- `--quality`: `360p`, `480p`, `720p` (default), `1080p`.
- `--mp3` mengunduh sebagai audio MP3.
- `[NAME]` opsional sebagai nama file.

### Download video + thumbnail sekaligus
```bash
rch download https://youtu.be/ID_VIDEO
rch download https://youtu.be/ID_VIDEO --out ./hasil
rch download https://youtu.be/ID_VIDEO --zip
```
Sintaks: `rch download URL [NAME] [OUT]`

### Download LENGKAP semua video dari channel → 1 ZIP
```bash
rch channel-full https://www.youtube.com/@namachannel hqdefault
```
Mendownload semua video channel dan mengemas ke **1 file ZIP**. Setiap video berisi
video, thumbnail, deskripsi, dan link.

```bash
rch channel-full https://www.youtube.com/@namachannel maxresdefault --out ./hasil
```

> ⚠️ Video diambil pada kualitas yang diminta (`720p` secara default). Channel
> dengan banyak video dapat menghasilkan ZIP sangat besar (ratusan MB hingga GB).

### Download metadata channel TANPA video → 1 ZIP
```bash
rch channel-info https://www.youtube.com/@namachannel hqdefault
```
Sama seperti `channel-full` tapi **tidak mengunduh video** (ringan & cepat).

Cocok untuk mendapatkan link, thumbnail, dan deskripsi secara massal. Video bisa
diunduh terpisah kapan saja pakai `rch video`.

```bash
rch channel-info https://www.youtube.com/@namachannel --limit 10
```

> ℹ️ Video yang **unavailable/private/unlisted** akan otomatis diberi nama folder
> `unavailable-<ID>` dengan deskripsi berisi keterangan, dan jumlahnya ditampilkan
> di ringkasan akhir sebagai "Video tidak tersedia".

### Download SEMUA video channel saja → 1 ZIP
```bash
rch channel-video https://www.youtube.com/@namachannel
```
Mendownload **hanya file video** dari semua video channel, dikemas ke 1 ZIP.
Tidak menyertakan thumbnail/deskripsi/link.

```bash
rch channel-video https://www.youtube.com/@namachannel --quality 1080p
rch channel-video https://www.youtube.com/@namachannel --limit 20
```
- `--quality`: `360p`, `480p`, `720p` (default), `1080p`.
- Nama folder memakai judul video (duplikat otomatis diberi suffix ID).

---

## 4a. Opsi Lanjutan (perintah channel)

Flag berikut berlaku untuk `channel`, `channel-info`, `channel-full`, dan `channel-video`:

| Flag | Keterangan |
| --- | --- |
| `--limit N` | Batasi jumlah video |
| `--out <folder>` | Folder tujuan |
| `--quality <q>` | Kualitas video |
| `--concurrency N` | Jumlah download paralel (default 2) |
| `--min-duration <detik>` | Hanya video dengan durasi ≥ N detik |
| `--max-duration <detik>` | Hanya video dengan durasi ≤ N detik |
| `--after <YYYYMMDD>` | Hanya video yang diunggah setelah tanggal |
| `--shorts` | Sertakan Shorts |
| `--subtitles` | Unduh subtitle (bersama video) |
| `--sub-lang <kode>` | Bahasa subtitle (contoh: `id`, `en`) |
| `--resume` | Lanjutkan dari checkpoint |
| `--csv <file>` | Simpan metadata ke CSV |
| `--json <file>` | Simpan metadata ke JSON |
| `--cookies <browser>` | Cookie browser untuk video unlisted/private |

Contoh:
```bash
rch channel-video https://www.youtube.com/@namachannel --min-duration 60 --max-duration 600
rch channel-full https://www.youtube.com/@namachannel --after 20260101 --subtitles
rch channel-info https://www.youtube.com/@namachannel --csv hasil.csv --json hasil.json
rch channel-video https://www.youtube.com/@namachannel --resume
```

### Config preset (`.rchrc.json`)

Simpan default di file `.rchrc.json` (folder kerja atau home):
```json
{
  "quality": "720p",
  "concurrency": 2,
  "cookies": "chrome",
  "proxy": "http://127.0.0.1:8080"
}
```
Prioritas: **env var > `.rchrc.json` > default**.

---

## 5. Lokasi Hasil

Semua hasil tersimpan di folder `downloads/` dengan subfolder bertanggal otomatis, contoh:

```
downloads/
├── 2026-09-26T14-30-00/            # hasil rch thumbnail
├── channel-2026-09-26T14-31-00/
│   └── channel-thumbnails-hqdefault.zip
└── list-2026-09-26T14-32-00/
    └── list-thumbnails-hqdefault.zip
```

Selain file hasil, tiap run channel menulis `report.txt` (ringkasan) dan
menambahkan baris ke `history.log` di folder output yang sama.

---

## 6. Contoh Nyata

### Kasus: Download semua thumbnail channel Lab Robotika
```bash
rch channel https://www.youtube.com/@labrobotika1762 hqdefault --limit 727
```
Output:
```
Ditemukan 727 video. Mendownload thumbnail (hqdefault)...
=== HASIL ===
Total: 727
Sukses: 727
Gagal: 0
ZIP: downloads/channel-.../channel-thumbnails-hqdefault.zip (74.32 MB)
```

### Kasus: Hanya video pendek 1 menit, upregulated 2026
```bash
rch channel-video https://www.youtube.com/@namachannel \
  --min-duration 60 --max-duration 300 --after 20260101 --quality 480p
```

### Kasus: Lanjut job yang terputus (resume)
```bash
rch channel-full https://www.youtube.com/@namachannel --resume
```
Checkpoint `.rch-checkpoint.json` ditulis di folder output, sehingga video yang
sudah selesai tidak diunduh ulang.

---

## 7. Troubleshooting

| Masalah | Solusi |
| --- | --- |
| `rch` tidak dikenali | Jalankan `python -m pip install -e .`, atau pakai `python -m rch` |
| `setup-gui.sh` tidak bisa dijalankan (Mac/Linux) | `chmod +x setup-gui.sh` lalu jalankan lagi |
| Python tidak ditemukan | `python -m pip install -r requirements.txt` dengan Python 3.10+ |
| `rch channel` gagal "yt-dlp not found" | `python -m pip install --upgrade yt-dlp` |
| `rch video` gagal 403 / video tidak bisa diakses | Update yt-dlp: `python -m pip install --upgrade yt-dlp` |
| Beberapa thumbnail gagal (404) | Video dihapus/private. Coba ukuran `hqdefault` |
| Video private/unlisted gagal | Tambahkan `--cookies chrome` (browser harus login YouTube) |
| Download sangat lambat / kena rate limit | Turunkan `--concurrency` ke 1, atau set `RCH_SLEEP_REQUESTS` lebih besar |
| Error 403 saat manyal ZIP | Terlalu agresif. Tutup proses lain, coba lagi nanti |

---

## 8. Merilis Versi Baru (untuk maintainer)

RCH pakai **Semantic Versioning** (MAJOR.MINOR.PATCH) dan **auto-release via GitHub Actions**.

Cara rilis:

1. Naikkan versi di **dua tempat** (wajib sama):
   - `rch/__init__.py` → `__version__`
   - `setup.py` → `version`

2. Pastikan test lulus:
```bash
python -m pytest --cov=rch --cov-fail-under=100
```

3. Commit + tag + push:
```bash
git add -A
git commit -m "feat: deskripsi perubahan"
git tag -a v2.1.0 -m "v2.1.0 — Deskripsi singkat"
git push && git push --tags
```

4. GitHub Actions otomatis:
   - Menjalankan test + coverage 100%.
   - Memastikan tag cocok dengan versi di `rch/__init__.py` dan `setup.py`.
   - Membuat **GitHub Release** dengan changelog otomatis.

Aturan SemVer:
- **MAJOR** — perubahan yang tidak kompatibel (breaking).
- **MINOR** — fitur baru, tetap kompatibel.
- **PATCH** — perbaikan bug.