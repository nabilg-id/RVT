# Setup Tim — OpenRouter & Penggunaan per Mesin

Dokumen ini untuk anggota tim yang installs aplikasi di mesin masing-masing:
**Windows**, **macOS**, dan **Linux**.

---

# BAGIAN 1 — Mendapatkan OpenRouter API Key

OpenRouter menyediakan satu API key yang dipakai **semua anggota tim**.
Jangan bikin key pribadi per orang — pakai satu key bersama, atau satu key per
anggota kalau lebih mudah dilacak.

## 1.1 Bikin akun

1. Buka **https://openrouter.ai**
2. Klik **Sign In** (atau **Sign Up** kalau belum punya)
3. Masuk pakai **Google**, **GitHub**, atau **email**
4. Verifikasi email bila diminta

## 1.2 Top-up saldo (wajib)

> ⚠️ **OpenRouter tidak punya kuota gratis bawaan.** Tanpa saldo, semua request
> akan ditolak dengan error `402 Insufficient credits`.

1. Klik ikon **kredit/dompet** di kanan atas, atau buka **https://openrouter.ai/credits**
2. Pilih nominal. Saran untuk tim kecil: **$5 – $10 per orang per bulan**
3. Isi metode pembayaran (kartu kredit / debit, atau crypto kalau tersedia)
4. Klik **Add Credits**

> 💡 **Tips hemat:** default aplikasi memakai model gratis
> `arcee-ai/trinity-large-preview:free`. Kalau model gratis masih tersedia,
> biaya bisa nol. Cek daftar model gratis di
> **https://openrouter.ai/models?fmt=cards&max_price=0**

## 1.3 Bikin API key

1. Buka **https://openrouter.ai/keys**
2. Klik **Create API Key**
3. Isi:
   - **Name**: `RCH Tim Ridikc` (atau `RCH - <nama kamu>`)
   - **Budget**: isi limit dollar supaya tidak kebablasan
     (misal `5.00` untuk $5/bulan)
4. Klik **Create**
5. **Salin key-nya sekarang.** Key hanya ditampilkan satu kali.

Format key diawali `sk-or-v1-...`

> 🔒 **Jangan pernah** commit key ini ke git, simpan di chat grup, atau
> bagikan lewat WhatsApp. Key = uang tim.

## 1.4 Test key (opsional tapi berguna)

Buka terminal, tempel (ganti `<KEY>`):

```bash
curl https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer <KEY>" \
  -H "Content-Type: application/json" \
  -d '{"model":"arcee-ai/trinity-large-preview:free","messages":[{"role":"user","content":"ping"}]}'
```

Kalau dapat jawaban JSON, key valid. Kalau `401`, key salah. Kalau `402`,
saldo habis.

## 1.5 Kalau key bocor

1. Buka **https://openrouter.ai/keys**
2. Klik **Delete** atau **Disable** pada key yang bocor
3. Bikin key baru
4. Update `.env` semua orang

---

# BAGIAN 2 — Menaruh Key di Mesin

Semua anggota tim butuh file `.env` di **root repo**.

## Windows (CMD)

```cmd
copy clipper\.env.example .env
notepad .env
```

## macOS / Linux

```bash
cp clipper/.env.example .env
nano .env      # atau: code .env
```

## Isi `.env`

```ini
OPENROUTER_API_KEY=sk-or-v1-ganti-dengan-key-mu
OPENROUTER_MODEL=arcee-ai/trinity-large-preview:free
WHISPER_MODEL=medium
WHISPER_LANGUAGE=id
```

> ✅ File `.env` sudah masuk `.gitignore`, jadi aman tidak ikut ter-commit.

---

# BAGIAN 3 — Instalasi per Operating System

## Yang perlu disiapkan (semua OS)

| Kebutuhan | Versi | Cara cek |
| --- | --- | --- |
| Python | 3.10 – 3.14 | `python --version` |
| Git | terbaru | `git --version` |
| Disk kosong | **±4 GB** | — |
| Internet | stabil, terutama saat pertama kali menjalankan | — |

> **Kenapa 4 GB?** `torch` (±200 MB versi CPU) + `faster-whisper` + model Whisper
> yang diunduh otomatis saat pertama transkripsi (±1,5 GB untuk `medium`).

---

## 3.1 🪟 Windows

### Cek Python

Buka **CMD**:

```cmd
python --version
py -3 --version
```

Kalau muncul pesan *"tidak dikenali sebagai perintah"*, pasang Python:

```cmd
winget install --id Python.Python.3.12 -e
```

Tutup & buka ulang CMD.

> ⚠️ **Jangan lupa centang "Add Python to PATH"** saat instalasi manual.

### Cara paling mudah

1. Extract folder proyek
2. **Dobel-klik `setup-gui.bat`**
3. Tunggu sampai selesai (bisa **15–30 menit**)
4. Shortcut **VCLIP-GUI** muncul di Desktop

### Cara manual

```cmd
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-

py -3 -m pip install --upgrade pip

REM torch versi CPU (jauh lebih kecil daripada build default ~2,5 GB)
py -3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu

py -3 -m pip install -r requirements.txt
```

### Jalankan

