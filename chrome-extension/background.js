// Jobber Autofill: background worker.
// Profile source, in order: the Jobber app (http://127.0.0.1:8765) when it's running,
// else the last copy saved in Chrome, else snapshot.json shipped inside this folder.
const APP = "http://127.0.0.1:8765";
const API = APP + "/api/apply/ext";

async function bundled() {
  try { return await (await fetch(chrome.runtime.getURL("snapshot.json"))).json(); } catch (e) { return null; }
}

async function profile(force) {
  const { prof, at } = await chrome.storage.local.get(["prof", "at"]);
  if (prof && !force && Date.now() - (at || 0) < 60_000) return prof;
  try {
    const r = await fetch(API + "/profile");
    if (r.ok) {
      const p = await r.json();
      p.source = "app";
      await chrome.storage.local.set({ prof: p, at: Date.now() });
      return p;
    }
  } catch (e) {}
  if (prof) return { ...prof, source: "saved copy" };
  const b = await bundled();
  if (b) { b.source = "built-in copy"; await chrome.storage.local.set({ prof: b, at: 0 }); }
  return b;
}

async function status(text) {
  await chrome.storage.local.set({ last: { text, at: Date.now() } });
}

async function applied(msg) {
  await status(`Applied: ${msg.title || msg.host}`);
  try {
    const r = await fetch(API + "/applied", { method: "POST", body: JSON.stringify(msg) });
    await chrome.storage.local.set({ at: 0 });  // pick up newly learned answers next time
    return await r.json();
  } catch (e) {
    const { pending = [] } = await chrome.storage.local.get("pending");
    pending.push(msg);
    await chrome.storage.local.set({ pending });
    return { warn: "Jobber app is closed, it will be logged when the app is open." };
  }
}

async function flush() {
  const { pending = [] } = await chrome.storage.local.get("pending");
  if (!pending.length) return;
  const left = [];
  for (const m of pending) {
    try { await fetch(API + "/applied", { method: "POST", body: JSON.stringify(m) }); } catch (e) { left.push(m); }
  }
  await chrome.storage.local.set({ pending: left });
}

