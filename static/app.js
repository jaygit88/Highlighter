"use strict";

const $ = (id) => document.getElementById(id);
const KIND_LABELS = { goal: "⚽ Goal", save: "🧤 Save", miss: "😬 Close miss", celebration: "🎉 Celebration" };
const KIND_KEYS = { "1": "goal", "2": "save", "3": "miss", "4": "celebration" };

const state = {
  videos: [],
  current: null,      // video id
  video: null,        // current video meta
  clips: [],
  signal: null,
  selected: null,     // clip id
  stopAt: null,       // pause playback here when playing a single clip
};

const player = $("player");
const canvas = $("timeline");

// ------------------------------------------------------------ helpers

function fmt(t) {
  t = Math.max(0, t || 0);
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && data.login) { location.href = "/login"; throw new Error("Enter the PIN first"); }
  if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status, data });
  return data;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) n.append(c);
  return n;
}

// ------------------------------------------------------------ videos list

async function loadVideos() {
  const data = await api("/api/videos");
  state.videos = data.videos;
  $("folderPath").textContent = data.folder;
  renderVideoList();
  updateKeptSummary();
  if (!state.current && state.videos.length) return selectVideo(state.videos[0].id);
  if (state.current) {
    const meta = state.videos.find((v) => v.id === state.current);
    if (!meta) { state.current = null; showPane(); return; }
    const wasReady = state.video && state.video.status === "ready";
    if (!wasReady && meta.status === "ready") await selectVideo(meta.id, true);
    else if (meta.status !== "ready") { state.video = meta; showPane(); }
  } else {
    showPane();
  }
}

function renderVideoList() {
  const list = $("videoList");
  list.replaceChildren(...state.videos.map((v) => {
    const status = {
      new: ["Not analysed", ""], queued: ["Waiting…", ""], processing: [`${Math.round((v.progress || 0) * 100)}%`, ""],
      ready: [`${v.kept_count}/${v.clip_count} kept`, "ready"], error: ["Problem", "error"],
    }[v.status] || [v.status, ""];
    return el("li", { class: v.id === state.current ? "active" : "", onclick: () => selectVideo(v.id), title: v.filename },
      el("span", { class: "name" }, v.filename),
      el("span", { class: "meta" },
        el("span", {}, v.duration ? fmt(v.duration) : ""),
        el("span", { class: `badge ${status[1]}` }, status[0])),
      v.status === "processing" || v.status === "queued"
        ? el("div", { class: "mini-bar" }, el("div", { style: `width:${(v.progress || 0) * 100}%` }))
        : null,
    );
  }));
}

function updateKeptSummary() {
  const kept = state.videos.reduce((n, v) => n + (v.status === "ready" ? v.kept_count : 0), 0);
  $("keptSummary").textContent = kept ? `${kept} moment${kept === 1 ? "" : "s"} kept` : "";
  $("exportBtn").disabled = kept === 0;
}

async function selectVideo(id, force = false) {
  if (state.current === id && !force) return;
  const changed = state.current !== id;
  state.current = id;
  const data = await api(`/api/videos/${id}`);
  state.video = data.video;
  state.clips = data.clips;
  state.signal = data.signal;
  if (changed) state.selected = null;
  renderVideoList();
  showPane();
  if (data.video.status === "ready") {
    const src = `/api/videos/${id}/proxy`;
    if (!player.src.endsWith(src)) { player.src = src; }
    $("sensitivity").value = data.video.sensitivity ?? 0.5;
    renderClips();
    drawTimeline();
  } else if (data.video.status === "new") {
    await api(`/api/videos/${id}/analyze`, { method: "POST" });
    loadVideos();
  }
}

function showPane() {
  const v = state.videos.length ? state.video : null;
  $("empty").hidden = !!v;
  $("processing").hidden = !v || v.status === "ready";
  $("review").hidden = !v || v.status !== "ready";
  if (v && v.status !== "ready") {
    player.pause();
    $("procTitle").textContent = v.filename;
    const stage = { new: "Starting…", queued: "Waiting for other videos to finish…", error: "Something went wrong" }[v.status];
    $("procStage").textContent = v.stage || stage || "";
    $("procBar").style.width = `${(v.progress || 0) * 100}%`;
    $("procError").hidden = v.status !== "error";
    $("procError").textContent = v.error || "";
    $("retryBtn").hidden = v.status !== "error";
  }
}

// ------------------------------------------------------------ clips

