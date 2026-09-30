"use strict";
const $ = (s, r = document) => r.querySelector(s);
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
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
    const el = document.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (el && el !== document.activeElement) el.focus({ preventScroll: true });
  }
}
const fmtBytes = n => n == null ? "?" : n >= 1e9 ? (n / 1e9).toFixed(1) + " GB" : n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB";

// ---- icons (stroke icons, same style as lucide) -----------------------
const ICON = {
  plus: '<path d="M12 5v14M5 12h14"/>', play: '<path d="M6 4l14 8-14 8z"/>', pause: '<path d="M8 5v14M16 5v14"/>',
  stop: '<rect x="6" y="6" width="12" height="12"/>', retry: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>', top: '<path d="M7 11l5-5 5 5M7 18l5-5 5 5"/>',
  up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>', bottom: '<path d="M7 6l5 5 5-5M7 13l5 5 5-5"/>',
  power: '<path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/>', gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
  scroll: '<path d="M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>', search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  x: '<path d="M18 6L6 18M6 6l12 12"/>', copy: '<rect x="9" y="9" width="12" height="12"/><path d="M5 15V5h10"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', check: '<path d="M4 12l5 5L20 6"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>', download: '<path d="M12 3v12M7 10l5 5 5-5M4 20h16"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5"/>',
};
function ico(name, size = 14) {
  const e = document.createElement("span");
  e.style.display = "inline-flex";
  e.innerHTML = `<svg class="ico" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${ICON[name] || ""}</svg>`;
  return e;
}

// ---- model ------------------------------------------------------------
const STATUS = { downloading: "DOWNLOADING", waiting: "WAITING", queued: "QUEUED", done: "DONE", error: "FAILED", cancelled: "STOPPED" };
const RUNNING = ["downloading", "waiting"], PENDING = ["queued", "downloading", "waiting"];
const FILTERS = [
  ["all", "All", "layers", () => true],
  ["downloading", "Downloading", "download", i => RUNNING.includes(i.status)],
  ["queued", "Queued", "clock", i => i.status === "queued"],
  ["completed", "Completed", "check", i => i.status === "done"],
  ["stopped", "Stopped", "stop", i => i.status === "cancelled"],
  ["failed", "Failed", "alert", i => i.status === "error"],
];
let S = null, offset = 0, filter = "all", query = "", dtab = "general", dopen = true, connected = false, logFilter = "all";
const sel = new Set();
let anchor = null;
const trackCache = {}, trackErr = new Set();

const byId = id => S.items.find(i => i.id === id);
function visible() {
  const f = FILTERS.find(x => x[0] === filter)[3], q = query.trim().toLowerCase();
  return S.items.filter(f).filter(i => !q || [i.title, i.artist, i.url, i.original_url, i.status, STATUS[i.status]].join(" ").toLowerCase().includes(q));
}
const curItem = () => {
  if (!S || !sel.size) return null;
  return byId(sel.has(anchor) ? anchor : [...sel][0]) || null;
};
function caps() {
  const items = S ? [...sel].map(byId).filter(Boolean) : [];
  return {
    items,
    retry: items.some(i => ["error", "cancelled"].includes(i.status)),
    stop: items.some(i => PENDING.includes(i.status)),
    remove: items.some(i => !RUNNING.includes(i.status)),
    prio: items.some(i => i.status === "queued"),
  };
}
function albumPct(it) {
  if (it.status === "done") return 100;
  if (!it.track_n) return 0;
  const done = it.status === "waiting" ? it.track_i : (it.track_i || 1) - 1;
  const cur = it.status === "downloading" ? (it.live?.track_pct || 0) / 100 : 0;
  return Math.min(100, ((done + cur) / it.track_n) * 100);
}
const nowSrv = () => Date.now() / 1000 + offset;
const countdown = (label, until) => `${label} ${Math.max(0, Math.round(until - nowSrv()))} s`;
function delayInfo(it) {
  const live = it.live || {};
  if (live.delay_until) return { until: live.delay_until, label: `${live.delay_kind} delay` };
  if (it.status === "queued" && it.attempts && it.not_before) return { until: it.not_before, label: `retry ${it.attempts}/${S.settings.track_retries} in` };
  return null;
}
const engine = () => S.paused ? "PAUSED" : S.items.some(i => RUNNING.includes(i.status)) ? "RUNNING" : "IDLE";

