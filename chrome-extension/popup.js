const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const ago = t => { const m = Math.round((Date.now() - t) / 60000); return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`; };

chrome.runtime.sendMessage({ type: "profile", force: true }, P => {
  if (!P || !P.fields) { $("who").textContent = "No profile found. Open the Jobber app once."; return; }
  const f = P.fields, app = P.source === "app";
  $("who").innerHTML = `<b>${esc(f.full_name || `${f.first_name} ${f.last_name}`)}</b>
    <div class="sub">${esc(f.email)} · ${esc(f.phone)}</div>
    <div class="sub">${esc(f.current_title)}, ${esc(f.current_company)} · ${esc(f.location)}</div>
    <div class="sub">Notice: ${esc(f.notice_period)} · Expected: ${esc(f.expected_ctc)} · CV: ${P.resume && P.resume.b64 ? "attached" : "missing"}</div>
    <div style="margin-top:6px"><span class="pill ${app ? "on" : "off"}">${app ? "App connected" : "Offline: " + esc(P.source || "copy")}</span>
    <span class="sub">${Object.keys(P.answers || {}).length} saved answers</span></div>`;
});
chrome.storage.local.get("last").then(({ last }) => { if (last) $("last").textContent = `Last: ${last.text} (${ago(last.at)})`; });

async function tab() { const [t] = await chrome.tabs.query({ active: true, currentWindow: true }); return t; }

$("fill").onclick = async () => {
  const t = await tab();
  if (!t || !/^https?:/.test(t.url || "")) { $("msg").textContent = "Open a job application page first."; return; }
  chrome.runtime.sendMessage({ type: "fill", tabId: t.id });
  $("msg").textContent = "Filling… check the orange fields, then press the site's Submit.";
};

$("cover").onclick = async () => {
  const t = await tab();
  let job = { url: t && t.url, title: t && t.title, role: t && t.title };
  try {
    const [r] = await chrome.scripting.executeScript({ target: { tabId: t.id }, func: () => window.__jh && window.__jh.jobInfo ? window.__jh.jobInfo() : null });
    if (r && r.result) job = r.result;
  } catch (e) {}
  chrome.runtime.sendMessage({ type: "cover", job }, async c => {
    if (!c || !c.text) { $("msg").textContent = "No cover letter templates found."; return; }
    await navigator.clipboard.writeText(c.text);
    $("msg").textContent = `Copied (${c.label || c.template}${c.offline ? ", offline template" : ""}). Paste it with Ctrl+V.`;
  });
};