// ---------- cover letters ----------
function pickTemplate(T, title, text) {
  const t = ` ${(title || "").toLowerCase()} `, d = (text || "").toLowerCase();
  let best = T.product_manager ? "product_manager" : Object.keys(T)[0], top = 0;
  for (const [k, v] of Object.entries(T)) {
    let n = 0;
    for (const m of v.match || []) n += 3 * (t.includes(m) ? 1 : 0) + Math.min(d.split(m).length - 1, 3);
    if (n > top) { best = k; top = n; }
  }
  return best;
}
function offlineCover(P, job) {
  const C = (P && P.cover) || {}, T = C.templates || {};
  if (!Object.keys(T).length) return null;
  const key = pickTemplate(T, job.role, job.text);
  const co = (job.company || "your company").trim();
  const role = (job.role || "").replace(/\s*[-–|(].*$/, "").trim() || "open";
  const hook = (C.default_hook || "").replaceAll("{company}", co);
  const name = P.fields ? `${P.fields.first_name || ""} ${P.fields.last_name || ""}`.trim() : "";
  let text = T[key].body || "";
  const date = new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" });
  for (const [k, v] of Object.entries({ "{role}": role, "{company}": co, "{hook}": hook, "{name}": name, "{date}": date }))
    text = text.replaceAll(k, v);
  return { text: text.replace(/[ \t]+\n/g, "\n").trim(), template: key, label: T[key].label || key, offline: true };
}
async function cover(job) {
  try {
    const q = new URLSearchParams({ url: job.url || "", title: job.title || "", company: job.company || "" });
    const r = await fetch(`${APP}/api/copilot/cover?${q}`);
    if (r.ok) { const c = await r.json(); if (c && c.text) return c; }
  } catch (e) {}
  return offlineCover(await profile(), job);
}

// ---------- tabs opened from the Batch apply page ----------
async function openJob(msg) {
  const tab = await chrome.tabs.create({ url: msg.url, active: false });
  const { tabs = {} } = await chrome.storage.local.get("tabs");
  tabs[tab.id] = msg.id;
  await chrome.storage.local.set({ tabs });
}
chrome.tabs.onRemoved.addListener(async tabId => {
  const { tabs = {} } = await chrome.storage.local.get("tabs");
  const id = tabs[tabId];
  if (!id) return;
  delete tabs[tabId];
  await chrome.storage.local.set({ tabs });
  try { await fetch(API + "/closed", { method: "POST", body: JSON.stringify({ id }) }); } catch (e) {}
});

// ---------- referrals + LinkedIn posts (see /network in the app) ----------
async function post(path, body) {
  const r = await fetch(APP + path, { method: "POST", body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.error) throw new Error(j.error || `Jobber said ${r.status}`);
  return j;
}
async function signal(msg) {
  try { await post("/api/network/signal", msg); } catch (e) {
    const { signals = [] } = await chrome.storage.local.get("signals");  // app closed: send later
    signals.push(msg);
    await chrome.storage.local.set({ signals: signals.slice(-200) });
  }
}
async function flushSignals() {
  const { signals = [] } = await chrome.storage.local.get("signals");
  if (!signals.length) return;
  const left = [];
  for (const m of signals) { try { await post("/api/network/signal", m); } catch (e) { left.push(m); } }
  await chrome.storage.local.set({ signals: left });
}
async function savePost(tabId) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func: readPost });
  const p = r && r.result;
  if (!p || (p.text || "").length < 20) return { error: "Couldn't find a post here. Select the post's text, then try again." };
  try {
    const res = await post("/api/network/post", { ...p, via: "extension", status: "saved" });
    await status(`Post: ${res.kept} job${res.kept === 1 ? "" : "s"} saved${res.poster ? " from " + res.poster : ""}`);
    return res;
  } catch (e) {
    return { error: /fetch/i.test(e.message) ? "Open the Jobber app first (it must be running to read the links)." : e.message };
  }
}
// Runs inside the page: the selected text, or the LinkedIn post nearest the middle of the screen.
function readPost() {
  const onLI = /(^|\.)linkedin\.com$/.test(location.hostname);
  const anchors = root => [...root.querySelectorAll("a[href]")].map(a => ({ url: a.href, text: (a.innerText || "").trim().slice(0, 200) }));
  const POST = '[data-urn*="urn:li:activity"], [data-id*="urn:li:activity"], .feed-shared-update-v2, article';
  let text = "", links = [], post = null;
  const sel = getSelection();
  if (sel && String(sel).trim().length > 30) {
    text = String(sel);
    const n = sel.getRangeAt(0).commonAncestorContainer, box = n.nodeType === 1 ? n : n.parentElement;
    links = anchors(box).filter((l, i) => sel.containsNode(box.querySelectorAll("a[href]")[i], true));
    post = onLI ? box.closest(POST) : null;
  } else if (onLI) {
    const mid = innerHeight / 2;
    const dist = e => { const r = e.getBoundingClientRect(); return r.top <= mid && r.bottom >= mid ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid)); };
    const all = [...document.querySelectorAll(POST)].filter(e => e.offsetHeight > 80);
    post = all.sort((a, b) => dist(a) - dist(b) || a.offsetHeight - b.offsetHeight)[0] || null;
    if (post) {
      const body = post.querySelector(".update-components-text, .feed-shared-update-v2__description, .feed-shared-inline-show-more-text, .feed-shared-text, .attributed-text-segment-list__content") || post;
      text = body.innerText;
      links = anchors(body).concat(anchors(post).filter(l => !/linkedin\.com\/(in|company|feed\/hashtag|search)/.test(l.url)));
    }
  } else {
    const main = document.querySelector("main, article") || document.body;
    text = main.innerText;
    links = anchors(main);
  }
  let url = location.href.split("?")[0], poster = "", poster_url = "";
  if (post) {
    const urn = (post.getAttribute("data-urn") || post.getAttribute("data-id") || "").match(/urn:li:(activity|share|ugcPost):\d+/);
    if (urn) url = `https://www.linkedin.com/feed/update/${urn[0]}/`;
    const actor = post.querySelector('.update-components-actor__title span[aria-hidden="true"], .update-components-actor__name span[aria-hidden="true"], .update-components-actor__title, .feed-shared-actor__name');
    poster = actor ? actor.innerText.trim().split("\n")[0] : "";
    const a = post.querySelector('a.update-components-actor__meta-link[href*="/in/"], .update-components-actor__container a[href*="/in/"], a[href*="linkedin.com/in/"]');
    poster_url = a ? a.href.split("?")[0] : "";
  }
  return { text: text.slice(0, 15000), links: links.slice(0, 150), url: onLI ? url : location.href, poster, poster_url };
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "signal") { signal(msg); return; }
  if (msg.type === "savePost") { savePost(msg.tabId).then(reply); return true; }
  if (msg.type === "open") { openJob(msg); return; }
  if (msg.type === "profile") { flush(); flushSignals(); profile(msg.force).then(reply); return true; }
  if (msg.type === "cover") { cover(msg.job || {}).then(reply); return true; }
  if (msg.type === "status") { status(msg.text); return; }
  if (msg.type === "fill") { fillTab(msg.tabId); return; }
  if (msg.type === "applied") {
    chrome.storage.local.get("tabs").then(({ tabs = {} }) => {
      msg.id = sender.tab ? tabs[sender.tab.id] : undefined;
      applied(msg).then(reply);
    });
    return true;
  }
});

// Popup "Fill this page" / Alt+Shift+F: fill the current page, on any site
async function fillTab(tabId) {
  if (!tabId) return;
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func: () => { window.__jhForce = true; } });
  await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, files: ["content.js"] });
}
chrome.commands.onCommand.addListener(async c => {
  if (c === "fill") { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); fillTab(t && t.id); }
});
chrome.runtime.onInstalled.addListener(() => profile(true));