// ---- actions ----------------------------------------------------------
async function actOn(what, pick) {
  for (const it of caps().items.filter(pick)) await api(`queue/${it.id}/${what}`, { method: "POST" }).catch(fail);
  refresh().catch(fail);
}
const doRetry = () => actOn("retry", i => ["error", "cancelled"].includes(i.status));
const doRetryOrig = () => actOn("retry_original", i => ["error", "cancelled"].includes(i.status));
const doStop = () => actOn("cancel", i => PENDING.includes(i.status));
const doRemove = () => actOn("remove", i => !RUNNING.includes(i.status));
function priority(dir) {
  const ids = S.items.map(i => i.id);
  const slots = ids.map((id, k) => k).filter(k => byId(ids[k]).status === "queued");
  let q = slots.map(k => ids[k]);
  const picked = q.filter(id => sel.has(id)), rest = q.filter(id => !sel.has(id));
  if (!picked.length) return;
  if (dir === "top") q = [...picked, ...rest];
  else if (dir === "bottom") q = [...rest, ...picked];
  else if (dir === "up") { for (let i = 1; i < q.length; i++) if (sel.has(q[i]) && !sel.has(q[i - 1])) [q[i - 1], q[i]] = [q[i], q[i - 1]]; }
  else { for (let i = q.length - 2; i >= 0; i--) if (sel.has(q[i]) && !sel.has(q[i + 1])) [q[i + 1], q[i]] = [q[i], q[i + 1]]; }
  slots.forEach((k, n) => { ids[k] = q[n]; });
  S.items = ids.map(byId);   // optimistic, so repeated clicks stack
  renderRows();
  api("queue/reorder", { method: "POST", body: { ids } }).catch(e => { fail(e); refresh().catch(fail); });
}
const toggleEngine = () => api(S.paused ? "resume" : "pause", { method: "POST" }).then(() => refresh()).catch(fail);

// ---- selection --------------------------------------------------------
function clickRow(e, id) {
  const vis = visible().map(i => i.id);
  if (e.shiftKey && anchor != null && vis.includes(anchor)) {
    const a = vis.indexOf(anchor), b = vis.indexOf(id), [lo, hi] = a < b ? [a, b] : [b, a];
    if (!(e.ctrlKey || e.metaKey)) sel.clear();
    vis.slice(lo, hi + 1).forEach(x => sel.add(x));
  } else if (e.ctrlKey || e.metaKey) {
    if (sel.has(id)) sel.delete(id); else sel.add(id);
    anchor = id;
  } else { sel.clear(); sel.add(id); anchor = id; }
  selectionChanged();
}
function selectionChanged() {
  renderRows(); renderToolbar(); renderQInfo(); renderDTabs(); renderDBody();
  const c = curItem(); if (dtab === "tracks" && c) loadTracks(c.id);
}