let saveTimer = null;
function saveClips() {
  clearTimeout(saveTimer);
  const id = state.current;
  const clips = state.clips.map((c) => ({ ...c }));
  saveTimer = setTimeout(async () => {
    try {
      await api(`/api/videos/${id}/clips`, { method: "PUT", body: JSON.stringify({ clips }) });
      const v = state.videos.find((x) => x.id === id);
      if (v) {
        v.clip_count = clips.length;
        v.kept_count = clips.filter((c) => c.keep).length;
        renderVideoList();
        updateKeptSummary();
      }
    } catch (e) { alert(`Couldn't save: ${e.message}`); }
  }, 300);
}

function changed(clip) {
  clip.source = "manual"; // edited by hand, so re-detecting won't replace it
  state.clips.sort((a, b) => a.start - b.start);
  renderClips();
  drawTimeline();
  saveClips();
}

function stars(score) {
  const n = score >= 6 ? 3 : score >= 3.5 ? 2 : 1;
  return "★".repeat(n) + "☆".repeat(3 - n);
}

function renderClips() {
  const list = $("clipList");
  const kept = state.clips.filter((c) => c.keep).length;
  $("clipCount").textContent = `(${kept} of ${state.clips.length} kept)`;
  if (!state.clips.length) {
    list.replaceChildren(el("li", { class: "muted" },
      "No moments found. Slide towards “more”, or play the video and press “+ Add clip here”."));
    return;
  }
  list.replaceChildren(...state.clips.map((c, i) => {
    const dur = c.end - c.start;
    const nudge = (field, delta) => () => {
      c[field] = Math.max(0, Math.min(state.video.duration, c[field] + delta));
      if (c.end - c.start < 1) c[field] -= delta;
      changed(c);
    };
    return el("li", {
        class: `clip ${c.keep ? "" : "dropped"} ${c.id === state.selected ? "selected" : ""}`,
        "data-id": c.id,
      },
      el("span", { class: "num" }, i + 1),
      el("div", {},
        el("div", { class: "when" },
          `${fmt(c.start)} – ${fmt(c.end)} `,
          el("span", { class: "muted" }, `(${Math.round(dur)}s)`), " ",
          c.score > 0 ? el("span", { class: "stars", title: "How lively this moment looked" }, stars(c.score)) : null),
        el("div", { class: "kinds" }, Object.entries(KIND_LABELS).map(([k, label]) =>
          el("button", {
            class: `kind ${c.kind === k ? "on" : ""}`, "data-kind": k, type: "button",
            onclick: () => { c.kind = c.kind === k ? "highlight" : k; if (c.kind !== "highlight") c.keep = true; changed(c); },
          }, label))),
      ),
      el("div", { class: "actions" },
        el("div", { class: "row" },
          el("button", { type: "button", onclick: () => playClip(c), title: "Play this clip" }, "▶ Play"),
          el("button", {
            type: "button", class: `keep-toggle ${c.keep ? "on" : ""}`,
            onclick: () => { c.keep = !c.keep; changed(c); },
          }, c.keep ? "✓ Keep" : "Leave out"),
          el("button", { type: "button", class: "del", title: "Delete", onclick: () => {
            state.clips = state.clips.filter((x) => x !== c); changed(c);
          } }, "✕")),
        el("div", { class: "row" },
          el("span", { class: "lbl" }, "Start"),
          el("button", { type: "button", onclick: nudge("start", -2), title: "Start 2s earlier" }, "−2s"),
          el("button", { type: "button", onclick: nudge("start", 2), title: "Start 2s later" }, "+2s"),
          el("button", { type: "button", title: "Start at the current video position", onclick: () => {
            if (player.currentTime < c.end - 1) { c.start = player.currentTime; changed(c); }
          } }, "⇤ here"),
          el("span", { class: "lbl" }, "End"),
          el("button", { type: "button", onclick: nudge("end", -2), title: "End 2s earlier" }, "−2s"),
          el("button", { type: "button", onclick: nudge("end", 2), title: "End 2s later" }, "+2s"),
          el("button", { type: "button", title: "End at the current video position", onclick: () => {
            if (player.currentTime > c.start + 1) { c.end = player.currentTime; changed(c); }
          } }, "here ⇥")),
      ),
    );
  }));
}

