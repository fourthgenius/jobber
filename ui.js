/* Jobber shared UI: app bar + mobile tab bar, glossary tooltips, help drawer, toasts, empty states.
   Served by app.py at /ui.js. Include it right after <header class="appbar" data-page="..."> on every page. */
(function () {
  "use strict";
  const svg = (p, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${p}</svg>`;
  const ICONS = {
    jobs: svg('<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/>'),
    apply: svg('<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>'),
    copilot: svg('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>'),
    prep: svg('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/><path d="M9 10h6M9 13.5h4"/>'),
    setup: svg('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>'),
    help: svg('<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M12 17h.01"/>'),
    info: svg('<path d="M12 11v6M12 7.5h.01"/>', 'stroke-width="3"'),
    bulb: svg('<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/>'),
    shield: svg('<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>'),
    search: svg('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
    inbox: svg('<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/>'),
    star: svg('<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>'),
    calendar: svg('<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>'),
    chat: svg('<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>'),
    chart: svg('<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>'),
    book: svg('<path d="M4 19.5V5a2 2 0 0 1 2-2h14v16H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M8 7h8M8 11h6"/>'),
    plug: svg('<path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0zM12 17v5"/>'),
    check: svg('<path d="M20 6 9 17l-5-5"/>'),
    send: svg('<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>'),
    clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    user: svg('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'),
    file: svg('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>'),
    mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>'),
    radar: svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12 19 5"/>'),
    x: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
    network: svg('<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17.5" cy="9.5" r="2.5"/><path d="M16 14.6a5 5 0 0 1 6 4.9"/>'),
    gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>'),
    more: svg('<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>'),
    filter: svg('<path d="M3 5h18l-7 8.5V19l-4 2v-7.5z"/>'),
    chev: svg('<path d="m6 9 6 6 6-6"/>'),
    arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  };
  const MARK = `<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M14.5 4v9.5a4.5 4.5 0 0 1-8.6 1.9" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/><circle cx="18.5" cy="6.5" r="2.5" fill="#5fe0bd"/></svg>`;
  const NAV = [["jobs", "/", "Jobs"], ["apply", "/apply", "Batch apply"], ["copilot", "/copilot", "Copilot"], ["prep", "/prep", "Interview prep"], ["network", "/network", "Referrals"]];
  const SHORT = {apply: "Apply", prep: "Prep", network: "Referrals"};

  /* Plain-words glossary. Keys are used by data-term="..." anywhere in the app. */
  const GLOSS = {
    referral: ["Referral", "Someone who works at the company passes your CV to the hiring team. Referred candidates are far more likely to get an interview. Jobs where you know someone rank higher in your list."],
    posts: ["LinkedIn hiring posts", "Many roles are only announced in posts: recruiters writing \"we're hiring\", or people sharing lists of job links. Jobber finds public posts through a web search, or you paste one, and it pulls out the jobs. No LinkedIn login is used."],
    fit: ["CV fit score", "How closely a job matches what you told Setup: your target titles, skills, experience and pay. 70%+ is a strong match, 50-69% is worth a look, below 50% is a long shot."],
    aifit: ["AI fit", "A second opinion from Claude, which reads the full job post against your CV. Only shows when you add an API key."],
    ats: ["ATS (applicant tracking system)", "The software companies use to collect applications, e.g. Greenhouse, Lever, Workday. Many ATSs scan your CV for keywords from the job post, so matching words matters."],
    direct: ["Direct company apply", "The job is on the company's own careers portal instead of a job board. These forms are usually shorter and Batch apply can prefill them."],
    floor: ["Pay floor", "The lowest yearly pay you'd accept. Jobs that list less are hidden by default (not deleted). Jobs that don't list pay are kept."],
    below: ["Below the bar", "Jobs that list pay under your floor or ask for more experience than you set. They're hidden by default; pick \"Show all\" to see them."],
    lpa: ["LPA", "Lakhs per annum: Indian salary shorthand. 25 LPA = ₹25,00,000 a year."],
    batch: ["Batch apply", "Pick several jobs, and Jobber opens them a few at a time with the forms already filled in. You check each one and press Submit yourself."],
    prefill: ["Prefill", "The Jobber Chrome extension types your details, attaches your CV and pastes a cover letter into the form. It never presses Submit."],
    extension: ["Chrome extension", "A small add-on for Google Chrome that comes with Jobber. It fills application forms on your screen. Setup takes 2 minutes: Copilot → Setup."],
    quick: ["Quick forms", "Greenhouse, Lever, Ashby and similar portals. One page, no account needed, fastest to apply."],
    workday: ["Workday", "A common big-company portal. It usually asks you to create an account and has several pages, so it takes longer."],
    pacing: ["Pacing", "Some portals block you if you apply too fast, so Jobber spaces those out (e.g. 3 per 30 minutes on Lever)."],
    never: ["You always press Submit", "Jobber never sends an application or a message for you. It fills and drafts; you review and send."],
    search_terms: ["Search terms", "The exact words Jobber types into job sites. Each term is a separate search, so 3-6 focused terms work best."],
    tiers: ["Title lists", "How Jobber decides if a job title is relevant. Best-fit titles score highest, stretch titles lowest. A job must match at least one list to be kept."],
    exclude: ["Skip titles", "Any job whose title contains one of these words is dropped, e.g. \"intern\" or \"director\"."],
    boost: ["Boost", "Words that push a job up your list when they appear in its description, with a weight from 1 (a bit) to 3 (a lot)."],
    remote_regions: ["Remote regions", "Remote jobs often say where you must live. Jobber keeps ones that mention your allowed regions and skips ones that say e.g. \"US only\"."],
    sources: ["Job sources", "The sites Jobber searches. All read public listings only; no account or login is used anywhere."],
    star: ["STAR story", "A way to tell a work story in interviews: Situation, Task, Action, Result. About two minutes spoken, with real numbers."],
    competency: ["Competency", "A skill interviewers test, like leadership or handling conflict. Aim for at least one story per competency."],
    evergreen: ["Long-running post", "This job has been listed for 30+ days. It may be a permanent opening or an old post nobody closed."],
    watchlist: ["Watchlist", "Companies you'd love to work at. Their jobs get a ★ and jump to the top of your queue and alerts."],
    outreach: ["Outreach", "Short notes to people at the company (a recruiter, hiring manager or alum) asking for a referral or a quick chat. Referrals get far more replies than cold applications."],
    followup: ["Follow-up", "A short, polite check-in a week or so after applying. It often moves an application forward."],
    hook: ["Why-this-company line", "One specific sentence on why you want this company. It makes a template cover letter feel personal."],
    gap: ["Keyword gap", "Words from the job post that aren't in your CV. Add the ones that are true for you, since ATS filters look for them."],
    interview_rate: ["Interview rate", "Interviews divided by applications. 5-10% is typical; higher means your targeting and CV are working."],
    brief: ["Company brief", "A one-page summary of the company and role, plus research links, so you walk in prepared."],
    mock: ["Mock practice", "Answer a likely interview question out loud against a timer, then check your answer against a short list."],
    queue: ["Apply-today queue", "Fresh jobs above your fit bar that you haven't applied to, best first. Applying in the first 48 hours gets the most replies."],
  };
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  /* ---------- App bar + tab bar ---------- */
  const bar = document.querySelector("header.appbar[data-page]");
  if (bar) {
    const page = bar.dataset.page, w = bar.querySelector(".wrap") || bar;
    const links = (short) => NAV.map(([k, href, l]) => `<a href="${href}" class="${k === page ? "on" : ""}" ${k === page ? 'aria-current="page"' : ""}>${ICONS[k]}<span>${short && SHORT[k] || l}</span></a>`).join("");
    w.insertAdjacentHTML("afterbegin", `<a class="brand" href="/" aria-label="Jobber home"><span class="mark">${MARK}</span><span>Job<i>ber</i></span></a><nav class="nav" aria-label="Main">${links()}</nav>`);
    w.insertAdjacentHTML("beforeend", `<a class="helpbtn${page === "setup" ? " on" : ""}" href="/setup" id="jb-setup" aria-label="Setup: your search, pay and CV" data-tip="Setup: your search, pay and CV"${page === "setup" ? ' aria-current="page"' : ""}>${ICONS.gear}</a><button type="button" class="helpbtn" id="jb-help" aria-label="Help and glossary" data-tip="Help: how Jobber works, and what the words mean">${ICONS.help}</button>`);
    document.addEventListener("DOMContentLoaded", () => document.body.insertAdjacentHTML("beforeend", `<nav class="tabbar" aria-label="Main">${links(true)}</nav>`));
  }

  /* ---------- Tooltips ---------- */
  let tipEl = null, tipFor = null;
  function tipNode() { if (!tipEl) { tipEl = document.createElement("div"); tipEl.id = "jb-tip"; tipEl.setAttribute("role", "tooltip"); document.body.appendChild(tipEl); } return tipEl; }
  function showTip(el) {
    const term = el.dataset.term, g = term && GLOSS[term];
    const html = g ? `<b>${esc(g[0])}</b>${esc(g[1])}` : esc(el.dataset.tip || "");
    if (!html) return;
    const t = tipNode(); t.innerHTML = html; tipFor = el;
    el.setAttribute("aria-describedby", "jb-tip"); if (el.classList.contains("tipbtn")) el.setAttribute("aria-expanded", "true");
    const r = el.getBoundingClientRect(); t.style.left = "0px"; t.style.top = "0px"; t.classList.add("on");
    const tw = t.offsetWidth, th = t.offsetHeight, m = 10;
    let x = r.left + r.width / 2 - tw / 2; x = Math.max(m, Math.min(x, innerWidth - tw - m));
    let y = r.top - th - 8; if (y < 70) y = r.bottom + 8;
    t.style.left = x + "px"; t.style.top = y + "px";
  }
  function hideTip() {
    if (tipEl) tipEl.classList.remove("on");
    if (tipFor) { tipFor.removeAttribute("aria-describedby"); if (tipFor.classList.contains("tipbtn")) tipFor.setAttribute("aria-expanded", "false"); }
    tipFor = null;
  }
  const tipTarget = e => e.target.closest && e.target.closest("[data-term],[data-tip]");
  document.addEventListener("pointerover", e => { if (e.pointerType === "touch") return; const el = tipTarget(e); if (el && el !== tipFor) showTip(el); });
  document.addEventListener("pointerout", e => { const el = tipTarget(e); if (el && el === tipFor && !el.contains(e.relatedTarget)) hideTip(); });
  document.addEventListener("focusin", e => { const el = tipTarget(e); if (el) showTip(el); });
  document.addEventListener("focusout", e => { if (tipTarget(e) === tipFor) hideTip(); });
  document.addEventListener("click", e => {
    const b = e.target.closest && e.target.closest(".tipbtn");
    if (b) { e.preventDefault(); e.stopPropagation(); if (tipFor !== b) showTip(b); return; }
    if (tipFor && tipFor.classList.contains("tipbtn")) hideTip();
  }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape") { hideTip(); closeHelp(); } });
  addEventListener("scroll", hideTip, {passive: true, capture: true});

  /* Fill in any <button class="tipbtn" data-term="..."></button> written in static HTML. */
  function fillTips(root) {
    (root.querySelectorAll ? root : document).querySelectorAll(".tipbtn:empty").forEach(b => {
      const g = GLOSS[b.dataset.term]; b.type = "button"; b.innerHTML = ICONS.info;
      b.setAttribute("aria-label", g ? `What is "${g[0]}"?` : "More info"); b.setAttribute("aria-expanded", "false");
    });
  }
  document.addEventListener("DOMContentLoaded", () => {
    fillTips(document);
    new MutationObserver(ms => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1) fillTips(n); }).observe(document.body, {childList: true, subtree: true});
  });

  /* ---------- Overflow menus ----------
     <details class="mnu"><summary class="btn ghost icon" aria-label="More">…</summary><div class="mnu-list"><button>…</button></div></details>
     Jobber.menu(items, {label, icon, cls}) builds one: items = [{label, icon?, attrs?, href?, danger?} | "-"]. */
  function menu(items, o = {}) {
    const it = items.map(i => i === "-" ? '<hr>' : i.href
      ? `<a href="${esc(i.href)}" target="${i.self ? "_self" : "_blank"}" rel="noopener" ${i.attrs || ""}>${i.icon ? ICONS[i.icon] || "" : ""}<span>${esc(i.label)}</span></a>`
      : `<button type="button" class="${i.danger ? "danger" : ""}" ${i.attrs || ""}>${i.icon ? ICONS[i.icon] || "" : ""}<span>${esc(i.label)}</span></button>`).join("");
    return `<details class="mnu ${o.cls || ""}"><summary class="${o.btn || "btn ghost icon sm"}" aria-label="${esc(o.label || "More options")}">${o.text ? `${o.icon === false ? "" : ICONS[o.icon || "more"]}<span>${esc(o.text)}</span>` : ICONS[o.icon || "more"]}</summary><div class="mnu-list" role="menu">${it}</div></details>`;
  }
  const closeMenus = keep => document.querySelectorAll("details.mnu[open],details.pop[open]").forEach(d => { if (d !== keep && !d.contains(keep)) d.open = false; });
  document.addEventListener("click", e => {
    const d = e.target.closest && e.target.closest("details.mnu,details.pop");
    closeMenus(d);
    if (d && d.classList.contains("mnu") && e.target.closest(".mnu-list button,.mnu-list a")) setTimeout(() => d.open = false, 0);
  });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenus(null); });

  /* ---------- Help drawer ---------- */
  let helpEl = null;
  function openHelp() {
    if (helpEl) return;
    helpEl = document.createElement("div"); helpEl.className = "jb-scrim";
    helpEl.innerHTML = `<div class="jb-sheet" role="dialog" aria-modal="true" aria-labelledby="jb-help-h">
      <div style="display:flex;align-items:center;gap:10px"><h2 id="jb-help-h" class="grow">How Jobber works</h2><button class="btn ghost sm" data-close aria-label="Close help">${ICONS.x}</button></div>
      <p class="muted" style="margin:6px 0 16px">Four steps, at your own pace. Everything stays on your computer.</p>
      <ol class="jb-flow">
        <li><span class="n">1</span><div><b>Tell it what you want</b>Setup: roles, cities, pay, your CV. About 10 minutes, once.</div></li>
        <li><span class="n">2</span><div><b>Find jobs</b>Jobs → Find new jobs. Jobber searches job boards and company sites and ranks results by fit.</div></li>
        <li><span class="n">3</span><div><b>Apply faster</b>Save the ones you like, then use Batch apply or Copilot's Apply kit for cover letters and tailored CVs.</div></li>
        <li><span class="n">4</span><div><b>Follow up and prepare</b>Copilot reminds you to follow up; Interview prep gets you ready when someone replies.</div></li>
      </ol>
      <div class="jb-promise">${ICONS.shield}<span>Jobber never submits an application or sends a message for you. You always press the final button.</span></div>
      <h3>Words you'll see</h3>
      <dl>${Object.values(GLOSS).map(([t, d]) => `<dt>${esc(t)}</dt><dd>${esc(d)}</dd>`).join("")}</dl>
    </div>`;
    document.body.appendChild(helpEl);
    helpEl.addEventListener("click", e => { if (e.target === helpEl || e.target.closest("[data-close]")) closeHelp(); });
    helpEl.querySelector("[data-close]").focus();
  }
  function closeHelp() { if (helpEl) { helpEl.remove(); helpEl = null; const b = document.getElementById("jb-help"); b && b.focus(); } }
  document.addEventListener("click", e => { if (e.target.closest && e.target.closest("#jb-help")) openHelp(); });

  /* ---------- Toast ---------- */
  let toastT = null;
  function toast(msg, bad) {
    let t = document.querySelector(".jb-toast");
    if (!t) { t = document.createElement("div"); t.className = "jb-toast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.classList.toggle("bad", !!bad); t.textContent = msg; t.hidden = false;
    clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, 3200);
  }

  /* ---------- Empty state ---------- */
  function empty({icon = "inbox", title, body = "", steps = null, actions = ""}) {
    return `<div class="empty-state"><div class="ei">${ICONS[icon] || ICONS.inbox}</div><h3>${title}</h3>${body ? `<p>${body}</p>` : ""}${steps ? `<ol>${steps.map(s => `<li>${s}</li>`).join("")}</ol>` : ""}${actions ? `<div class="acts">${actions}</div>` : ""}</div>`;
  }
  const tip = term => `<button type="button" class="tipbtn" data-term="${term}" aria-label="What is &quot;${esc((GLOSS[term] || [term])[0])}&quot;?" aria-expanded="false">${ICONS.info}</button>`;

  window.Jobber = {menu, ICONS, GLOSS, tip, toast, empty, esc, openHelp};
})();