// ---- toolbar / sidebar / banner / status ------------------------------
function tb(icon, label, opts = {}) {
  const { class: cls = "", key, ...rest } = opts;
  return h("button", { type: "button", class: "tb " + cls, "data-key": key, ...rest }, ico(icon), label ? h("span", { class: "t" }, label) : "");
}
function renderToolbar() {
  const c = caps(), paused = S.paused;
  keepFocus(() => $("#toolbar").replaceChildren(
    h("div", { class: "tgroup" },
      h("div", { class: "brand" + (engine() === "RUNNING" ? " live" : "") }, h("i"), h("span", {}, "GAMDL")),
      tb("plus", "Add", { class: "primary", key: "add", title: "Add URLs", onclick: openAdd }),
      h("span", { class: "tsep" }),
      tb("retry", "Retry", { key: "retry", title: "Retry selected", disabled: !c.retry, onclick: doRetry }),
      tb("stop", "Stop", { key: "stop", title: "Stop / cancel selected", disabled: !c.stop, onclick: doStop }),
      tb("trash", "Remove", { key: "remove", title: "Remove selected from the queue (Delete). Downloaded files are never touched", disabled: !c.remove, onclick: askRemove }),
      h("span", { class: "tsep" }),
      tb("top", "", { class: "icon", key: "ptop", title: "Move to top", disabled: !c.prio, onclick: () => priority("top") }),
      tb("up", "", { class: "icon", key: "pup", title: "Move up ( [ )", disabled: !c.prio, onclick: () => priority("up") }),
      tb("down", "", { class: "icon", key: "pdown", title: "Move down ( ] )", disabled: !c.prio, onclick: () => priority("down") }),
      tb("bottom", "", { class: "icon", key: "pbottom", title: "Move to bottom", disabled: !c.prio, onclick: () => priority("bottom") })),
    h("div", { class: "tgroup" },
      tb(paused ? "play" : "pause", paused ? "Start queue" : "Pause queue", { class: "engine", key: "engine", title: paused ? "Start / resume the queue" : "Pause after the current track", onclick: toggleEngine }),
      h("span", { class: "tsep" }),
      tb("gear", "Settings", { key: "settings", onclick: openSettings }),
      tb("scroll", "Logs", { key: "logs", onclick: () => { dtab = "log"; dopen = true; applyDetailHeight(); renderDTabs(); renderDBody(); } }))));
}
function renderSidebar() {
  const counts = Object.fromEntries(FILTERS.map(f => [f[0], S.items.filter(f[3]).length]));
  const cookies = S.cookies, cap = S.cap;
  let ck = "missing", bad = true;
  if (cookies.exists) {
    if (cookies.problem) ck = "unusable";
    else if (cookies.expired) ck = "expired";
    else if (cookies.expiry_days == null) { ck = "ok"; bad = false; }
    else { ck = `ok, ${cookies.expiry_days} d left`; bad = cookies.expiry_days <= 7; }
  }
  keepFocus(() => $("#sidebar").replaceChildren(
    h("div", { class: "side-h" }, "Status"),
    ...FILTERS.map(([id, label, icon]) => h("button", { type: "button", class: "flt" + (filter === id ? " active" : ""), "data-key": "flt:" + id, "aria-pressed": String(filter === id),
      onclick: () => { filter = id; renderRows(); renderSidebar(); renderQInfo(); } },
      h("span", { class: "l" }, ico(icon), label), h("span", { class: "pill" }, counts[id]))),
    h("div", { class: "side-h" }, "System"),
    h("div", { class: "sys" },
      h("div", {}, h("div", { class: "k" }, "disk free"), fmtBytes(S.disk.free_bytes)),
      h("div", {}, h("div", { class: "k" }, "queue estimate"), "~" + fmtBytes(S.forecast_bytes)),
      h("div", {}, h("div", { class: "k" }, cap.limit ? `today ${cap.used} / ${cap.limit} tracks` : `today ${cap.used} tracks`),
        cap.limit ? h("div", { class: "meter" }, h("i", { style: `width:${Math.min(100, cap.used / cap.limit * 100)}%` })) : ""),
      h("div", {}, h("div", { class: "k" }, "cookies"), h("span", { class: bad ? "bad" : "" }, ck)))));
}
function renderBanner() {
  const b = $("#banner"), msgs = [];
  if (S.banner) msgs.push(`${S.banner.kind}: ${S.banner.reason}. Fix the cause, then press Start queue.`);
  if (S.cookies.exists && S.cookies.problem) msgs.push(`cookies unusable: ${S.cookies.problem}.`);
  else if (S.cookies.expired) msgs.push("cookies expired: re-export cookies.txt.");
  else if (S.cookies.exists && S.cookies.expiry_days != null && S.cookies.expiry_days <= 7) msgs.push(`cookies expire in ${S.cookies.expiry_days} d: re-export soon.`);
  b.hidden = msgs.length === 0; b.textContent = msgs.join(" ");
}
function renderStatus() {
  const run = S.items.find(i => RUNNING.includes(i.status)), n = f => S.items.filter(f).length;
  const speed = run?.live?.speed || "0 KB/s";
  const stopped = n(i => i.status === "cancelled");
  $("#statusbar").replaceChildren(
    h("div", { class: "g" }, h("span", {}, h("b", {}, engine())),
      run ? h("span", { class: "dim" }, `${run.title || run.url} (${Math.round(albumPct(run))}%)`) : ""),
    h("div", { class: "g" }, h("span", {}, "Active: ", h("b", {}, n(i => RUNNING.includes(i.status)))), h("span", {}, "Queued: ", h("b", {}, n(i => i.status === "queued"))),
      h("span", {}, "Done: ", h("b", {}, n(i => i.status === "done"))), stopped ? h("span", {}, "Stopped: ", h("b", {}, stopped)) : "",
      h("span", {}, "Failed: ", h("b", {}, n(i => i.status === "error")))),
    h("div", { class: "g" }, h("span", { id: "sb-speed" }, "Speed: ", h("b", {}, speed)),
      h("span", {}, h("span", { class: "lamp" + (connected ? " on" : "") }), connected ? "Live" : "Reconnecting...")));
}

