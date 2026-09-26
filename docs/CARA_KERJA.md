# Cara Kerja Ridikc Content Harvester

Dokumen ini menjelaskan arsitektur, alur kerja, dan cara pengujian **Ridikc Content Harvester** untuk tim R&D.

---

## 1. Ringkasan

Ridikc Content Harvester adalah **library Node.js (CommonJS, murni JavaScript, tanpa framework)** untuk mengekstrak link download media dan thumbnail dari YouTube. Arsitekturnya **extensible multi-platform** (meniru pola `scrapr`), sehingga platform lain (TikTok, Instagram, dll.) tinggal ditambahkan sebagai modul di `lib/`.

### Teknologi

| Komponen | Keterangan |
| --- | --- |
| Runtime | Node.js ≥ 16 |
| HTTP client | `axios` |
| HTML parsing | `cheerio` (opsional, fallback provider) |
| Backend video/audio | `yt-dlp` (CLI lokal, direkomendasikan) |
| ZIP | implementasi murni Node (`lib/core/zip.js`), tanpa dependensi |

---

## 2. Struktur Direktori

```
#6downloader/
├── package.json
├── README.md
├── LICENSE
├── index.js                  # entry point: export { youtube }
├── lib/
│   ├── core/
│   │   └── zip.js            # helper ZIP murni Node
│   └── youtube/
│       ├── index.js          # dispatcher: export semua fitur
│       ├── thumbnail.js      # resolve + download thumbnail (tunggal & massal)
│       ├── ytmp3.js          # resolve MP4/MP3 (yt-dlp + fallback cnvmp3)
│       ├── ytmp3gg.js        # resolve multi-kualitas (yt-dlp)
│       └── playlist.js       # parse playlist YouTube
└── test/
    └── test.js               # smoke test semua fitur
```

---

## 3. Alur Kerja

```
Kamu kasih URL YouTube
        │
        ▼
index.js ──► lib/youtube/  (dispatcher per fitur)
        │
        ├─ thumbnail.js ──► extractVideoId (regex) ──► i.ytimg.com/vi/<ID>/<size>.jpg
        │                       │                            │
        │                       ▼                            ▼
        │                fetchTitle (oEmbed)        download / simpan / zip
        │
        ├─ ytmp3.js ──► extractVideoId ──► yt-dlp (lokal) ──► URL MP4/MP3 langsung
        │                                       │
        │                                       └─ fallback: cnvmp3 (HTTP)
        │
        └─ playlist.js ──► fetch HTML playlist ──► parse ytInitialData ──► daftar video
```

### Pola return (konsisten di semua fungsi)

```js
// sukses
{ status: true, result: { ... } }
// gagal
{ status: false, message: "..." }
```

---

## 4. Detail Tiap Modul

### 4.1 `lib/youtube/thumbnail.js`

Fokus utama produk. **Tanpa API key, tanpa layanan pihak ketiga, legal & stabil.**

- `extractVideoId(url)` — ekstrak ID 11 karakter via regex. Mendukung `youtube.com/watch?v=`, `youtu.be/`, `shorts/`, `embed/`, `live/`.
- `fetchTitle(videoId)` — ambil judul dari oEmbed API (`youtube.com/oembed`). Gratis, tanpa key.
- `thumbnailUrl(videoId, size)` — susun URL thumbnail dari CDN YouTube.

Ukuran yang didukung:
`default`, `mqdefault`, `hqdefault`, `sddefault`, `maxresdefault`.

Fungsi publik:

| Fungsi | Deskripsi |
| --- | --- |
| `thumbnail(url, opts)` | Resolve semua ukuran thumbnail |
| `downloadThumbnail(url, opts)` | Download satu thumbnail ke disk |
| `downloadThumbnails(urls, opts)` | Download massal (konkuren) + opsional ZIP |
| `thumbnailUrls(urls, opts)` | Resolve URL massal tanpa simpan file |

