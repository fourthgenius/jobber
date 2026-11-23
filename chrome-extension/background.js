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

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg.type === "open") { openJob(msg); return; }
  if (msg.type === "profile") { flush(); profile(msg.force).then(reply); return true; }
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