// ---- queue table ------------------------------------------------------
function bar(pct, wait) {
  const i = h("i"); i.style.width = Math.max(0, Math.min(100, pct || 0)) + "%";
  return h("div", { class: "bar" + (wait ? " wait" : ""), role: "progressbar", "aria-valuenow": Math.round(pct || 0) }, i);
}
function renderQInfo() {
  const el = $("#qinfo"); if (!el || !S) return;
  el.replaceChildren(sel.size ? h("b", {}, `${sel.size} selected`) : "", sel.size ? " · " : "", "Showing ", h("b", {}, visible().length), ` / ${S.items.length}`);
}
let rowSig = null;
const sigOf = items => items.map(i => [i.id, i.status, i.track_i, i.track_n, i.error_msg, i.attempts, i.not_before, i.title, i.artist, i.size_bytes, i.library_note, i.codec, (i.findings || []).length].join("|")).join("\n") + "#" + S.settings.track_retries;
function renderRows() {
  const vis = visible(), frag = document.createDocumentFragment();
  rowSig = sigOf(S.items);
  vis.forEach((it, n) => {
    const cd = delayInfo(it), pct = albumPct(it);
    const tr = h("tr", { tabindex: 0, "data-id": it.id, "data-key": "row:" + it.id, class: sel.has(it.id) ? "sel" : "", "aria-selected": String(sel.has(it.id)),
      onclick: e => clickRow(e, it.id),
      oncontextmenu: e => { e.preventDefault(); if (!sel.has(it.id)) { sel.clear(); sel.add(it.id); anchor = it.id; selectionChanged(); } openCtx(e.clientX, e.clientY); },
      onkeydown: e => {
        if (e.target !== tr) return;
        if (e.key === " " || e.key === "Enter") { e.preventDefault(); clickRow(e, it.id); }
        if (e.key === "[") { sel.clear(); sel.add(it.id); anchor = it.id; priority("up"); }
        if (e.key === "]") { sel.clear(); sel.add(it.id); anchor = it.id; priority("down"); }
      } },
      h("td", { class: "c-n" }, n + 1),
      h("td", { class: "c-name", title: it.original_url || it.url }, h("div", {}, it.title || it.url),
        it.artist ? h("div", { class: "sub" }, it.artist) : "", it.error_msg ? h("div", { class: "sub" }, it.error_msg) : ""),
      h("td", { class: "c-status" }, h("span", { class: "chip st-" + it.status }, STATUS[it.status] || it.status)),
      h("td", { class: "c-prog" }, h("div", { class: "pcell" }, bar(pct, it.status === "waiting"), h("span", { class: "pct" }, Math.round(pct) + "%"))),
      h("td", { class: "c-tracks" }, it.track_n ? `${it.track_i || 0}/${it.track_n}` : "-"),
      h("td", { class: "c-speed" }, (it.live || {}).speed || ""),
      h("td", { class: "c-delay", ...(cd ? { "data-until": cd.until, "data-label": cd.label } : {}) }, cd ? countdown(cd.label, cd.until) : it.status === "waiting" ? "waiting" : ""),
      h("td", { class: "c-size" }, it.status === "done" ? fmtBytes(it.size_bytes) : "-"));
    frag.append(tr);
  });
  const empty = $("#empty");
  empty.hidden = vis.length > 0;
  empty.textContent = S.items.length ? "no items match this filter." : "the queue is empty. Press Add to queue some URLs.";
  keepFocus(() => $("#rows").replaceChildren(frag));
}
// progress events only touch bars and the speed/countdown cells; nothing is rebuilt, so focus is never lost
function updateLive() {
  for (const it of S.items) {
    const tr = document.querySelector(`#rows > tr[data-id="${it.id}"]`);
    if (!tr) continue;
    const pct = albumPct(it), b = tr.querySelector(".bar");
    b.firstChild.style.width = pct + "%"; b.setAttribute("aria-valuenow", Math.round(pct));
    tr.querySelector(".pct").textContent = Math.round(pct) + "%";
    tr.querySelector(".c-speed").textContent = (it.live || {}).speed || "";
    const cd = delayInfo(it), cell = tr.querySelector(".c-delay");
    if (cd) { cell.dataset.until = cd.until; cell.dataset.label = cd.label; cell.textContent = countdown(cd.label, cd.until); }
    else { delete cell.dataset.until; delete cell.dataset.label; cell.textContent = it.status === "waiting" ? "waiting" : ""; }
  }
  renderStatus();
}