function selectClip(c, scroll = true) {
  state.selected = c ? c.id : null;
  document.querySelectorAll(".clip").forEach((n) => n.classList.toggle("selected", n.dataset.id === state.selected));
  if (c && scroll) document.querySelector(`.clip[data-id="${c.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  drawTimeline();
}

function playClip(c) {
  selectClip(c, false);
  player.currentTime = c.start;
  state.stopAt = c.end;
  player.play();
}

function selectedClip() {
  return state.clips.find((c) => c.id === state.selected) || null;
}

function stepClip(dir) {
  if (!state.clips.length) return;
  const cur = selectedClip();
  let idx;
  if (cur) idx = state.clips.indexOf(cur) + dir;
  else {
    const t = player.currentTime;
    idx = dir > 0 ? state.clips.findIndex((c) => c.start > t) : state.clips.findLastIndex((c) => c.start < t);
  }
  idx = Math.max(0, Math.min(state.clips.length - 1, idx < 0 ? 0 : idx));
  const c = state.clips[idx];
  selectClip(c);
  playClip(c);
}

// ------------------------------------------------------------ timeline

function drawTimeline() {
  if (!state.video || state.video.status !== "ready") return;
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = 84;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const dur = state.video.duration || 1;
  const x = (t) => (t / dur) * w;

  for (const c of state.clips) {
    ctx.fillStyle = cssVar(c.keep ? "--kept" : "--dropped");
    ctx.fillRect(x(c.start), 0, Math.max(2, x(c.end) - x(c.start)), h);
    if (c.id === state.selected) {
      ctx.strokeStyle = cssVar("--kept-edge"); ctx.lineWidth = 2;
      ctx.strokeRect(x(c.start) + 1, 1, Math.max(2, x(c.end) - x(c.start)) - 2, h - 2);
    }
  }

  const s = state.signal?.score;
  if (s && s.length) {
    const max = Math.max(6, ...s);
    const pad = 6;
    ctx.beginPath();
    const step = Math.max(1, Math.floor(s.length / w));
    for (let i = 0; i < s.length; i += step) {
      let v = s[i];
      for (let j = i; j < i + step && j < s.length; j++) v = Math.max(v, s[j]);
      const px = (i / s.length) * w;
      const py = h - pad - (Math.max(0, v) / max) * (h - 2 * pad);
      i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.strokeStyle = cssVar("--curve"); ctx.lineWidth = 1.5; ctx.stroke();
  }

  // minute ticks
  ctx.fillStyle = cssVar("--muted"); ctx.font = "10px system-ui";
  const every = dur > 1800 ? 600 : dur > 600 ? 300 : 60;
  for (let t = every; t < dur; t += every) {
    ctx.fillRect(x(t), h - 4, 1, 4);
    ctx.fillText(fmt(t), x(t) + 3, h - 3);
  }

  ctx.fillStyle = cssVar("--text");
  ctx.fillRect(x(player.currentTime) - 1, 0, 2, h);
}

canvas.addEventListener("click", (e) => {
  const r = canvas.getBoundingClientRect();
  const t = ((e.clientX - r.left) / r.width) * (state.video?.duration || 0);
  const hit = state.clips.find((c) => t >= c.start && t <= c.end);
  state.stopAt = null;
  if (hit) selectClip(hit); else selectClip(null);
  player.currentTime = t;
});
window.addEventListener("resize", drawTimeline);

player.addEventListener("timeupdate", () => {
  if (state.stopAt != null && player.currentTime >= state.stopAt) {
    player.pause();
    state.stopAt = null;
  }
  drawTimeline();
});
player.addEventListener("seeking", drawTimeline);

// ------------------------------------------------------------ toolbar

let detectTimer = null;
$("sensitivity").addEventListener("input", () => {
  clearTimeout(detectTimer);
  detectTimer = setTimeout(async () => {
    const data = await api(`/api/videos/${state.current}/detect`, {
      method: "POST", body: JSON.stringify({ sensitivity: parseFloat($("sensitivity").value) }),
    });
    state.clips = data.clips;
    renderClips(); drawTimeline();
    const v = state.videos.find((x) => x.id === state.current);
    if (v) { v.clip_count = data.clips.length; v.kept_count = data.clips.filter((c) => c.keep).length; renderVideoList(); updateKeptSummary(); }
  }, 250);
});

function addClipHere() {
  const t = player.currentTime;
  const c = {
    id: Math.random().toString(16).slice(2, 10),
    start: Math.max(0, t - 7), end: Math.min(state.video.duration, t + 7), peak: t,
    score: 0, kind: "highlight", keep: true, source: "manual",
  };
  state.clips.push(c);
  changed(c);
  selectClip(c);
}
$("addClipBtn").addEventListener("click", addClipHere);
$("keepAllBtn").addEventListener("click", () => { state.clips.forEach((c) => (c.keep = true)); renderClips(); drawTimeline(); saveClips(); });
$("dropAllBtn").addEventListener("click", () => { state.clips.forEach((c) => (c.keep = false)); renderClips(); drawTimeline(); saveClips(); });
$("retryBtn").addEventListener("click", async () => { await api(`/api/videos/${state.current}/analyze`, { method: "POST" }); loadVideos(); });
$("rescanBtn").addEventListener("click", async () => { await api("/api/analyze-all", { method: "POST" }); loadVideos(); });

document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, select, textarea, dialog") || e.metaKey || e.ctrlKey || e.altKey) return;
  if ($("review").hidden) return;
  const c = selectedClip();
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); player.paused ? player.play() : player.pause(); }
  else if (k === "n") stepClip(1);
  else if (k === "p") stepClip(-1);
  else if (k === "a") addClipHere();
  else if (k === "arrowleft") { player.currentTime -= 5; }
  else if (k === "arrowright") { player.currentTime += 5; }
  else if (c && k === "k") { c.keep = !c.keep; changed(c); }
  else if (c && KIND_KEYS[k]) { c.kind = c.kind === KIND_KEYS[k] ? "highlight" : KIND_KEYS[k]; c.keep = true; changed(c); }
  else if (c && (k === "delete" || k === "backspace")) { state.clips = state.clips.filter((x) => x !== c); state.selected = null; changed(c); }
  else return;
  if (c && ["k", "1", "2", "3", "4"].includes(k)) selectClip(c, false);
});

// ------------------------------------------------------------ uploads

const CHUNK = 8 * 1024 * 1024;
let uploading = false;

function gb(bytes) {
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

function putChunk(uid, offset, blob, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/upload/${uid}?offset=${offset}`);
    xhr.upload.onprogress = (e) => onProgress(e.loaded);
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      if (xhr.status === 200 || xhr.status === 409) resolve(body.offset);
      else if (xhr.status === 401) { location.href = "/login"; reject(new Error("login")); }
      else reject(new Error(`Server said ${xhr.status}`));
    };
    xhr.onerror = () => reject(new Error("Connection lost"));
    xhr.timeout = 120000;
    xhr.ontimeout = () => reject(new Error("Timed out"));
    xhr.send(blob);
  });
}

