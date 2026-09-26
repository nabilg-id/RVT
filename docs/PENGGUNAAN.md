# Panduan Install & Penggunaan (untuk Tim)

Dokumen ini berisi cara install dan pakai **Ridikc Content Harvester (RCH)** secara praktis, tanpa perlu menulis kode.

---

## 1. Persyaratan

| Kebutuhan | Cara cek | Wajib? |
| --- | --- | --- |
| Node.js ≥ 16 | `node --version` | Ya (untuk thumbnail) |
| yt-dlp | `yt-dlp --version` | Hanya untuk `rch channel` & `rch video` |

> **Thumbnail** tidak butuh yt-dlp sama sekali. yt-dlp hanya diperlukan untuk mengambil daftar video channel dan resolve link MP4/MP3.

### Install yt-dlp (jika belum ada)

Windows (pakai winget):
```bash
winget install yt-dlp.yt-dlp
```

Atau unduh manual: https://github.com/yt-dlp/yt-dlp/releases

---

## 2. Install RCH

### Cara A — Installer satu-klik (paling simpel untuk tim)

**Windows** — klik ganda `install.bat`, atau dari cmd:
```bash
install.bat
```

**macOS / Linux** — dari Terminal:
```bash
./install.sh
```
(Jika gagal karena permission: `chmod +x install.sh` lalu jalankan lagi.)

Installer otomatis: cek Node.js → `npm install` → install yt-dlp (opsional) → `npm link` agar perintah `rch` tersedia global.

### Cara B — Manual (dari repo)

```bash
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-
npm install
npm link
```

### Cara C — Tanpa `npm link` (langsung dari folder)

Setiap perintah cukup diawali `node bin/rch.js`:
```bash
node bin/rch.js channel https://www.youtube.com/@namachannel
```

---

## 3. Perintah Sehari-hari

Setiap perintah punya bantuan detail:
```bash
rch channel --help
rch list --help
rch video --help
# dst.
```

### `rch help`
Tampilkan semua perintah.

### Download thumbnail SATU video
```bash
rch thumbnail https://youtu.be/ID_VIDEO maxresdefault
```
Ukuran bisa: `default`, `mqdefault`, `hqdefault`, `sddefault`, `maxresdefault`.

### Download SEMUA thumbnail dari sebuah channel (langsung ZIP)
```bash
rch channel https://www.youtube.com/@labrobotika1762 hqdefault
```
- Mengambil seluruh video channel → download thumbnail → otomatis jadi satu file ZIP.
- Gunakan `hqdefault` agar video lama (tanpa `maxresdefault`) ikut terdownload.

### Download thumbnail dari daftar (file .txt)
Buat file `daftar.txt`, satu URL/ID per baris:
```
https://youtu.be/abc123xyz__
https://youtu.be/def456uvw__
https://youtu.be/ghi789rst__
```
Lalu:
```bash
rch list daftar.txt hqdefault
```
Hasil otomatis di-ZIP.

### Lihat info video
```bash
rch info https://youtu.be/ID_VIDEO
```
Menampilkan judul + semua ukuran thumbnail.

### Resolve link download MP4
```bash
rch video https://youtu.be/ID_VIDEO 720p
```
URL ditampilkan ringkas. Untuk URL lengkap:
```bash
rch video https://youtu.be/ID_VIDEO 720p --full
```

---

## 4. Lokasi Hasil

Semua hasil tersimpan di folder `downloads/` dengan subfolder bertanggal otomatis, contoh:

```
downloads/
├── 2026-09-26T14-30-00/            # hasil rch thumbnail
├── channel-2026-09-26T14-31-00/
│   └── channel-thumbnails-hqdefault.zip
└── list-2026-09-26T14-32-00/
    └── list-thumbnails-hqdefault.zip
```

---

## 5. Contoh Nyata

### Kasus: Download semua thumbnail channel Lab Robotika
```bash
rch channel https://www.youtube.com/@labrobotika1762 hqdefault
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

---

## 6. Troubleshooting

| Masalah | Solusi |
| --- | --- |
| `rch` tidak dikenali | Jalankan installer ulang, atau pakai `node bin/rch.js` |
| `install.sh` tidak bisa dijalankan (Mac/Linux) | `chmod +x install.sh` lalu `./install.sh` |
| `rch channel` gagal "yt-dlp not found" | Install yt-dlp (`winget install yt-dlp.yt-dlp` / `brew install yt-dlp`) |
| Beberapa thumbnail gagal (404) | Video dihapus/private. Coba ukuran `hqdefault` |
| `rch video` gagal | Pastikan yt-dlp terpasang & terupdate (`yt-dlp -U`) |