```cmd
REM GUI (dibuka di browser)
py -3 -m clipper.app

REM atau CLI interaktif
py -3 -m clipper.main
```

> **Catatan CMD Windows:** kalau ketik `python` tidak ada, pakai **`py -3`**.

---

## 3.2 🍎 macOS

### Cek Python

```bash
python3 --version
```

Kalau belum ada:

```bash
brew install python@3.12
```

### Cara paling mudah

1. Extract folder proyek
2. **Dobel-klik `setup-gui.command`**
3. Sekali saja, macOS akan minta izin — klik **Open** → **Open**
4. Shortcut **VCLIP-GUI** muncul di Desktop

### Cara manual

```bash
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-

python3 -m pip install --upgrade pip
python3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install -r requirements.txt
```

### Jalankan

```bash
python3 -m clipper.app     # GUI
python3 -m clipper.main    # CLI
```

### Permission denied saat jalankan `.sh`?

```bash
chmod +x launchers/vclip.sh setup-gui.sh
```

---

## 3.3 🐧 Linux

### Cek Python

```bash
python3 --version
```

Pasang bila belum ada:

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# Fedora
sudo dnf install python3 python3-pip

# Arch
sudo pacman -S python
```

### Cara paling mudah

```bash
chmod +x setup-gui.sh
./setup-gui.sh
```

### Cara manual

```bash
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-

python3 -m pip install --upgrade pip
python3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install -r requirements.txt
```

### Jalankan

```bash
python3 -m clipper.app     # GUI
python3 -m clipper.main    # CLI
```

### Error OpenCV di server tanpa GUI (headless)

Di headless server (tanpa desktop):

```bash
pip install opencv-python-headless
pip uninstall opencv-python
```

---

# BAGIAN 4 — Cara Pakai Sehari-hari

## GUI (cara utama untuk semua orang)

```bash
python -m clipper.app
```

Browser terbuka di `http://127.0.0.1:8787`

1. **Tempel URL** YouTube ke kolom atas
2. Klik **Cek Info** → judul, durasi, thumbnail muncul
3. Atur **Jumlah Clip**, **Durasi Min/Maks**, **Gaya Caption**
4. Klik **Generate Clip**
5. Tunggu progress bar; log pipeline tampil langsung
6. Klik link untuk unduh clip
7. Semua job tercatat di tabel **Riwayat**

## CLI

```bash
python -m clipper.main "https://www.youtube.com/watch?v=VIDEO_ID"
```

CLI akan menanyakan jumlah clip, durasi, dan gaya caption.

---

# BAGIAN 5 — Struktur Folder di Mesin

| Lokasi | Isi |
| --- | --- |
| `./clips/` | Hasil clip MP4 |
| `./temp/` | Video sementara + riwayat |
| `./asset/` | Model deteksi wajah (auto-download) |
| `./.env` | API key (**jangan** di-share) |

---

# BAGIAN 6 — Kalau Ada Masalah

| Gejala | Penyebab & Solusi |
|---| --- |
| `OPENROUTER_API_KEY is not set` | `.env` belum diisi, atau salah lokasi |
| Error `402 Insufficient credits` | Saldo OpenRouter habis → top-up |
| Error `401` | API key salah / sudah dihapus |
| `No module named moviepy.editor` | moviepy versi 2 terpasang → `pip install "moviepy>=1.0.3,<2.0"` |
| `No module named 'rch'` | Jalankan dari root repo, bukan subfolder |
| Face tracking tidak jalan | Model `asset/blaze_face_short_range.tflite` hilang → hapus `temp/`, jalankan ulang |
| `yt-dlp` gagal download | Video private/terbatas, atau YouTube memblokir → isi `YOUTUBE_COOKIES_BROWSER=chrome` |
| Port 8787 sudah dipakai | Server lain jalan → ganti `RCH_PORT=8788` di `.env` |
| Whisper sangat lambat | Turunkan model: `WHISPER_MODEL=tiny` |

### Update yt-dlp (YouTube sering berubah)

```bash
python -m pip install -U yt-dlp
```

Lakukan ini kalau tiba-tiba semua unduhan gagal.

---

# BAGIAN 7 — Update Aplikasi

```bash
cd Ridikc-Content-Harvester-RCH-
git pull
python -m pip install -r requirements.txt
```

`.env` dan `clips/` tidak akan hilang saat `git pull`.

---

# BAGIAN 8 — Ringkasan Cepat

```bash
# 1. clone
git clone https://github.com/nabilg-id/Ridikc-Content-Harvester-RCH-.git
cd Ridikc-Content-Harvester-RCH-

# 2. install (pakai torch CPU, jauh lebih kecil)
python3 -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python3 -m pip install -r requirements.txt

# 3. api key
cp clipper/.env.example .env
# edit .env, isi OPENROUTER_API_KEY

# 4. jalankan
python3 -m clipper.app
```

Di Windows, ganti `python3` dengan **`py -3`**.

---

> 🔒 **Jaga kerahasiaan API key.** Satu key bocor = bisa dipakai orang lain dan
> menguras saldo tim. Kalau bocor, hapus di https://openrouter.ai/keys lalu bikin
> yang baru.