// Sends each video in 8 MB pieces. If the connection drops it retries, and if
// the page is reloaded, picking the same video again resumes where it stopped.
async function uploadFiles(files) {
  files = [...files].filter((f) => f.type.startsWith("video/") || /\.(mp4|mov|m4v|mkv|avi|mts|lrv)$/i.test(f.name));
  if (!files.length || uploading) return;
  uploading = true;
  const status = $("uploadStatus");
  status.hidden = false;
  const show = (text, frac) => { $("uploadText").textContent = text; $("uploadBar").style.width = `${frac * 100}%`; };
  let lastVideo = null;
  try {
    for (const [i, file] of files.entries()) {
      const label = files.length > 1 ? `Sending video ${i + 1} of ${files.length}` : "Sending video";
      const start = await api("/api/upload/start", { method: "POST", body: JSON.stringify({ name: file.name, size: file.size }) });
      if (start.exists) { lastVideo = start.video; continue; }
      let offset = start.offset;
      let failures = 0;
      while (offset < file.size) {
        const end = Math.min(file.size, offset + CHUNK);
        show(`${label}: ${Math.floor((offset / file.size) * 100)}% (${gb(offset)} of ${gb(file.size)})`, offset / file.size);
        try {
          offset = await putChunk(start.id, offset, file.slice(offset, end), (sent) => {
            const now = offset + sent;
            show(`${label}: ${Math.floor((now / file.size) * 100)}% (${gb(now)} of ${gb(file.size)})`, now / file.size);
          });
          failures = 0;
        } catch (err) {
          if (err.message === "login") return;
          failures += 1;
          if (failures > 8) throw new Error(`Lost connection to the computer while sending ${file.name}`);
          show(`${label}: connection dropped, retrying…`, offset / file.size);
          await new Promise((r) => setTimeout(r, Math.min(15000, 1000 * 2 ** failures)));
          try {
            offset = (await api("/api/upload/start", { method: "POST", body: JSON.stringify({ name: file.name, size: file.size }) })).offset;
          } catch { /* still offline; try again next loop */ }
        }
      }
      const done = await api(`/api/upload/${start.id}/finish`, { method: "POST" });
      lastVideo = done.video;
    }
    status.hidden = true;
  } catch (err) {
    show(`Problem: ${err.message}. Choose the same video again to carry on.`, 0);
  } finally {
    uploading = false;
  }
  await loadVideos();
  if (lastVideo) selectVideo(lastVideo);
}

