(function () {
  "use strict";

  var $ = function (s) { return document.querySelector(s); };
  var pollTimer = null;
  var busy = false;
  var lastLogLen = 0;

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
  }

  async function loadHistory() {
    var rows = await get("/api/history");
    var tbody = $("#historyRows");
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="6">Belum ada riwayat.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (r) {
      var ok = r.status === "done";
      return "<tr>" +
        "<td>" + esc(r.timestamp) + "</td>" +
        "<td>" + esc(r.style) + "</td>" +
        "<td>" + esc(r.clips) + "</td>" +
        "<td>" + esc(r.range) + "</td>" +
        '<td class="' + (ok ? "ok" : "fail") + '">' + (ok ? "OK" : "GAGAL") + "</td>" +
        "<td>" + esc(r.title) + "</td>" +
        "</tr>";
    }).join("");
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

    $("#quitBtn").addEventListener("click", async function () {
      if (!confirm("Matikan server clipper?")) return;
      try { await post("/api/quit", {}); } catch (e) { /* ignore */ }
      log("[*] Server dimatikan. Tab ini bisa ditutup.");
      document.body.innerHTML =
        '<div style="padding:60px;text-align:center;font-family:system-ui">' +
        "<h2>Server dimatikan</h2><p>Jendela ini bisa ditutup dengan aman.</p></div>";
    });

    try {
      var saved = localStorage.getItem("vclip-theme");
      if (saved) document.documentElement.dataset.theme = saved;
    } catch (e) { /* ignore */ }

    loadHistory();
  });
})();