### 4.2 `lib/youtube/ytmp3.js`

Resolve link download MP4/MP3.

1. Ekstrak video ID.
2. Ambil judul/thumbnail via oEmbed (fallback metadata).
3. Panggil `yt-dlp --dump-single-json` untuk baca metadata + URL format langsung.
4. Pilih format sesuai `format` (`mp4`/`mp3`) dan `quality` (mis. `720p`).
5. Jika `yt-dlp` tidak tersedia/gagal → fallback ke provider HTTP `cnvmp3`.

### 4.3 `lib/youtube/ytmp3gg.js`

Alternatif resolver berbasis `yt-dlp`:
- MP3 → audio terbaik (bitrate tertinggi).
- MP4 → kembalikan daftar beberapa kualitas.

### 4.4 `lib/youtube/playlist.js`

1. Ekstrak `list=` dari URL.
2. Fetch HTML playlist.
3. Cari `var ytInitialData` (JSON tersembunyi di `<script>`).
4. Parse rekursif untuk judul, author, thumbnail, dan daftar item video.

---

## 5. Cara Pengujian

### Smoke test bawaan (semua fitur)

```bash
npm test
```

### Tes fitur satu per satu

```bash
# Resolve thumbnail
node -e "const {youtube}=require('./index'); youtube.thumbnail('https://youtu.be/dQw4w9WgXcQ').then(r=>console.log(JSON.stringify(r,null,2)))"

# Download satu thumbnail
node -e "const {youtube}=require('./index'); youtube.downloadThumbnail('https://youtu.be/dQw4w9WgXcQ',{size:'maxresdefault',outputDir:'./hasil'}).then(console.log)"

# Download massal + ZIP
node -e "const {youtube}=require('./index'); youtube.downloadThumbnails(['https://youtu.be/dQw4w9WgXcQ','https://youtu.be/9bZkp7q19f0'],{size:'hqdefault',zip:true,outputDir:'./hasil'}).then(console.log)"

# Resolve MP4
node -e "const {youtube}=require('./index'); youtube.ytmp3('https://youtu.be/dQw4w9WgXcQ',{format:'mp4',quality:'720p'}).then(console.log)"
```

### Tes dengan video sendiri

```bash
# Windows (cmd)
set RIDIKC_TEST_URL=https://youtu.be/ID_KAMU && npm test

# PowerShell
$env:RIDIKC_TEST_URL="https://youtu.be/ID_KAMU"; npm test
```

---

## 6. Contoh Pemakaian Lengkap

```js
const { youtube } = require("ridikc-content-harvester");

(async () => {
  // Resolve semua ukuran thumbnail
  const thumbs = await youtube.thumbnail("https://youtu.be/dQw4w9WgXcQ");
  console.log(thumbs.result.thumbnails);

  // Download massal + ZIP
  const batch = await youtube.downloadThumbnails(
    ["https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/9bZkp7q19f0"],
    { size: "hqdefault", outputDir: "./downloads", concurrency: 5, zip: true }
  );
  console.log(batch.result.zip); // { path, sizeBytes, fileCount }

  // Resolve MP4
  const video = await youtube.ytmp3("https://youtu.be/dQw4w9WgXcQ", {
    format: "mp4", quality: "720p",
  });
  console.log(video.result.downloads);
})();
```

---

## 7. Catatan / Keterbatasan

- **Thumbnail**: stabil, cepat, legal, tanpa dependensi eksternal. Direkomendasikan untuk kebutuhan produksi.
- **Video/audio**: butuh `yt-dlp` terpasang (cek: `yt-dlp --version`). Tanpa `yt-dlp`, fallback ke provider pihak ketiga yang rawan berubah/diblokir (Cloudflare, anti-bot).
- **Disclaimer**: resolve stream video/audio penuh dapat melanggar ToS platform terkait. Gunakan untuk keperluan R&D/internal yang diizinkan.
