<div align="center">

# Ridikc Content Harvester

**Universal Social Media & Streaming Downloader for Node.js.**

_Extract direct MP4, MP3, and image links from YouTube and more platforms — built for internal R&D use by Ridikc._

[![node version](https://img.shields.io/badge/node-%3E%3D%2016.x-61afef.svg?style=flat-square)](https://nodejs.org)
[![license](https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square)](LICENSE)

</div>

---

## ✨ Features

- 🎬 **YouTube** — resolve MP4 / MP3 download links via multiple providers.
- 🖼️ **YouTube Thumbnails** — resolve every thumbnail size (default → maxresdefault) and download them to disk in bulk.
- 📃 **YouTube Playlists** — extract full playlist metadata and item list.
- 🧩 **Extensible multi-platform** architecture (add TikTok, Instagram, etc. by dropping a module into `lib/`).

---

## 📦 Installation

```bash
npm install ridikc-content-harvester
```

---

## 🚀 Quick Start

```js
const { youtube } = require("ridikc-content-harvester");

(async () => {
  // Resolve all thumbnail sizes
  const thumbs = await youtube.thumbnail("https://youtu.be/VIDEO_ID");
  console.log(thumbs);

  // Download a thumbnail to disk
  const dl = await youtube.downloadThumbnail("https://youtu.be/VIDEO_ID", {
    size: "maxresdefault",
    outputDir: "./downloads",
  });
  console.log(dl);

  // Download many thumbnails in bulk (concurrent), optionally zipped
  const batch = await youtube.downloadThumbnails(
    ["https://youtu.be/AAA", "https://youtu.be/BBB", "https://youtu.be/CCC"],
    { size: "hqdefault", outputDir: "./downloads", concurrency: 5, zip: true }
  );
  console.log(batch);

  // Resolve MP4 / MP3 download links (via yt-dlp, with HTTP fallback)
  const video = await youtube.ytmp3("https://youtu.be/VIDEO_ID", { format: "mp4", quality: "720p" });
  console.log(video);

  // Resolve audio-only / alternative format
  const alt = await youtube.ytmp3gg("https://youtu.be/VIDEO_ID", { format: "mp3" });
  console.log(alt);

  // Extract playlist
  const playlist = await youtube.playlist("https://youtube.com/playlist?list=PLAYLIST_ID");
  console.log(playlist);
})();
```

---

## 📚 API

Every function resolves to a uniform shape:

```js
// success
{ status: true, result: { ... } }
// failure
{ status: false, message: "..." }
```

### `youtube.thumbnail(url, options?)`
Resolves every available thumbnail size from YouTube's CDN (stable, no third-party dependency).

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `sizes` | `string[]` | all | List of sizes (`default`, `mqdefault`, `hqdefault`, `sddefault`, `maxresdefault`) |

### `youtube.downloadThumbnail(url, options?)`
Resolves and downloads a thumbnail to disk.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `size` | `string` | `"maxresdefault"` | Thumbnail size |
| `outputDir` | `string` | `"./downloads"` | Destination directory |
| `filename` | `string` | auto (slug of title) | Output filename |

### `youtube.downloadThumbnails(urls, options?)`
Bulk-download thumbnails from many URLs concurrently.

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `size` | `string` | `"maxresdefault"` | Thumbnail size |
| `outputDir` | `string` | `"./downloads"` | Destination directory |
| `concurrency` | `number` | `5` | Number of parallel downloads |
| `filename` | `function` | auto | `(videoId, title, index) => name` |
| `zip` | `boolean` | `false` | Package downloaded files into a ZIP archive |
| `zipName` | `string` | `"thumbnails-<size>.zip"` | Output ZIP filename |

`urls` may be a plain string array or `{ url, filename }` objects. When `zip` is enabled, the result includes a `zip` object with `{ path, sizeBytes, fileCount }`.

### `youtube.ytmp3(url, options?)`
Resolve direct MP4/MP3 download links. Uses local `yt-dlp` when available, with an HTTP provider fallback (`cnvmp3`).

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `format` | `string` | `"mp4"` | `"mp4"` or `"mp3"` |
| `quality` | `string` | `"720p"` | e.g. `"360p"`, `"480p"`, `"720p"`, `"1080p"` |

### `youtube.ytmp3gg(url, options?)`
Resolve direct MP4/MP3 download links via `yt-dlp`, returning multiple video qualities (or best audio for `mp3`).

### `youtube.playlist(url)`
Extract playlist metadata and items from a YouTube playlist URL.

---

## ⚠️ Disclaimer

This project is intended for internal R&D and educational use. Resolving full video/audio streams may rely on third-party conversion services or local `yt-dlp`, whose availability can change at any time. Respect each platform's Terms of Service. If you are the owner, operator, or authorized representative of any service supported here and wish for removal, please contact us.

> **Note:** Video/audio resolution works best when [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) is installed on the system. Thumbnail download is fully self-contained (direct from YouTube CDN) and needs no external tool.

---

## 📄 License

MIT — (c) 2026 Ridikc
