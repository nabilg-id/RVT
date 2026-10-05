(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var pollTimer = null;
  var busy = false;
  var currentJobId = null;
  var lastLogLen = 0;
  var STATUS_LABELS = {};

  function log(msg) {
    var el = $("#log");
    el.textContent += msg + "\n";
    el.scrollTop = el.scrollHeight;
  }

  function esc(t) {
    return String(t == null ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  async function post(url, body) {
    var res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
    return { status: res.status, data: await res.json() };
  }

  async function get(url) {
    return (await fetch(url)).json();
  }

  function setProgress(pct, phase) {
    var c = Math.max(0, Math.min(100, Math.round(pct * 100)));
    $("#bar").style.width = c + "%";
    $("#percent").textContent = c + "%";
    if (phase) $("#phase").textContent = phase;
  }

  function setBusy(state) {
    busy = state;
    $("#generateBtn").disabled = state;
    $("#previewBtn").disabled = state;
    // The cancel button only makes sense while a job is running. Uses the
    // hidden attribute, matching the preview/results panels in the markup.
    $("#cancelBtn").hidden = !state;
    if (!state) currentJobId = null;
  }

  function currentPayload() {
    return {
      url: $("#url").value.trim(),
      numClips: parseInt($("#numClips").value, 10),
      minDur: parseInt($("#minDur").value, 10),
      maxDur: parseInt($("#maxDur").value, 10),
      style: $("#style").value
    };
  }

  async function doPreview() {
    var url = $("#url").value.trim();
    if (!url) { log("[!] URL wajib diisi."); return; }
    var r = await post("/api/preview", { url: url });
    if (r.data.error) { log("[!] " + r.data.error); return; }

    var box = $("#preview");
    if (r.data.thumbnail) { $("#previewThumb").src = r.data.thumbnail; }
    $("#previewTitle").textContent = r.data.title || r.data.id || "-";

    var meta = [];
    if (r.data.duration) meta.push(Math.round(r.data.duration) + " detik");
    if (r.data.uploadDate) meta.push("Upload " + r.data.uploadDate);
    if (r.data.warning) meta.push("Peringatan: " + r.data.warning);
    $("#previewMeta").textContent = meta.join(" · ");
    box.hidden = false;
  }

  function renderResults(outputs) {
    if (!outputs || !outputs.length) return;
    var list = $("#resultList");
    list.innerHTML = outputs.map(function (name) {
      return '<li><a href="/clips/' + encodeURIComponent(name) +
             '" target="_blank" download>' + esc(name) + "</a></li>";
    }).join("");
    $("#results").hidden = false;
  }

  async function poll(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async function () {
      var job;
      try {
        job = await get("/api/status/" + jobId);
      } catch (e) { return; }

      setProgress(job.progress || 0, job.phase);
      if (job.logTruncated) log("[log dipotong agar tidak memblender UI]");

      if (job.error) {
        log("[!!] " + job.error);
        finish();
      } else if (job.status === "cancelled") {
        log("[x] Dibatalkan.");
        finish();
      } else if (job.status === "done") {
        log("[OK] Selesai. " + (job.title ? '"' + job.title + '"' : ""));
        renderResults(job.outputs);
        finish();
      }
    }, 900);
  }

  function finish() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    setBusy(false);
    loadHistory();
    loadBoard();
  }

  async function loadHistory() {
    var rows = await get("/api/history");
    var tbody = $("#historyRows");
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="7">Belum ada riwayat.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (r) {
      var ok = r.status === "done";
      var video = r.videoId
        ? '<a href="https://youtu.be/' + encodeURIComponent(r.videoId) +
          '" target="_blank" rel="noopener" class="vid-link">' +
          esc(r.videoId) + "</a>"
        : '<span class="muted">-</span>';
      return "<tr>" +
        "<td>" + esc(r.timestamp) + "</td>" +
        "<td>" + esc(r.style) + "</td>" +
        "<td>" + esc(r.clips) + "</td>" +
        "<td>" + esc(r.range) + "</td>" +
        '<td class="' + (ok ? "ok" : "fail") + '">' + (ok ? "OK" : "GAGAL") + "</td>" +
        "<td>" + esc(r.title) + "</td>" +
        "<td>" + video + "</td>" +
        "</tr>";
    }).join("");
  }

  // -- Papan Video ---------------------------------------------------------

  function statusChip(status) {
    return '<span class="chip s-' + esc(status) + '">' +
           esc(STATUS_LABELS[status] || status) + "</span>";
  }

  function buildFilterOptions(counts) {
    var sel = $("#statusFilter");
    var current = sel.value;
    var labels = STATUS_LABELS;
    var opts = ['<option value="all">Semua status (' +
                ((counts.total || 0)) + ")</option>"];
    Object.keys(counts).forEach(function (key) {
      if (key === "total") return;
      opts.push('<option value="' + esc(key) + '">' +
                esc(labels[key] || key) + " (" + counts[key] + ")</option>");
    });
    sel.innerHTML = opts.join("");
    sel.value = current && sel.querySelector('option[value="' + current + '"]')
      ? current : "all";
  }

  function renderCounts(counts) {
    var order = ["clipped", "processing", "queued", "downloaded", "none",
                 "download_failed", "clip_failed"];
    $("#boardCounts").innerHTML = order
      .filter(function (k) { return counts[k]; })
      .map(function (k) {
        return '<span class="chip s-' + esc(k) + '">' +
               esc(STATUS_LABELS[k] || k) + " " + counts[k] + "</span>";
      })
      .join("") || '<span class="muted">Belum ada video tercatat.</span>';
  }

  function renderBoard(rows) {
    var tbody = $("#videoRows");
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="6">Tidak ada video yang cocok.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (v) {
      var clips = (v.clipFiles || []).length
        ? v.clipFiles.map(function (name) {
            return '<a href="/clips/' + encodeURIComponent(name) +
                   '" target="_blank" download>' + esc(name) + "</a>";
          }).join("<br>")
        : '<span class="muted">-</span>';

      var actions = '<button class="btn ghost small act-clip" data-id="' +
        esc(v.videoId) + '">Isi URL</button> ';
      if (v.status === "queued") {
        actions += '<button class="btn ghost small act-unqueue" data-id="' +
          esc(v.videoId) + '">Batalkan antre</button>';
      } else if (v.status !== "clipped" && v.status !== "processing") {
        actions += '<button class="btn ghost small act-queue" data-id="' +
          esc(v.videoId) + '">Antrekan Clip</button>';
      }

      return "<tr>" +
        "<td>" + statusChip(v.status) + "</td>" +
        "<td>" + esc(v.title || "(tanpa judul)") + "</td>" +
        '<td><a href="' + esc(v.url) + '" target="_blank" rel="noopener" class="vid-link">' +
          esc(v.videoId) + "</a></td>" +
        "<td>" + esc(v.downloadStatus) + "</td>" +
        "<td>" + clips + "</td>" +
        "<td>" + actions + "</td>" +
        "</tr>";
    }).join("");
  }

  async function loadBoard() {
    var params = [];
    var status = $("#statusFilter").value;
    var q = $("#videoSearch").value.trim();
    if (status && status !== "all") params.push("status=" + encodeURIComponent(status));
    if (q) params.push("q=" + encodeURIComponent(q));

    var data;
    try {
      data = await get("/api/videos" + (params.length ? "?" + params.join("&") : ""));
    } catch (e) {
      $("#videoRows").innerHTML =
        '<tr class="empty"><td colspan="6">Papan video gagal dimuat.</td></tr>';
      return;
    }
    if (data.error) {
      $("#videoRows").innerHTML =
        '<tr class="empty"><td colspan="6">' + esc(data.error) + "</td></tr>";
      return;
    }

    STATUS_LABELS = data.labels || STATUS_LABELS;
    buildFilterOptions(data.counts || {});
    renderCounts(data.counts || {});
    renderBoard(data.videos || []);
  }

  var searchTimer = null;
  function queueSearch() {
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(loadBoard, 250);
  }

  async function setQueue(videoId, queued) {
    var path = queued ? "/api/videos/queue" : "/api/videos/unqueue";
    var r = await post(path, { videoId: videoId });
    if (r.status >= 400) {
      log("[!] " + (r.data.error || "gagal mengubah antrean"));
    }
    await loadBoard();
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("#previewBtn").addEventListener("click", doPreview);

    $("#generateBtn").addEventListener("click", async function () {
      if (busy) return;
      setBusy(true);
      setProgress(0.02, "Menyiapkan");
      log("[*] Meminta clip…");
      try {
        var r = await post("/api/clip", currentPayload());
        if (r.data.error) {
          log("[!] " + r.data.error);
          setBusy(false);
          return;
        }
        currentJobId = r.data.jobId;
        await poll(r.data.jobId);
      } catch (e) {
        log("[!!] " + e.message);
        setBusy(false);
      }
    });

    $("#themeToggle").addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("vclip-theme", next); } catch (e) { /* ignore */ }
    });

    $("#refreshHistory").addEventListener("click", loadHistory);

    $("#refreshBoard").addEventListener("click", loadBoard);
    $("#statusFilter").addEventListener("change", loadBoard);
    $("#videoSearch").addEventListener("input", queueSearch);

    // Delegasi, karena baris papan dirender ulang setiap poll.
    $("#videoRows").addEventListener("click", function (ev) {
      var btn = ev.target.closest("button[data-id]");
      if (!btn) return;
      var id = btn.getAttribute("data-id");
      if (!id) return;
      if (btn.classList.contains("act-queue")) { setQueue(id, true); }
      else if (btn.classList.contains("act-unqueue")) { setQueue(id, false); }
      else if (btn.classList.contains("act-clip")) {
        $("#url").value = "https://www.youtube.com/watch?v=" + id;
        $("#url").focus();
        log("[*] URL diisi dari papan video. Tekan Generate Clip untuk lanjut.");
      }
    });

    $("#quitBtn").addEventListener("click", async function () {
      if (!confirm("Matikan server clipper?")) return;
      try { await post("/api/quit", {}); } catch (e) { /* ignore */ }
      log("[*] Server dimatikan. Tab ini bisa ditutup.");
      document.body.innerHTML =
        '<div style="padding:60px;text-align:center;font-family:system-ui">' +
        "<h2>Server dimatikan</h2><p>Jendela ini bisa ditutup dengan aman.</p></div>";
    });

    $("#cancelBtn").addEventListener("click", async function () {
      if (!currentJobId) return;
      log("[*] Membatalkan job…");
      try {
        await post("/api/cancel/" + currentJobId, {});
      } catch (e) {
        log("[!!] Gagal membatalkan: " + e.message);
      }
    });

    try {
      var saved = localStorage.getItem("vclip-theme");
      if (saved) document.documentElement.dataset.theme = saved;
    } catch (e) { /* ignore */ }

    loadHistory();
    loadBoard();
  });
})();
