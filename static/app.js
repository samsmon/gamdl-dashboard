"use strict";
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) e.append(c instanceof Node ? c : String(c ?? ""));
  return e;
}
async function api(path, opt = {}) {
  const r = await fetch("/api/" + path, { headers: { "Content-Type": "application/json" }, ...opt, body: opt.body === undefined ? undefined : JSON.stringify(opt.body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}
const fmtBytes = n => n == null ? "?" : n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB";
const STATUS = { downloading: "▶ downloading", waiting: "◷ waiting", queued: "○ queued", done: "✓ done", error: "✕ error", cancelled: "– cancelled" };
const LIB = {
  in_library_lossless: "in library (lossless)", in_library_lossy: "in library (lossy)", similar: "similar (needs confirmation)",
  in_staging: "in staging", new: "new", unknown: "library data unavailable", unavailable: "library data unavailable",
};

let S = null, offset = 0, tab = "done", logFilter = "all", preview = [];
const expanded = new Set();   // item ids whose track list is open; everything starts collapsed
const trackCache = {};        // item id -> track rows

// single-track downloads (song urls, ?i= links, one-track albums) never get an expand toggle
const isAlbum = it => !(it.kind === "song" || it.track_id || it.track_n === 1 || it.expected_tracks === 1);

function bar(pct, wait) {
  const i = h("i"); i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  return h("div", { class: "bar" + (wait ? " wait" : ""), role: "progressbar", "aria-valuenow": Math.round(pct || 0) }, i);
}
function albumPct(it) {
  if (it.status === "done") return 100;
  if (!it.track_n) return 0;
  const done = it.status === "waiting" ? it.track_i : (it.track_i || 1) - 1;
  const cur = it.status === "downloading" ? (it.live?.track_pct || 0) / 100 : 0;
  return ((done + cur) / it.track_n) * 100;
}
function nowSrv() { return Date.now() / 1000 + offset; }
const countdown = (label, until) => `${label} ${Math.max(0, Math.round(until - nowSrv()))} s`;

// ---- expandable album detail -------------------------------------------
async function loadTracks(id) {
  try { trackCache[id] = (await api("queue/" + id)).tracks; } catch { trackCache[id] = []; }
  renderQueue();
  if (tab === "done") renderPanel();
}
function toggle(id) {
  if (expanded.has(id)) expanded.delete(id);
  else { expanded.add(id); loadTracks(id); }
  renderQueue();
  if (tab === "done") renderPanel();
}
function toggleBtn(it) {
  if (!isAlbum(it)) return h("span", {});
  const open = expanded.has(it.id);
  return h("button", { class: "icon", "aria-expanded": String(open), "aria-controls": "det-" + it.id,
    "aria-label": (open ? "collapse" : "expand") + " tracks", onclick: e => { e.stopPropagation(); toggle(it.id); } }, open ? "−" : "+");
}
function trackTable(id) {
  const rows = trackCache[id];
  if (!rows) return h("p", { class: "dim" }, "loading...");
  if (!rows.length) return h("p", { class: "dim" }, "no tracks yet.");
  const cls = s => "st-" + (["error", "done", "downloading"].includes(s) ? s : "queued");
  return h("table", { class: "tracks" },
    h("thead", {}, h("tr", {}, ["#", "status", "title", "note"].map(x => h("th", {}, x)))),
    h("tbody", {}, rows.map(t => h("tr", {}, h("td", {}, t.idx), h("td", { class: cls(t.status) }, t.status),
      h("td", { class: "name" }, t.title), h("td", { class: "dim" }, t.reason || "")))));
}

// ---- header, banner, queue --------------------------------------------
function renderBar() {
  const el = $("#bar"); el.replaceChildren();
  const run = S.items.find(i => i.status === "downloading" || i.status === "waiting");
  const state = S.paused ? "PAUSED" : run ? "RUNNING" : "IDLE";
  el.append(h("strong", {}, "gamdl"), h("span", {}, state),
    h("button", { onclick: () => api(S.paused ? "resume" : "pause", { method: "POST" }) }, S.paused ? "resume" : "pause"),
    h("span", { class: "grow" }),
    h("span", {}, `disk ${fmtBytes(S.disk.free_bytes)} free`),
    h("span", {}, `queue ~${fmtBytes(S.forecast_bytes)}`),
    h("span", {}, S.cap.limit ? `today ${S.cap.used}/${S.cap.limit}` : `today ${S.cap.used}`),
    h("span", {}, S.cookies.exists ? `cookies ${S.cookies.expiry_days == null ? "no expiry" : S.cookies.expiry_days < 0 ? "expired" : "expire " + S.cookies.expiry_days + " d"}` : "cookies missing"),
    h("span", {}, `errors ${S.errors}`));
}
function renderBanner() {
  const b = $("#banner"), msgs = [];
  if (S.banner) msgs.push(`${S.banner.kind}: ${S.banner.reason}. Fix the cause, then press resume.`);
  if (S.cookies.expired) msgs.push("cookies expired: re-export cookies.txt.");
  else if (S.cookies.exists && S.cookies.expiry_days != null && S.cookies.expiry_days <= 7) msgs.push(`cookies expire in ${S.cookies.expiry_days} d: re-export soon.`);
  b.hidden = msgs.length === 0; b.textContent = msgs.join(" ");
}
function move(it, dir) {
  const ids = S.items.map(i => i.id), k = ids.indexOf(it.id), j = k + dir;
  if (j < 0 || j >= ids.length) return;
  [ids[k], ids[j]] = [ids[j], ids[k]];
  api("queue/reorder", { method: "POST", body: { ids } });
}
const act = (it, what) => e => { e.stopPropagation(); api(`queue/${it.id}/${what}`, { method: "POST" }); };
function renderQueue() {
  const body = $("#rows"); body.replaceChildren();
  $("#empty").hidden = S.items.length > 0;
  S.items.forEach((it, n) => {
    const live = it.live || {};
    // countdown cells carry data-until/data-label so the 1 s tick only edits text (rows are not rebuilt, focus is kept)
    let right = live.speed || "", cd = null;
    if (live.delay_until) cd = { until: live.delay_until, label: `${live.delay_kind} delay` };
    else if (it.status === "waiting") right = "waiting";
    else if (it.status === "queued" && it.attempts && it.not_before) cd = { until: it.not_before, label: `retry ${it.attempts}/${S.settings.track_retries} in` };
    if (cd) right = countdown(cd.label, cd.until);
    const btn = (label, what, title) => h("button", { class: "icon", title, onclick: act(it, what) }, label);
    const actions = h("td", {},
      it.status === "queued" ? [h("button", { class: "icon", title: "move up", onclick: e => { e.stopPropagation(); move(it, -1); } }, "↑"),
                                h("button", { class: "icon", title: "move down", onclick: e => { e.stopPropagation(); move(it, 1); } }, "↓")] : "",
      ["queued", "downloading", "waiting"].includes(it.status) ? btn("cancel", "cancel") : "",
      ["error", "cancelled"].includes(it.status) ? [btn("retry", "retry"), btn("retry orig", "retry_original", "retry with the original storefront")] : "",
      !["downloading", "waiting"].includes(it.status) ? btn("remove", "remove") : "");
    const tr = h("tr", { tabindex: 0,
      onkeydown: e => { if (e.key === "[") move(it, -1); if (e.key === "]") move(it, 1); if (e.key === "Enter" && isAlbum(it) && e.target === tr) toggle(it.id); } },
      h("td", {}, toggleBtn(it)),
      h("td", {}, n + 1),
      h("td", { class: "st-" + it.status }, STATUS[it.status] || it.status),
      h("td", { class: "name" }, it.title ? `${it.title}${it.artist ? " / " + it.artist : ""}` : it.url,
        it.error_msg ? h("div", { class: "dim" }, it.error_msg) : ""),
      h("td", {}, it.track_n ? `${it.track_i || 0}/${it.track_n}` : "-"),
      h("td", {}, bar(albumPct(it), it.status === "waiting")),
      h("td", {}, bar(it.status === "done" ? 100 : live.track_pct, it.status === "waiting")),
      h("td", cd ? { "data-until": cd.until, "data-label": cd.label } : {}, right), actions);
    body.append(tr);
    if (isAlbum(it) && expanded.has(it.id)) {
      body.append(h("tr", { id: "det-" + it.id, class: "detail" }, h("td", {}), h("td", { colspan: 8 }, trackTable(it.id))));
    }
  });
}

// ---- completed / settings ---------------------------------------------
async function renderPanel() {
  const p = $("#panel"); p.replaceChildren();
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
  if (tab === "done") {
    const rows = await api("history");
    if (!rows.length) return p.append(h("p", { class: "dim" }, "nothing completed yet."));
    for (const r of rows) {
      const rel = (r.output_path || "").split(/[\\/]_gamdl-incoming[\\/]/)[1] || "";
      const smb = "\\\\192.168.18.225\\homelab\\hdd-backup\\music\\_gamdl-incoming\\" + rel.replaceAll("/", "\\");
      const open = expanded.has(r.id);
      p.append(h("div", { class: "done-item" },
        h("div", { class: "row" }, toggleBtn(r), h("strong", {}, r.title || r.url), h("span", { class: "dim" }, fmtBytes(r.size_bytes)),
          r.classification ? h("span", {}, `${r.codec}: ${r.classification}`) : ""),
        h("div", { class: "row" }, h("code", {}, smb), h("button", { onclick: () => navigator.clipboard?.writeText(smb) }, "copy path")),
        isAlbum(r) && open ? h("div", { id: "det-" + r.id, class: "detail" }, trackTable(r.id)) : "",
        r.findings.length ? h("ul", { class: "plain" }, r.findings.map(f => h("li", {}, f))) : h("div", { class: "dim" }, "no findings")));
    }
  } else {
    const s = await api("settings");
    const inputs = {};
    const fields = [["track_delay", "track delay s (min-max)"], ["album_delay", "album delay s (min-max)"], ["error_threshold", "pause after N 429/403"],
      ["low_disk_gb", "low disk GB"], ["max_tracks_per_24h", "max tracks / 24 h (0 = off)"], ["storefront", "storefront (empty = keep url's own)"],
      ["preview_max_tracks", "warn above N tracks"], ["track_retries", "album retries after a track error"], ["retry_backoff", "retry backoff s (min-max)"],
      ["library_exact", "library: in-library score"], ["library_similar", "library: similar score"]];
    for (const [k, label] of fields) { inputs[k] = h("input", { value: s[k], id: "f-" + k }); p.append(h("div", { class: "field" }, h("label", { for: "f-" + k }, label), inputs[k])); }
    const auto = h("input", { type: "checkbox", id: "f-auto", checked: s.auto_resume_after_cap });
    p.append(h("div", { class: "field" }, h("label", { for: "f-auto" }, "auto resume after cap"), auto));
    const msg = h("span", {});
    const text = ["track_delay", "album_delay", "storefront", "retry_backoff"];
    p.append(h("div", { class: "row" }, h("button", { onclick: async () => {
      const body = { auto_resume_after_cap: auto.checked };
      for (const [k] of fields) body[k] = text.includes(k) ? inputs[k].value : Number(inputs[k].value);
      try { await api("settings", { method: "PUT", body }); msg.textContent = "saved"; } catch (e) { msg.textContent = e.message; }
    } }, "save"), msg));
  }
}
function renderAll() { if (!S) return; renderBar(); renderBanner(); renderQueue(); if (tab === "done") renderPanel(); }

async function refresh() {
  S = await api("state"); offset = S.now - Date.now() / 1000; renderAll();
  for (const id of expanded) if (S.items.some(i => i.id === id)) loadTracks(id);
}

// ---- log and live events ----------------------------------------------
let logLines = [];
function addLog(l) { logLines.push(l); if (logLines.length > 500) logLines.shift(); drawLog(); }
function drawLog() {
  const rank = { DEBUG: 0, INFO: 0, WARNING: 1, ERROR: 2, CRITICAL: 2 }, min = { all: 0, warn: 1, error: 2 }[logFilter];
  const el = $("#log"), stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  el.replaceChildren(...logLines.filter(l => (rank[l.level] ?? 0) >= min).map(l => h("div", { class: "lvl-" + l.level }, l.text)));
  if (stick) el.scrollTop = el.scrollHeight;
}
let pending = null, pendingTracks = new Set();
function scheduleRefresh() { if (!pending) pending = setTimeout(() => { pending = null; refresh(); }, 250); }
function scheduleTracks(id) {
  if (!expanded.has(id) || pendingTracks.has(id)) return;
  pendingTracks.add(id);
  setTimeout(() => { pendingTracks.delete(id); loadTracks(id); }, 250);
}
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = m => {
    const ev = JSON.parse(m.data);
    if (ev.type === "log") addLog(ev);
    else if (ev.type === "tracks") scheduleTracks(ev.item_id);
    else if (ev.type === "progress" && S) {
      const it = S.items.find(i => i.id === ev.item_id);
      if (it) { it.live = { track_pct: ev.pct, speed: ev.speed, delay_kind: ev.delay_kind, delay_until: ev.delay_until }; renderQueue(); }
    } else scheduleRefresh();
  };
  es.onerror = () => { es.close(); setTimeout(() => { refresh(); connect(); }, 2000); };
}

// ---- add flow: parse -> preview + library check -> queue ---------------
$("#btn-preview").onclick = async () => {
  const msg = $("#add-msg"), text = $("#urls").value;
  msg.textContent = "reading...";
  const { results } = await api("parse", { method: "POST", body: { text } });
  preview = results.map(r => ({ ...r, info: null, checked: false, force: false }));
  drawPreview(); msg.textContent = preview.length ? "" : "no urls found.";
  for (const row of preview) {
    if (!row.url) continue;
    try {
      row.info = await api("preview", { method: "POST", body: { url: row.url } });
      row.checked = row.info.library.default_checked && !row.duplicate;
    } catch (e) { row.info = { error: e.message }; row.checked = !row.duplicate; }
    drawPreview();
  }
};
function libCell(row) {
  const i = row.info;
  if (!row.url) return "";
  if (!i) return "checking...";
  if (i.error) return "preview failed: " + i.error;
  const l = i.library, parts = [];
  const head = h("div", { class: l.needs_force ? "flag" : "" },
    (LIB[l.status] || l.status) + (l.confidence ? ` (${Math.round(l.confidence * 100)}%)` : ""));
  if (l.album) parts.push(h("div", { class: "dim" }, `${l.album}${l.path ? " - " + l.path : ""}`));
  if (l.source === "catalog") parts.push(h("div", { class: "dim" }, "names only (metadata.csv unavailable)"));
  if (l.status === "in_library_lossy") parts.push(h("div", { class: "dim" }, "only a lossy copy exists; this download is AAC"));
  if (i.staging && i.staging.level !== "none") parts.push(h("div", { class: "dim" }, `staging: ${i.staging.paths[0]}`));
  if (i.large) parts.push(h("div", { class: "dim" }, "large: many tracks"));
  return [head, ...parts];
}
function drawPreview() {
  const t = $("#pv"), b = $("#pv tbody"); b.replaceChildren();
  t.hidden = $("#pv-actions").hidden = preview.length === 0;
  for (const r of preview) {
    const needs = !!r.info?.library?.needs_force;
    const cb = h("input", { type: "checkbox", checked: r.checked, disabled: !r.url || (needs && !r.force), "aria-label": "include",
      onchange: e => { r.checked = e.target.checked; } });
    const pv = r.info?.preview;
    const anyway = needs ? (r.force ? h("span", {}, "will download anyway")
      : h("button", { onclick: () => { r.force = true; r.checked = true; drawPreview(); } }, "download anyway")) : "";
    b.append(h("tr", {}, h("td", {}, cb),
      h("td", { class: "name" }, r.error ? `${r.raw}: ${r.error}` : pv?.title || r.url, r.duplicate ? h("div", { class: "dim" }, "already queued") : ""),
      h("td", {}, pv?.artist || ""), h("td", {}, pv?.tracks ?? ""), h("td", { class: "name" }, libCell(r)), h("td", {}, anyway)));
  }
}
$("#btn-queue").onclick = async () => {
  const items = preview.filter(r => r.checked && r.url).map(r => ({
    url: r.url, title: r.info?.preview?.title || null, artist: r.info?.preview?.artist || null,
    tracks: r.info?.preview?.tracks ?? null, force: !!r.force }));
  if (!items.length) return;
  const { results } = await api("queue", { method: "POST", body: { items } });
  const bad = results.filter(r => r.error);
  $("#add-msg").textContent = bad.length ? bad.map(r => r.error).join("; ") : `queued ${results.length}`;
  preview = []; drawPreview(); $("#urls").value = ""; refresh();
};
document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => { tab = b.dataset.tab; renderPanel(); });
$("#logfilter").onchange = e => { logFilter = e.target.value; drawLog(); };
$("#logcopy").onclick = () => navigator.clipboard?.writeText(logLines.map(l => l.text).join("\n"));
setInterval(() => document.querySelectorAll("[data-until]").forEach(e => { e.textContent = countdown(e.dataset.label, Number(e.dataset.until)); }), 1000);

(async () => {
  logLines = (await api("log?limit=200").catch(() => [])).map(l => ({ level: l.level, text: l.text }));
  drawLog(); await refresh(); connect();
})();
