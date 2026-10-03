(function () {
  "use strict";

  var $ = function (sel) { return document.querySelector(sel); };
  var pollTimer = null;
  var busy = false;

  function log(msg) {
    var el = $("#log");
    el.textContent += msg + "\n";
    el.scrollTop = el.scrollHeight;
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
    var res = await fetch(url);
    return res.json();
  }

  function currentOptions() {
    var limitEnabled = $("#limitEnabled").checked;
    return {
      url: $("#link").value.trim(),
      format: $("#format").value,
      quality: $("#quality").value,
      size: $("#size").value,
      outputDir: $("#out").value.trim() || "./downloads",
      shorts: $("#shorts").checked,
      limit: limitEnabled ? parseInt($("#limit").value, 10) || null : null
    };
  }

  function setProgress(pct, phase) {
    var clamped = Math.max(0, Math.min(100, Math.round(pct * 100)));
    $("#bar").style.width = clamped + "%";
    $("#percent").textContent = clamped + "%";
    if (phase) $("#phase").textContent = phase;
  }

  function setBusy(state) {
    busy = state;
    document.querySelectorAll(".actions .btn").forEach(function (b) {
      b.disabled = state;
    });
  }

  function renderRows(items) {
    var tbody = $("#rows");
    if (!items || !items.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="4">Belum ada job.</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(function (it) {
      var cls = it.ok ? "ok" : "fail";
      var label = it.ok ? "OK" : "GAGAL";
      return "<tr>" +
        '<td class="' + cls + '">' + label + "</td>" +
        '<td class="mono">' + esc(it.videoId || "-") + "</td>" +
        "<td>" + esc(it.title || "-") + "</td>" +
        "<td>" + esc(it.error || "") + "</td>" +
        "</tr>";
    }).join("");
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  async function poll(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async function () {
      var job;
      try {
        job = await get("/api/status/" + jobId);
      } catch (e) {
        return;
      }
      if (job.error) {
        log("[!] " + job.error);
      } else {
        setProgress(job.progress || 0, job.phase);
        renderRows(job.items);
        if (job.result && job.result.total != null) {
          $("#summary").textContent =
            "total " + job.result.total +
            " · sukses " + (job.result.success != null ? job.result.success : "-") +
            " · gagal " + (job.result.failed != null ? job.result.failed : "-");
        }
        if (job.status === "done") {
          log("[OK] Job selesai.");
          if (job.result && job.result.zipPath) log("ZIP: " + job.result.zipPath);
          finish();
        } else if (job.status === "error") {
          log("[!!] Job gagal: " + (job.error || "tidak diketahui"));
          finish();
        }
      }
    }, 700);
  }

  function finish() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    setBusy(false);
    loadHistory();
  }

  var STATUS_LABELS = {
    none: "Belum", downloaded: "Download", queued: "Antri",
    processing: "Proses", clipped: "Sudah Clip",
    download_failed: "Gagal Download", clip_failed: "Gagal Clip"
  };

  function clipSummary(record) {
    var items = record.items || [];
    if (!items.length) return '<span class="muted">-</span>';
    var counts = {};
    items.forEach(function (it) {
      var key = it.status || "none";
      counts[key] = (counts[key] || 0) + 1;
    });
    var parts = Object.keys(counts).sort(function (a, b) {
      return counts[b] - counts[a];
    }).map(function (k) {
      return '<span class="chip s-' + esc(k) + '">' +
             esc(STATUS_LABELS[k] || k) + " " + counts[k] + "</span>";
    });
    return parts.join(" ");
  }

  async function loadHistory() {
    var out = $("#out").value.trim() || "./downloads";
    var rows;
    try {
      // /api/runs, not /api/history. In the merged app /api/history is the
      // clipper's clip history, so the old path filled this run table with clip
      // rows and the harvest runs disappeared.
      rows = await get("/api/runs?out=" + encodeURIComponent(out));
    } catch (e) {
      return;
    }
    var tbody = $("#historyRows");
    if (!rows || !rows.length) {
      tbody.innerHTML = '<tr class="empty"><td colspan="7">Belum ada riwayat.</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(function (r) {
      return "<tr>" +
        "<td>" + esc(r.timestamp) + "</td>" +
        "<td>" + esc(r.command) + "</td>" +
        "<td>" + esc(r.channel) + "</td>" +
        "<td>" + esc(r.total) + "</td>" +
        "<td>" + esc(r.success) + "</td>" +
        "<td>" + esc(r.failed) + "</td>" +
        "<td>" + clipSummary(r) + "</td>" +
        "</tr>";
    }).join("");
  }

  var ACTIONS = {
    "info": async function (o) {
      var r = await post("/api/info", { url: o.url });
      if (r.data.status === false) { log("[!] " + (r.data.message || "gagal")); return; }
      log("[INFO] " + JSON.stringify(r.data, null, 2));
    },
    "download": async function (o) {
      log("[*] Mengunduh video...");
      var r = await post("/api/download", o);
      if (r.data.status === false) { log("[!] " + (r.data.message || "gagal")); return; }
      log("[OK] " + (r.data.result.path || "selesai"));
    },
    "playlist": async function (o) {
      var r = await post("/api/playlist", { url: o.url, limit: o.limit });
      if (r.data.status === false) {
        log("[!] " + (r.data.message || "gagal"));
        return;
      }
      var p = r.data.result || {};
      log("[PLAYLIST] " + (p.title || "-") + " · " + (p.author || "-") +
          " · " + ((p.itemCount != null ? p.itemCount : 0)) + " video");
      (p.items || []).forEach(function (it) {
        log("  " + (it.videoId || "-") + "  " + (it.title || "-") +
            "  " + (it.lengthText || ""));
      });
      renderRows((p.items || []).map(function (it) {
        return { ok: true, videoId: it.videoId, title: it.title, error: "" };
      }));
      $("#summary").textContent = "playlist " + (p.title || "-") +
        " · total " + ((p.itemCount != null ? p.itemCount : 0));
    },
    "channel-info": async function (o) { await startJob("/api/channel-info", o, "Info channel"); },
    "channel-video": async function (o) { await startJob("/api/channel-video", o, "Video channel"); },
    "channel-full": async function (o) { await startJob("/api/channel-full", o, "Channel lengkap"); }
  };

  async function startJob(url, o, label) {
    log("[*] " + label + " dimulai...");
    var r = await post(url, o);
    if (!r.data.jobId) { log("[!] Gagal membuat job."); return; }
    setProgress(0.02, label + " berjalan");
    await poll(r.data.jobId);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".actions .btn").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (busy) return;
        var o = currentOptions();
        if (!o.url) { log("[!] URL wajib diisi."); return; }
        setBusy(true);
        setProgress(0, "Menyiapkan...");
        try {
          await ACTIONS[btn.dataset.act](o);
        } catch (e) {
          log("[!!] " + e.message);
        } finally {
          if (!pollTimer) setBusy(false);
        }
      });
    });

    $("#limitEnabled").addEventListener("change", function (e) {
      $("#limit").disabled = !e.target.checked;
    });

    $("#themeToggle").addEventListener("click", function () {
      var root = document.documentElement;
      var next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("rch-theme", next); } catch (e) { /* ignore */ }
    });

    $("#refreshHistory").addEventListener("click", loadHistory);

    $("#quitBtn").addEventListener("click", async function () {
      if (!confirm("Matikan server RCH?")) return;
      try { await post("/api/quit", {}); } catch (e) { /* ignore */ }
      log("[*] Server dimatikan. Anda bisa menutup tab ini.");
      document.body.innerHTML =
        '<div style="padding:60px;text-align:center;font-family:system-ui">' +
        "<h2>Server RCH sudah dimatikan</h2>" +
        "<p>Jendela ini bisa ditutup dengan aman.</p></div>";
    });

    try {
      var saved = localStorage.getItem("rch-theme");
      if (saved) document.documentElement.dataset.theme = saved;
    } catch (e) { /* ignore */ }

    loadHistory();
  });
})();
