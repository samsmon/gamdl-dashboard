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
function fmtDetail(d, fallback) {
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(x => (x && x.msg) ? x.msg : JSON.stringify(x)).join("; ");
  if (d && typeof d === "object") return d.msg || JSON.stringify(d);
  return fallback;
}
async function api(path, opt = {}) {
  const r = await fetch("/api/" + path, { headers: { "Content-Type": "application/json" }, ...opt, body: opt.body === undefined ? undefined : JSON.stringify(opt.body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(fmtDetail(data.detail, r.statusText || "request failed"));
  return data;
}
let sayTimer = null;
function say(msg) {
  const el = $("#status-msg"); el.textContent = msg; el.hidden = !msg;
  clearTimeout(sayTimer); if (msg) sayTimer = setTimeout(() => say(""), 8000);
}
const fail = e => say("error: " + (e && e.message ? e.message : e));
// run fn (which rebuilds DOM) and put focus back on the control that had the same data-key
function keepFocus(fn) {
  const key = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null;
  fn();
  if (key) {
    // if the control is gone (e.g. cancel became remove), fall back to its row
    const id = key.split(":")[1], fall = id && /^(cancel|retry|retry_original|remove|up|down|toggle)$/.test(key.split(":")[0]) ? `row:${id}` : null;
    const el = document.querySelector(`[data-key="${CSS.escape(key)}"]`) || (fall && document.querySelector(`[data-key="${CSS.escape(fall)}"]`));
    if (el && el !== document.activeElement) el.focus({ preventScroll: true });
  }
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
const trackErr = new Set();   // ids whose last track load failed
let histRows = [];
async function loadTracks(id) {
  try { trackCache[id] = (await api("queue/" + id)).tracks; trackErr.delete(id); }
  catch { delete trackCache[id]; trackErr.add(id); }
  drawDetail(id);
}
// re-render only the detail(s) of one item, in the queue and on the completed tab
function drawDetail(id) {
  for (const sel of [`#det-q-${id} > td.dtd`, `#det-c-${id}`]) {
    const el = document.querySelector(sel);
    if (el) el.replaceChildren(trackTable(id));
  }
}
function toggle(id) {
  if (expanded.has(id)) expanded.delete(id);
  else { expanded.add(id); loadTracks(id); }
  keepFocus(() => { renderQueue(); if (tab === "done") drawDone(); });
}
function toggleBtn(it, view) {
  if (!isAlbum(it)) return h("span", {});
  const open = expanded.has(it.id);
  return h("button", { class: "icon", "data-key": (view === "c" ? "ctoggle:" : "toggle:") + it.id, "aria-expanded": String(open), "aria-controls": `det-${view}-${it.id}`,
    "aria-label": (open ? "collapse" : "expand") + " tracks", onclick: e => { e.stopPropagation(); toggle(it.id); } }, open ? "−" : "+");
}
function trackTable(id) {
  const rows = trackCache[id];
  if (trackErr.has(id)) return h("p", { class: "dim" }, "could not load tracks (collapse and expand to retry).");
  if (!rows) return h("p", { class: "dim" }, "loading...");
  if (!rows.length) return h("p", { class: "dim" }, "no tracks yet.");
  const cls = s => "st-" + (["error", "done", "downloading"].includes(s) ? s : "queued");
  return h("div", { class: "tablewrap" }, h("table", { class: "tracks" },
    h("thead", {}, h("tr", {}, ["#", "status", "title", "note"].map(x => h("th", {}, x)))),
    h("tbody", {}, rows.map(t => h("tr", {}, h("td", {}, t.idx), h("td", { class: cls(t.status) }, t.status),
      h("td", { class: "name" }, t.title), h("td", { class: "dim" }, t.reason || ""))))));
}

// ---- header, banner, queue --------------------------------------------
function renderBar() {
  keepFocus(() => {
    const el = $("#bar"); el.replaceChildren();
    const run = S.items.find(i => i.status === "downloading" || i.status === "waiting");
    const state = S.paused ? "PAUSED" : run ? "RUNNING" : "IDLE";
    el.append(h("strong", {}, "gamdl"), h("span", {}, state),
      h("button", { "data-key": "pause", onclick: () => api(S.paused ? "resume" : "pause", { method: "POST" }).catch(fail) }, S.paused ? "resume" : "pause"),
      h("span", { class: "grow" }),
      h("span", {}, `disk ${fmtBytes(S.disk.free_bytes)} free`),
      h("span", {}, `queue ~${fmtBytes(S.forecast_bytes)}`),
      h("span", {}, S.cap.limit ? `today ${S.cap.used}/${S.cap.limit}` : `today ${S.cap.used}`),
      h("span", {}, S.cookies.exists ? `cookies ${S.cookies.expiry_days == null ? "no expiry" : S.cookies.expiry_days < 0 ? "expired" : "expire " + S.cookies.expiry_days + " d"}` : "cookies missing"),
      h("span", {}, `errors ${S.errors}`));
  });
}
function renderBanner() {
  const b = $("#banner"), msgs = [];
  if (S.banner) msgs.push(`${S.banner.kind}: ${S.banner.reason}. Fix the cause, then press resume.`);
  if (S.cookies.expired) msgs.push("cookies expired: re-export cookies.txt.");
  else if (S.cookies.exists && S.cookies.expiry_days != null && S.cookies.expiry_days <= 7) msgs.push(`cookies expire in ${S.cookies.expiry_days} d: re-export soon.`);
  b.hidden = msgs.length === 0; b.textContent = msgs.join(" ");
}
function move(it, dir) {
  if (it.status !== "queued") return;
  const k = S.items.findIndex(i => i.id === it.id), j = k + dir;
  if (k < 0 || j < 0 || j >= S.items.length) return;
  [S.items[k], S.items[j]] = [S.items[j], S.items[k]];   // optimistic, so repeated key presses stack
  renderQueue();
  api("queue/reorder", { method: "POST", body: { ids: S.items.map(i => i.id) } }).catch(e => { fail(e); refresh().catch(fail); });
}
const act = (it, what) => e => { e.stopPropagation(); api(`queue/${it.id}/${what}`, { method: "POST" }).catch(fail); };

function rightInfo(it) {
  const live = it.live || {};
  let right = live.speed || "", cd = null;
  if (live.delay_until) cd = { until: live.delay_until, label: `${live.delay_kind} delay` };
  else if (it.status === "waiting") right = "waiting";
  else if (it.status === "queued" && it.attempts && it.not_before) cd = { until: it.not_before, label: `retry ${it.attempts}/${S.settings.track_retries} in` };
  if (cd) right = countdown(cd.label, cd.until);
  return { right, cd };
}
function setBar(el, pct) {
  const i = el.firstChild; i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  el.setAttribute("aria-valuenow", Math.round(pct || 0));
}
// progress events only touch bars and the speed/countdown cell; nothing is rebuilt, so focus is never lost
function updateLive() {
  for (const it of S.items) {
    const tr = document.querySelector(`#rows > tr[data-id="${it.id}"]`);
    if (!tr) continue;
    setBar(tr.querySelector(".abar > .bar"), albumPct(it));
    setBar(tr.querySelector(".tbar > .bar"), it.status === "done" ? 100 : (it.live || {}).track_pct);
    const { right, cd } = rightInfo(it), cell = tr.querySelector(".right");
    if (cd) { cell.dataset.until = cd.until; cell.dataset.label = cd.label; } else { delete cell.dataset.until; delete cell.dataset.label; }
    if (cell.textContent !== right) cell.textContent = right;
  }
}
let queueSig = null;
const sigOf = items => items.map(i => [i.id, i.status, i.track_i, i.track_n, i.error_msg, i.attempts, i.not_before, i.title, i.artist].join("|")).join("\n") + "#" + S.settings.track_retries;
function renderQueue() {
  const body = $("#rows"), frag = document.createDocumentFragment();
  queueSig = sigOf(S.items);
  $("#empty").hidden = S.items.length > 0;
  S.items.forEach((it, n) => {
    const { right, cd } = rightInfo(it);
    const btn = (label, what, title) => h("button", { class: "icon", "data-key": what + ":" + it.id, title, onclick: act(it, what) }, label);
    const actions = h("td", {},
      it.status === "queued" ? [h("button", { class: "icon", "data-key": "up:" + it.id, title: "move up", onclick: e => { e.stopPropagation(); move(it, -1); } }, "↑"),
                                h("button", { class: "icon", "data-key": "down:" + it.id, title: "move down", onclick: e => { e.stopPropagation(); move(it, 1); } }, "↓")] : "",
      ["queued", "downloading", "waiting"].includes(it.status) ? btn("cancel", "cancel") : "",
      ["error", "cancelled"].includes(it.status) ? [btn("retry", "retry"), btn("retry orig", "retry_original", "retry with the original storefront")] : "",
      !["downloading", "waiting"].includes(it.status) ? btn("remove", "remove") : "");
    const tr = h("tr", { tabindex: 0, "data-id": it.id, "data-key": "row:" + it.id,
      onkeydown: e => {
        if (e.target !== tr) return;
        if (e.key === "[") move(it, -1);
        if (e.key === "]") move(it, 1);
        if (e.key === "Enter" && isAlbum(it)) toggle(it.id);
      } },
      h("td", {}, toggleBtn(it, "q")),
      h("td", {}, n + 1),
      h("td", { class: "st-" + it.status }, STATUS[it.status] || it.status),
      h("td", { class: "name" }, it.title ? `${it.title}${it.artist ? " / " + it.artist : ""}` : it.url,
        it.error_msg ? h("div", { class: "dim" }, it.error_msg) : ""),
      h("td", {}, it.track_n ? `${it.track_i || 0}/${it.track_n}` : "-"),
      h("td", { class: "abar" }, bar(albumPct(it), it.status === "waiting")),
      h("td", { class: "tbar" }, bar(it.status === "done" ? 100 : (it.live || {}).track_pct, it.status === "waiting")),
      h("td", { class: "right", ...(cd ? { "data-until": cd.until, "data-label": cd.label } : {}) }, right), actions);
    frag.append(tr);
    if (isAlbum(it) && expanded.has(it.id)) {
      frag.append(h("tr", { id: "det-q-" + it.id, class: "detail" }, h("td", {}), h("td", { colspan: 8, class: "dtd" }, trackTable(it.id))));
    }
  });
  keepFocus(() => body.replaceChildren(frag));
}

// ---- completed / settings ---------------------------------------------
function buildDone() {
  const frag = document.createDocumentFragment();
  if (!histRows.length) { frag.append(h("p", { class: "dim" }, "nothing completed yet.")); return frag; }
  for (const r of histRows) {
    const rel = (r.output_path || "").split(/[\\/]_gamdl-incoming[\\/]/)[1] || "";
    const smb = "\\\\192.168.18.225\\homelab\\hdd-backup\\music\\_gamdl-incoming\\" + rel.replaceAll("/", "\\");
    const open = expanded.has(r.id);
    frag.append(h("div", { class: "done-item" },
      h("div", { class: "row" }, toggleBtn(r, "c"), h("strong", {}, r.title || r.url), h("span", { class: "dim" }, fmtBytes(r.size_bytes)),
        r.classification ? h("span", {}, `${r.codec}: ${r.classification}`) : ""),
      h("div", { class: "row" }, h("code", {}, smb), h("button", { "data-key": "copy:" + r.id, onclick: () => navigator.clipboard?.writeText(smb) }, "copy path")),
      isAlbum(r) && open ? h("div", { id: "det-c-" + r.id, class: "detail" }, trackTable(r.id)) : "",
      r.findings.length ? h("ul", { class: "plain" }, r.findings.map(f => h("li", {}, f))) : h("div", { class: "dim" }, "no findings")));
  }
  return frag;
}
function drawDone() { const f = buildDone(); keepFocus(() => $("#panel").replaceChildren(f)); }

let panelGen = 0;
async function renderPanel() {
  const my = ++panelGen, p = $("#panel");
  document.querySelectorAll("#tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
  try {
    if (tab === "done") {
      const rows = await api("history");
      if (my !== panelGen) return;
      histRows = rows; drawDone();
    } else {
      const s = await api("settings");
      if (my !== panelGen) return;
      const frag = document.createDocumentFragment(), inputs = {};
      const fields = [["track_delay", "track delay s (min-max)"], ["album_delay", "album delay s (min-max)"], ["error_threshold", "pause after N 429/403"],
        ["low_disk_gb", "low disk GB"], ["max_tracks_per_24h", "max tracks / 24 h (0 = off)"], ["storefront", "storefront (empty = keep url's own)"],
        ["preview_max_tracks", "warn above N tracks"], ["track_retries", "album retries after a track error"], ["retry_backoff", "retry backoff s (min-max)"],
        ["library_exact", "library: in-library score"], ["library_similar", "library: similar score"]];
      for (const [k, label] of fields) { inputs[k] = h("input", { value: s[k], id: "f-" + k, "data-key": "f-" + k }); frag.append(h("div", { class: "field" }, h("label", { for: "f-" + k }, label), inputs[k])); }
      const auto = h("input", { type: "checkbox", id: "f-auto", "data-key": "f-auto", checked: s.auto_resume_after_cap });
      frag.append(h("div", { class: "field" }, h("label", { for: "f-auto" }, "auto resume after cap"), auto));
      const msg = h("span", {});
      const text = ["track_delay", "album_delay", "storefront", "retry_backoff"];
      frag.append(h("div", { class: "row" }, h("button", { "data-key": "save", onclick: async () => {
        const body = { auto_resume_after_cap: auto.checked };
        for (const [k, label] of fields) {
          const v = inputs[k].value;
          if (text.includes(k)) { body[k] = v; continue; }
          if (v.trim() === "" || !Number.isFinite(Number(v))) { msg.textContent = `${label}: a number is required`; inputs[k].focus(); return; }
          body[k] = Number(v);
        }
        try { await api("settings", { method: "PUT", body }); msg.textContent = "saved"; } catch (e) { msg.textContent = e.message; }
      } }, "save"), msg));
      p.replaceChildren(frag);
    }
  } catch (e) { if (my === panelGen) { p.replaceChildren(h("p", { class: "dim" }, "could not load: " + e.message)); } }
}
function renderAll() {
  if (!S) return;
  renderBar(); renderBanner();
  if (sigOf(S.items) !== queueSig) { renderQueue(); if (tab === "done") renderPanel(); } else updateLive();
}

async function refresh() {
  S = await api("state"); offset = S.now - Date.now() / 1000;
  const ids = new Set(S.items.map(i => i.id));
  for (const id of [...expanded]) if (!ids.has(id)) expanded.delete(id);
  for (const id of Object.keys(trackCache)) if (!ids.has(Number(id))) delete trackCache[id];
  for (const id of [...trackErr]) if (!ids.has(id)) trackErr.delete(id);
  renderAll();
  for (const id of expanded) loadTracks(id);
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
function scheduleRefresh() { if (!pending) pending = setTimeout(() => { pending = null; refresh().catch(fail); }, 250); }
function scheduleTracks(id) {
  if (!expanded.has(id) || pendingTracks.has(id)) return;
  pendingTracks.add(id);
  setTimeout(() => { pendingTracks.delete(id); loadTracks(id); }, 250);
}
function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = m => {
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.type === "log") addLog(ev);
    else if (ev.type === "tracks") scheduleTracks(ev.item_id);
    else if (ev.type === "progress" && S) {
      const it = S.items.find(i => i.id === ev.item_id);
      if (it) { it.live = { track_pct: ev.pct, speed: ev.speed, delay_kind: ev.delay_kind, delay_until: ev.delay_until }; updateLive(); }
    } else scheduleRefresh();
  };
  es.onerror = () => { es.close(); say("error: connection lost, retrying"); setTimeout(() => { refresh().catch(fail); connect(); }, 2000); };
}

// ---- add flow: parse -> preview + library check -> queue ---------------
let runId = 0;
$("#btn-preview").onclick = async () => {
  const my = ++runId, msg = $("#add-msg"), btn = $("#btn-preview"), text = $("#urls").value;
  btn.disabled = true; msg.textContent = "reading...";
  try {
    const { results } = await api("parse", { method: "POST", body: { text } });
    if (my !== runId) return;
    preview = results.map(r => ({ ...r, info: null, checked: false, force: false }));
    drawPreview(); msg.textContent = preview.length ? "" : "no urls found.";
    for (const row of preview) {
      if (my !== runId) return;
      if (!row.url) continue;
      try {
        row.info = await api("preview", { method: "POST", body: { url: row.url } });
        row.checked = row.info.library.default_checked && !row.duplicate;
      } catch (e) { row.info = { error: e.message }; row.checked = !row.duplicate; }
      if (my !== runId) return;
      drawPreview();
    }
  } catch (e) { msg.textContent = "error: " + e.message; }
  finally { if (my === runId) btn.disabled = false; }
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
  keepFocus(() => {
    const t = $("#pv"), b = $("#pv tbody"); b.replaceChildren();
    t.hidden = $("#pv-actions").hidden = preview.length === 0;
    preview.forEach((r, n) => {
      const needs = !!r.info?.library?.needs_force;
      const cb = h("input", { type: "checkbox", "data-key": "pvcb:" + n, checked: r.checked, disabled: !r.url || (needs && !r.force), "aria-label": "include",
        onchange: e => { r.checked = e.target.checked; } });
      const pv = r.info?.preview;
      const anyway = needs ? (r.force ? h("span", {}, "will download anyway")
        : h("button", { "data-key": "pvforce:" + n, onclick: () => { r.force = true; r.checked = true; drawPreview(); } }, "download anyway")) : "";
      b.append(h("tr", {}, h("td", {}, cb),
        h("td", { class: "name" }, r.error ? `${r.raw}: ${r.error}` : pv?.title || r.url, r.duplicate ? h("div", { class: "dim" }, "already queued") : ""),
        h("td", {}, pv?.artist || ""), h("td", {}, pv?.tracks ?? ""), h("td", { class: "name" }, libCell(r)), h("td", {}, anyway)));
    });
  });
}
$("#btn-queue").onclick = async () => {
  const items = preview.filter(r => r.checked && r.url).map(r => ({
    url: r.url, title: r.info?.preview?.title || null, artist: r.info?.preview?.artist || null,
    tracks: r.info?.preview?.tracks ?? null, force: !!r.force }));
  if (!items.length) return;
  try {
    const { results } = await api("queue", { method: "POST", body: { items } });
    const bad = results.filter(r => r.error);
    $("#add-msg").textContent = bad.length ? bad.map(r => r.error).join("; ") : `queued ${results.length}`;
    runId++; $("#btn-preview").disabled = false;
    preview = []; drawPreview(); $("#urls").value = ""; refresh().catch(fail);
  } catch (e) { $("#add-msg").textContent = "error: " + e.message; }
};
document.querySelectorAll("#tabs button").forEach(b => { b.dataset.key = "tab:" + b.dataset.tab; b.onclick = () => { tab = b.dataset.tab; renderPanel(); }; });
$("#logfilter").onchange = e => { logFilter = e.target.value; drawLog(); };
$("#logcopy").onclick = () => navigator.clipboard?.writeText(logLines.map(l => l.text).join("\n"));
setInterval(() => document.querySelectorAll("[data-until]").forEach(e => { e.textContent = countdown(e.dataset.label, Number(e.dataset.until)); }), 1000);

(async () => {
  logLines = (await api("log?limit=200").catch(() => [])).map(l => ({ level: l.level, text: l.text }));
  drawLog(); connect();
  (async function start() { try { await refresh(); say(""); } catch (e) { fail(e); setTimeout(start, 3000); } })();
})();