// ---- detail panel -----------------------------------------------------
function applyDetailHeight() {
  const d = $("#detail");
  let saved = "270px"; try { saved = localStorage.getItem("gd-detail-h") || saved; } catch { /* storage blocked */ }
  d.style.height = dopen ? saved : "32px";
  $("#resizer").hidden = !dopen;
}
function renderDTabs() {
  const c = curItem(), t = (id, icon, label, extra = "") => h("button", { type: "button", role: "tab", "data-key": "dt:" + id, "aria-selected": String(dtab === id),
    onclick: () => { dtab = id; dopen = true; applyDetailHeight(); renderDTabs(); renderDBody(); const cc = curItem(); if (id === "tracks" && cc) loadTracks(cc.id); } }, ico(icon), label, extra);
  keepFocus(() => $("#dtabs").replaceChildren(
    h("div", { class: "tabs", role: "tablist" }, t("general", "info", "General"), t("tracks", "list", "Tracks", c && c.track_n ? h("span", { class: "pill" }, c.track_n) : ""), t("log", "scroll", "Log")),
    h("div", { class: "tgroup" },
      h("span", { class: "cur" }, sel.size > 1 ? `${sel.size} items selected` : c ? (c.title || c.url) : ""),
      h("button", { type: "button", class: "tb icon", "data-key": "dcollapse", title: dopen ? "collapse" : "expand", "aria-expanded": String(dopen),
        onclick: () => { dopen = !dopen; applyDetailHeight(); renderDTabs(); renderDBody(); } }, ico(dopen ? "down" : "up")))));
}
async function loadTracks(id) {
  try { trackCache[id] = (await api("queue/" + id)).tracks; trackErr.delete(id); }
  catch { delete trackCache[id]; trackErr.add(id); }
  const c = curItem(); if (c && c.id === id && dtab === "tracks") renderDBody();
}
function trackTable(id) {
  const rows = trackCache[id];
  if (trackErr.has(id)) return h("p", { class: "dim" }, "could not load tracks (re-select the item to retry).");
  if (!rows) return h("p", { class: "dim" }, "loading...");
  if (!rows.length) return h("p", { class: "dim" }, "no tracks yet.");
  return h("table", { class: "tracks" },
    h("thead", {}, h("tr", {}, ["#", "status", "title", "note"].map(x => h("th", {}, x)))),
    h("tbody", {}, rows.map(t => h("tr", {}, h("td", {}, t.idx), h("td", {}, h("span", { class: "chip st-" + (["error", "done", "downloading"].includes(t.status) ? t.status : "queued") }, t.status)),
      h("td", { class: "c-name" }, t.title), h("td", { class: "dim" }, t.reason || "")))));
}
const kv = (k, v, cls = "") => h("div", { class: "r " + cls }, h("span", { class: "k" }, k), h("span", { class: "v sel-text" }, v));
function generalPane(it) {
  const rel = (it.output_path || "").split(/[\\/]_gamdl-incoming[\\/]/)[1] || "";
  const smb = rel ? "\\\\192.168.18.225\\homelab\\hdd-backup\\music\\_gamdl-incoming\\" + rel.replaceAll("/", "\\") : "";
  const src = it.original_url || it.url;
  const rows = [
    kv("Title", it.title || "-", "wide"),
    kv("Artist", it.artist || "-"),
    kv("Link", h("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, src)),
    kv("Status", [STATUS[it.status] || it.status, it.error_msg ? ` (${it.error_msg})` : ""]),
    kv("Tracks", it.track_n ? `${it.track_i || 0} / ${it.track_n}` : "-"),
    kv("Attempts", String(it.attempts || 0)),
    kv("Codec", it.codec ? `${it.codec}${it.classification ? ": " + it.classification : ""}` : "-"),
    kv("Size", it.status === "done" ? fmtBytes(it.size_bytes) : "-"),
  ];
  if (it.output_path) rows.push(kv("Saved to", [h("code", {}, smb || it.output_path), smb ? h("button", { type: "button", class: "tb", style: "margin-left:8px", onclick: () => navigator.clipboard?.writeText(smb) }, ico("copy", 12), "copy") : ""], "wide"));
  const out = [h("div", { class: "kv" }, rows)];
  if (it.library_note) out.push(h("div", { class: "funfact" }, h("b", {}, "Fun fact: "), it.library_note));
  if (it.status === "done") out.push(it.findings.length ? h("ul", { class: "plain" }, it.findings.map(f => h("li", {}, f))) : h("p", { class: "dim" }, "no findings"));
  return out;
}
let logLines = [], logPre = null;
function logNodes() {
  const rank = { DEBUG: 0, INFO: 0, WARNING: 1, ERROR: 2, CRITICAL: 2 }, min = { all: 0, warn: 1, error: 2 }[logFilter];
  return logLines.filter(l => (rank[l.level] ?? 0) >= min).map(l => h("div", { class: "lvl-" + l.level }, l.text));
}
function drawLog() {
  if (!logPre) return;
  const body = $("#dbody"), stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
  logPre.replaceChildren(...logNodes());
  if (stick) body.scrollTop = body.scrollHeight;
}
function renderDBody() {
  const body = $("#dbody"); body.hidden = !dopen; logPre = null;
  if (!dopen) return;
  if (dtab === "log") {
    logPre = h("pre", { class: "log sel-text", tabindex: 0 });
    body.replaceChildren(h("div", { class: "rowbar" }, h("select", { "aria-label": "log filter", onchange: e => { logFilter = e.target.value; drawLog(); } },
      ["all", "warn", "error"].map(v => h("option", { value: v, selected: v === logFilter }, v === "warn" ? "warn+" : v))),
      h("button", { type: "button", class: "tb", onclick: () => navigator.clipboard?.writeText(logLines.map(l => l.text).join("\n")) }, ico("copy", 12), "copy")), logPre);
    drawLog(); body.scrollTop = body.scrollHeight; return;
  }
  const it = curItem();
  if (!it) { body.replaceChildren(h("p", { class: "dim" }, "Select an item in the queue to inspect its details and tracks.")); return; }
  if (dtab === "tracks") { body.replaceChildren(trackTable(it.id)); return; }
  body.replaceChildren(...generalPane(it));
}

// ---- context menu -----------------------------------------------------
function openCtx(x, y) {
  const c = caps(), m = $("#ctx"), item = (icon, label, fn, off) => h("button", { type: "button", role: "menuitem", disabled: !!off, onclick: () => { closeCtx(); fn(); } }, ico(icon, 13), label);
  m.replaceChildren(item("retry", "Retry", doRetry, !c.retry), item("retry", "Retry with original storefront", doRetryOrig, !c.retry), item("stop", "Stop", doStop, !c.stop), item("trash", "Remove from queue", askRemove, !c.remove),
    h("hr"), item("top", "Move to top", () => priority("top"), !c.prio), item("up", "Move up", () => priority("up"), !c.prio), item("down", "Move down", () => priority("down"), !c.prio), item("bottom", "Move to bottom", () => priority("bottom"), !c.prio),
    h("hr"), item("copy", "Copy link", () => navigator.clipboard?.writeText(curItem()?.original_url || curItem()?.url || ""), !curItem()));
  m.hidden = false;
  m.style.left = Math.min(x, innerWidth - m.offsetWidth - 6) + "px"; m.style.top = Math.min(y, innerHeight - m.offsetHeight - 6) + "px";
}
function closeCtx() { $("#ctx").hidden = true; }

// ---- modals -----------------------------------------------------------
function openModal(title, build) {
  const prev = document.activeElement;
  const close = () => { overlay.remove(); if (prev && prev.focus) prev.focus(); };
  const { body, footer } = build(close);
  const overlay = h("div", { class: "overlay", onmousedown: e => { if (e.target === overlay) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
      h("header", {}, h("span", {}, title), h("button", { type: "button", class: "tb icon", "aria-label": "close", onclick: close }, ico("x"))),
      h("div", { class: "body" }, body), h("footer", {}, footer)));
  overlay.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
  $("#modal-root").append(overlay);
  (overlay.querySelector("textarea, input") || overlay.querySelector("footer button:last-child")).focus();
  return close;
}
function openAdd() {
  openModal("Add URLs", close => {
    const ta = h("textarea", { spellcheck: "false", "aria-label": "urls", placeholder: "https://music.apple.com/jp/album/...\none URL per line" });
    const msg = h("div", { class: "msg", role: "status" });
    const go = h("button", { type: "button", class: "tb primary", onclick: async () => {
      go.disabled = true; msg.className = "msg"; msg.textContent = "adding...";
      try {
        const { results } = await api("parse", { method: "POST", body: { text: ta.value } });
        const ok = results.filter(r => r.url && !r.duplicate), skipped = results.filter(r => !r.url || r.duplicate);
        let queued = 0; const errs = [];
        if (ok.length) {
          const q = await api("queue", { method: "POST", body: { items: ok.map(r => ({ url: r.url })) } });
          q.results.forEach(r => { if (r.error) errs.push(r.error); else queued++; });
        }
        const problems = [...skipped.map(r => `${r.raw}: ${r.error || "already queued"}`), ...errs];
        refresh().catch(fail);
        if (!problems.length) { if (queued) say(""); close(); return; }
        ta.value = skipped.map(r => r.raw).join("\n");
        msg.className = "msg err"; msg.textContent = `queued ${queued}, skipped ${problems.length}:\n` + problems.join("\n");
      } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
      finally { go.disabled = false; }
    } }, "Add to queue");
    return { body: [h("div", { class: "dim" }, "One URL per line, any storefront (albums are downloaded from the configured storefront). Duplicates are skipped. No library check here: it runs after each download."), ta, msg],
      footer: [h("button", { type: "button", class: "tb", onclick: close }, "Cancel"), go] };
  });
}
function askRemove() {
  const n = caps().items.filter(i => !RUNNING.includes(i.status)).length;
  if (!n) return;
  openModal("Remove from queue", close => ({
    body: [h("p", {}, `Remove ${n} item${n > 1 ? "s" : ""} from the queue?`), h("p", { class: "dim" }, "Downloaded files are never touched.")],
    footer: [h("button", { type: "button", class: "tb", onclick: close }, "Cancel"), h("button", { type: "button", class: "tb primary", onclick: () => { close(); doRemove(); } }, "Remove")],
  }));
}
async function openSettings() {
  let s; try { s = await api("settings"); } catch (e) { fail(e); return; }
  const fields = [["track_delay", "track delay s (min-max)"], ["album_delay", "album delay s (min-max)"], ["error_threshold", "pause after N 429/403"],
    ["low_disk_gb", "low disk GB"], ["max_tracks_per_24h", "max tracks / 24 h (0 = off)"], ["storefront", "storefront (empty = keep url's own)"],
    ["track_retries", "album retries after a track error"], ["retry_backoff", "retry backoff s (min-max)"],
    ["library_exact", "library note: in-library score"], ["library_similar", "library note: similar score"]];
  const text = ["track_delay", "album_delay", "storefront", "retry_backoff"];
  openModal("Settings", close => {
    const inputs = {}, msg = h("div", { class: "msg", role: "status" });
    const rows = fields.map(([k, label]) => { inputs[k] = h("input", { id: "f-" + k, value: s[k] }); return h("div", { class: "field" }, h("label", { for: "f-" + k, class: "dim" }, label), inputs[k]); });
    const auto = h("input", { type: "checkbox", id: "f-auto", checked: s.auto_resume_after_cap });
    rows.push(h("div", { class: "field" }, h("label", { for: "f-auto", class: "dim" }, "auto resume after daily cap"), auto));
    const save = h("button", { type: "button", class: "tb primary", onclick: async () => {
      const body = { auto_resume_after_cap: auto.checked };
      for (const [k, label] of fields) {
        const v = inputs[k].value;
        if (text.includes(k)) { body[k] = v; continue; }
        if (v.trim() === "" || !Number.isFinite(Number(v))) { msg.className = "msg err"; msg.textContent = `${label}: a number is required`; inputs[k].focus(); return; }
        body[k] = Number(v);
      }
      try { await api("settings", { method: "PUT", body }); refresh().catch(fail); close(); say(""); } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
    } }, "Save");
    return { body: [...rows, msg], footer: [h("button", { type: "button", class: "tb", onclick: close }, "Cancel"), save] };
  });
}

// ---- refresh, log, live events ---------------------------------------
function renderAll() {
  if (!S) return;
  renderToolbar(); renderSidebar(); renderBanner(); renderStatus(); renderQInfo();
  if (sigOf(S.items) !== rowSig) { renderRows(); renderDTabs(); if (dtab !== "log") renderDBody(); } else updateLive();
}
async function refresh() {
  S = await api("state"); offset = S.now - Date.now() / 1000;
  const ids = new Set(S.items.map(i => i.id));
  for (const id of [...sel]) if (!ids.has(id)) sel.delete(id);
  for (const id of Object.keys(trackCache)) if (!ids.has(Number(id))) delete trackCache[id];
  for (const id of [...trackErr]) if (!ids.has(id)) trackErr.delete(id);
  renderAll();
  const c = curItem(); if (dtab === "tracks" && c) loadTracks(c.id);
}
function addLog(l) { logLines.push(l); if (logLines.length > 500) logLines.shift(); if (logPre) drawLog(); }
let pending = null, pendingTracks = null;
function scheduleRefresh() { if (!pending) pending = setTimeout(() => { pending = null; refresh().catch(fail); }, 250); }
function scheduleTracks(id) {
  const c = curItem();
  if (dtab !== "tracks" || !c || c.id !== id || pendingTracks) return;
  pendingTracks = setTimeout(() => { pendingTracks = null; loadTracks(id); }, 250);
}
function connect() {
  const es = new EventSource("/api/events");
  es.onopen = () => { connected = true; if (S) renderStatus(); };
  es.onmessage = m => {
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    if (ev.type === "log") addLog(ev);
    else if (ev.type === "tracks") scheduleTracks(ev.item_id);
    else if (ev.type === "progress" && S) {
      const it = byId(ev.item_id);
      if (it) { it.live = { track_pct: ev.pct, speed: ev.speed, delay_kind: ev.delay_kind, delay_until: ev.delay_until }; updateLive(); }
    } else scheduleRefresh();
  };
  es.onerror = () => { es.close(); connected = false; if (S) renderStatus(); say("error: connection lost, retrying"); setTimeout(() => { refresh().catch(fail); connect(); }, 2000); };
}

// ---- wiring -----------------------------------------------------------
$("#qhead").replaceChildren(
  h("div", { class: "searchbox" }, ico("search", 13),
    h("input", { type: "text", id: "search", "aria-label": "filter list", placeholder: "Filter list...", spellcheck: "false",
      oninput: e => { query = e.target.value; $("#search-clear").hidden = !query; if (S) { renderRows(); renderQInfo(); } } }),
    h("button", { type: "button", id: "search-clear", hidden: true, "aria-label": "clear filter", onclick: () => { $("#search").value = ""; query = ""; $("#search-clear").hidden = true; if (S) { renderRows(); renderQInfo(); } } }, ico("x", 12))),
  h("div", { class: "qinfo", id: "qinfo" }));
document.addEventListener("click", e => { if (!e.target.closest("#ctx")) closeCtx(); });
document.addEventListener("scroll", closeCtx, true);
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { closeCtx(); return; }
  const t = e.target;
  if (!S || (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) || $("#modal-root").firstChild) return;
  if (e.key === "Delete") askRemove();
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); visible().forEach(i => sel.add(i.id)); anchor = anchor ?? [...sel][0] ?? null; selectionChanged(); }
});
$("#resizer").addEventListener("mousedown", e => {
  e.preventDefault();
  const d = $("#detail"), r = $("#resizer"), startY = e.clientY, startH = d.offsetHeight;
  r.classList.add("drag");
  const move = ev => { d.style.height = Math.max(90, Math.min(innerHeight - 260, startH + (startY - ev.clientY))) + "px"; };
  const up = () => {
    removeEventListener("mousemove", move); removeEventListener("mouseup", up); r.classList.remove("drag");
    try { localStorage.setItem("gd-detail-h", d.style.height); } catch { /* storage blocked */ }
  };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
});
setInterval(() => document.querySelectorAll("[data-until]").forEach(e => { e.textContent = countdown(e.dataset.label, Number(e.dataset.until)); }), 1000);

(async () => {
  applyDetailHeight();
  logLines = (await api("log?limit=200").catch(() => [])).map(l => ({ level: l.level, text: l.text }));
  connect();
  (async function start() { try { await refresh(); renderDTabs(); renderDBody(); say(""); } catch (e) { fail(e); setTimeout(start, 3000); } })();
})();