// ------------------------------------------------------------ phone access

async function setupPhoneButton() {
  let info;
  try { info = await api("/api/phone"); } catch { return; } // we're on the phone
  document.querySelectorAll(".computer-only").forEach((n) => (n.hidden = false));
  if (info.local_only) return;
  $("phoneBtn").hidden = false;
  $("phoneBtn").addEventListener("click", () => {
    $("phoneOk").hidden = !info.url;
    $("phoneNone").hidden = !!info.url;
    $("qr").innerHTML = info.qr || "";
    $("phoneUrl").textContent = info.url || "";
    $("phonePin").textContent = info.pin;
    $("phoneDialog").showModal();
  });
}
setupPhoneButton();
$("fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
$("fileInput2").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
document.addEventListener("dragover", (e) => { e.preventDefault(); document.body.classList.add("dragging"); });
document.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
document.addEventListener("drop", (e) => {
  e.preventDefault(); document.body.classList.remove("dragging");
  uploadFiles(e.dataTransfer.files);
});

// ------------------------------------------------------------ export

const dialog = $("exportDialog");
$("exportBtn").addEventListener("click", () => {
  $("exportSetup").hidden = false; $("exportRun").hidden = true; $("exError").hidden = true;
  if (!$("exTitle").value && state.video) $("exTitle").value = state.video.filename.replace(/\.[^.]+$/, "");
  updateExportSummary();
  dialog.showModal();
});
dialog.addEventListener("change", updateExportSummary);

function exportScope() {
  return document.querySelector('input[name="scope"]:checked').value;
}
function updateExportSummary() {
  const vids = exportScope() === "all" ? state.videos.filter((v) => v.status === "ready") : state.videos.filter((v) => v.id === state.current);
  const n = vids.reduce((a, v) => a + v.kept_count, 0);
  const secs = vids.flatMap((v) => (v.id === state.current ? state.clips : [])).filter((c) => c.keep)
    .reduce((a, c) => a + (c.end - c.start), 0);
  $("exSummary").textContent = `${n} moment${n === 1 ? "" : "s"}` +
    (exportScope() === "current" ? `, about ${fmt(secs)} long before replays.` : ".");
  $("exGo").disabled = n === 0;
}

$("exGo").addEventListener("click", async (e) => {
  e.preventDefault();
  clearTimeout(saveTimer);
  await api(`/api/videos/${state.current}/clips`, { method: "PUT", body: JSON.stringify({ clips: state.clips }) });
  const body = {
    title: $("exTitle").value.trim(),
    videos: exportScope() === "all" ? null : [state.current],
    quality: $("exQuality").value,
    fades: $("exFades").checked,
    replays: $("exReplays").checked,
  };
  let job;
  try { job = await api("/api/export", { method: "POST", body: JSON.stringify(body) }); }
  catch (err) { $("exError").textContent = err.message; $("exError").hidden = false; return; }
  $("exportSetup").hidden = true; $("exportRun").hidden = false; $("exDone").hidden = true;
  player.pause();
  const poll = async () => {
    const s = await api(`/api/export/${job.id}`);
    $("exMsg").textContent = s.status === "error" ? `Problem: ${s.message}` : s.message;
    $("exBar").style.width = `${s.progress * 100}%`;
    if (s.status === "running") return setTimeout(poll, 800);
    if (s.status === "done") {
      const url = `/exports/${encodeURIComponent(s.file)}`;
      $("exMsg").textContent = `Your reel is ready: ${s.file}`;
      $("exVideo").src = url;
      $("exDownload").href = `${url}?dl=1`;
      $("exDownload").textContent = matchMedia("(pointer: coarse)").matches ? "Save to phone" : "Download";
      $("exDone").hidden = false;
    }
  };
  poll();
});
dialog.addEventListener("close", () => $("exVideo").pause());

// ------------------------------------------------------------ polling

async function tick() {
  try {
    const busy = state.videos.some((v) => v.status === "processing" || v.status === "queued");
    if (busy || !state.videos.length) await loadVideos();
  } catch (e) { console.warn(e); }
  setTimeout(tick, 1500);
}

loadVideos().then(tick);
