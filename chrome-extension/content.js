// Jobber Autofill v2 (Chrome extension content script).
// Fills fields, attaches the CV, highlights what's left. NEVER clicks Submit.
(() => {
  if (window.__jh) { if (window.__jhForce && window.__jh.fill) window.__jh.fill(true); return; }
  window.__jh = {};
  chrome.runtime.sendMessage({ type: "profile" }, P => { if (P && P.fields) main(P); });
function main(P) {
  const host = location.hostname;
  if (/^(127\.0\.0\.1|localhost)$/.test(host)) return;
  const ATS = /(greenhouse\.io|lever\.co|ashbyhq\.com|myworkdayjobs\.com|myworkdaysite\.com|workday\.com|smartrecruiters\.com|workable\.com|recruitee\.com|keka\.com|darwinbox|freshteam|zohorecruit|bamboohr|icims\.com|teamtailor|breezy\.hr|jobvite|successfactors|taleo|rippling|personio)/i;
  const AUTO = ATS.test(host) || !!window.__jhForce;
  const S = { filled: 0, missing: 0, captcha: false, login: false, submitted: false, auto: AUTO, last: {} };
  const done = new WeakSet();
  window.__jh = S;
  S.fill = f => fillOnce(f);

  const F = P.fields || {};
  const norm = s => (s || "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

  // label regex -> profile key. Order matters.
  const RULES = [
    [/cover\s*letter|covering letter|motivation(al)? letter|letter of motivation/i, "cover_letter"],
    [/first\s*name|given name|firstname/i, "first_name"],
    [/last\s*name|surname|family name|lastname/i, "last_name"],
    [/preferred (first )?name/i, "first_name"],
    [/e-?mail/i, "email"],
    [/phone|mobile|contact number|whatsapp/i, "phone"],
    [/linked\s*in/i, "linkedin"],
    [/github/i, "github"],
    [/portfolio|personal (web)?site|website|other (link|url)/i, "website"],
    [/notice period|notice|how soon can you join|earliest (start|joining)|available to (start|join)|joining date/i, "notice_period"],
    [/expected (ctc|salary|compensation|pay|annual)|desired (salary|compensation|pay)|salary expectation|compensation expectation|expected package/i, "expected_ctc"],
    [/current (ctc|salary|compensation|annual|package)|present (ctc|salary)|last drawn|\bctc\b/i, "current_ctc"],
    [/years of (relevant |total |work |professional )?experience|total experience|experience in years|how many years/i, "years_experience"],
    [/university|college|school|institut/i, "school"],
    [/degree|qualification|highest education|discipline/i, "degree"],
    [/graduat|year of passing|passing year/i, "grad_year"],
    [/current (company|employer|organi[sz]ation)|(most )?recent (company|employer)|company name|^company\b|organi[sz]ation|employer/i, "current_company"],
    [/current (job )?(title|role|designation|position)|job title|designation|headline/i, "current_title"],
    [/relocat/i, "relocate"],
    [/sponsor/i, "needs_sponsorship"],
    [/authori[sz]ed|work permit|eligible to work|right to work|legally (able|permitted)/i, "work_authorized"],
    [/how did you (hear|find|learn)|where did you (hear|find|see)|hear about/i, "source"],
    [/gender|\bsex\b/i, "gender"],
    [/race|ethnic|veteran|disab|hispanic|latino|pronoun|sexual orientation|transgender/i, "eeo"],
    [/country/i, "country"],
    [/\bcity\b|current location|where are you (based|located)|location|address/i, "location"],
    [/full\s*name|legal name|your name|candidate name|^name\b|\bname\b/i, "full_name"],
  ];

  function txt(e) { return e ? (e.innerText || e.textContent || "") : ""; }
  function labelOf(el) {
    let t = "";
    if (el.id) { try { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = txt(l); } catch (e) {} }
    if (!t && el.getAttribute("aria-labelledby"))
      t = el.getAttribute("aria-labelledby").split(/\s+/).map(i => txt(document.getElementById(i))).join(" ");
    if (!t) t = el.getAttribute("aria-label") || "";
    if (!t) t = txt(el.closest("label"));
    if (!t) { const fs = el.closest("fieldset"); if (fs) t = txt(fs.querySelector("legend")); }
    if (!t) {
      let p = el.parentElement;
      for (let i = 0; i < 4 && p && !t; i++, p = p.parentElement) {
        const lab = p.querySelector("label, legend, .application-label, [class*=label], [class*=question], [class*=Label]");
        if (lab && !lab.contains(el)) t = txt(lab);
      }
    }
    t = t.replace(/\s+/g, " ").trim();
    const extra = [el.placeholder, el.name, el.getAttribute("data-automation-id")].filter(Boolean).join(" ");
    return { q: t.slice(0, 300), full: (t + " " + extra).trim() };
  }
  function keyFor(full, label) {
    const n = norm(full), l = norm(label);
    for (const [q, a] of Object.entries(P.answers || {})) if (q && (l === q || n === q || (q.length > 12 && n.includes(q)))) return { ans: a };
    for (const [re, k] of RULES) if (re.test(full)) return { key: k };
    return null;
  }
  function valueFor(m) { if (!m) return ""; if ("ans" in m) return m.ans; return F[m.key] || ""; }

  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && getComputedStyle(el).visibility !== "hidden";
  function setVal(el, v) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, { bubbles: true }));
  }
  const YES = /^(yes|y|true)$/i, NO = /^(no|n|false)$/i, DECLINE = /decline|prefer not|don.?t wish|not (to )?(say|disclose|answer|specify)|rather not/i;
  function best(texts, v) {
    const nv = norm(v);
    let i = texts.findIndex(t => norm(t) === nv);
    if (i < 0 && YES.test(v)) i = texts.findIndex(t => /^yes\b/i.test(t.trim()));
    if (i < 0 && NO.test(v)) i = texts.findIndex(t => /^no\b/i.test(t.trim()));
    if (i < 0 && DECLINE.test(v)) i = texts.findIndex(t => DECLINE.test(t));
    if (i < 0 && nv) i = texts.findIndex(t => norm(t) && norm(t).includes(nv));
    if (i < 0 && nv) i = texts.findIndex(t => norm(t).length > 2 && nv.includes(norm(t)));
    return i;
  }
  function attachResume(el) {
    if (!P.resume || !P.resume.b64 || (el.files && el.files.length)) return false;
    const bin = atob(P.resume.b64), u = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    const dt = new DataTransfer();
    dt.items.add(new File([u], P.resume.name, { type: "application/pdf" }));
    el.files = dt.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  const isRequired = (el, q) => el.required || el.getAttribute("aria-required") === "true" || /\*\s*$|\*\s|required/i.test(q);
  function mark(el, need) {
    el.style.outline = need ? "2px solid #e8a317" : "";
    el.style.outlineOffset = need ? "2px" : "";
  }

  function fillOnce(force) {
    let filled = 0, missing = 0;
    const snap = {};
    // text-like inputs, textareas, selects
    const els = document.querySelectorAll("input:not([type=hidden]):not([type=password]):not([type=submit]):not([type=button]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, select");
    for (const el of els) {
      if (!visible(el) || el.disabled || el.readOnly) continue;
      const { q, full } = labelOf(el);
      if (/search|captcha/i.test(full) && !/location/i.test(full)) continue;
      const m = keyFor(full, q);
      if (!done.has(el) && (AUTO || force)) {
        const v = valueFor(m);
        const empty = el.tagName === "SELECT" ? (!el.value || el.selectedIndex <= 0) : !el.value;
        if (v && empty) {
          if (el.tagName === "SELECT") {
            const opts = [...el.options];
            const i = best(opts.map(o => o.text), v);
            if (i > 0 || (i === 0 && opts[0].value)) { el.value = opts[i].value; el.dispatchEvent(new Event("change", { bubbles: true })); done.add(el); filled++; }
          } else if (el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete")) {
            setVal(el, v); done.add(el); filled++; pickOption(el, v);
          } else if (!(m && m.key === "cover_letter")) {
            setVal(el, v); done.add(el); filled++;
          }
        } else if (m && m.key === "cover_letter" && !el.value && el.tagName === "TEXTAREA") {
          done.add(el); fillCover(el);
        }
      }
      const val = el.tagName === "SELECT" ? (el.selectedIndex > 0 ? el.options[el.selectedIndex].text : "") : el.value;
      if (val && q && !(m && m.key)) snap[norm(q)] = val;   // custom question you answered -> learn
      const need = !val && isRequired(el, q);
      mark(el, need); if (need) missing++;
    }
    // radio groups
    const groups = {};
    document.querySelectorAll("input[type=radio]").forEach(r => { if (visible(r) || visible(r.parentElement)) (groups[r.name || r.id] ||= []).push(r); });
    for (const rs of Object.values(groups)) {
      const box = rs[0].closest("fieldset, [role=radiogroup], .field, [class*=question], [class*=Field]") || rs[0].parentElement.parentElement;
      const qEl = box && (box.querySelector("legend, label:not(:has(input)), [class*=label], [class*=question]"));
      const q = txt(qEl).replace(/\s+/g, " ").trim().slice(0, 300) || (box && box.getAttribute("aria-label")) || "";
      const opts = rs.map(r => labelOf(r).q || r.value);
      const m = keyFor(q);
      const v = valueFor(m);
      const chosen = rs.find(r => r.checked);
      if (!chosen && v && (AUTO || force) && !done.has(rs[0])) {
        const i = best(opts, v);
        if (i >= 0) { rs[i].click(); done.add(rs[0]); filled++; }
      }
      const now = rs.find(r => r.checked);
      if (now && q && !(m && m.key)) snap[norm(q)] = opts[rs.indexOf(now)];
      if (!now && rs.some(r => r.required)) missing++;
    }
    // Yes/No button pairs (Ashby style)
    document.querySelectorAll("button[type=button]").forEach(b => {
      if (!/^yes$/i.test(txt(b).trim()) || done.has(b) || !(AUTO || force)) return;
      const box = b.parentElement && b.parentElement.parentElement;
      const no = box && [...box.querySelectorAll("button")].find(x => /^no$/i.test(txt(x).trim()));
      if (!no) return;
      const q = txt(box.querySelector("label, [class*=label], [class*=question]")) || txt(box.parentElement.querySelector("label"));
      const v = valueFor(keyFor(q));
      if (YES.test(v)) { b.click(); done.add(b); filled++; } else if (NO.test(v)) { no.click(); done.add(b); filled++; }
    });
    // resume upload
    const files = [...document.querySelectorAll("input[type=file]")];
    const res = files.find(f => /resume|cv\b|curriculum/i.test(labelOf(f).full)) || (files.length && !/cover/i.test(labelOf(files[0]).full) ? files[0] : null);
    if (res && (AUTO || force) && !done.has(res)) { if (attachResume(res)) filled++; done.add(res); }

    S.filled += filled; S.missing = missing;
    if (filled && !S.pinged) { S.pinged = true; chrome.runtime.sendMessage({ type: "status", text: `Filled ${S.filled} fields on ${host}` }); }
    Object.assign(S.last, snap);
    S.captcha = !!document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile');
    S.login = !!document.querySelector("input[type=password]");
    const body = (document.body && document.body.innerText || "").slice(0, 20000);
    S.submitted = /thank(s| you) for (applying|your application|your interest)|application (has been |was )?(submitted|received|sent)|we('ve| have) received your application|successfully (applied|submitted)/i.test(body)
      || /\/(confirmation|thank-?you|thanks|success)(\b|\/|$)/i.test(location.pathname);
    report();
    badge();
  }
  // Remember this form's URL + your own answers (survives the jump to the thank-you page)
  let reported = false;
  function report() {
    let mem = {};
    try { mem = JSON.parse(sessionStorage.getItem("jh_form") || "{}"); } catch (e) {}
    if (!S.submitted) {
      if (Object.keys(S.last).length || S.filled) {
        mem = { url: location.href, learned: { ...(mem.url === location.href ? mem.learned : {}), ...S.last } };
        try { sessionStorage.setItem("jh_form", JSON.stringify(mem)); } catch (e) {}
      }
      return;
    }
    if (reported) return;
    reported = true;
    chrome.runtime.sendMessage({ type: "applied", url: location.href, form_url: mem.url || document.referrer || "",
      learned: mem.learned || S.last, title: document.title, host }, r => { S.warn = r && r.warn; badge(); });
    try { sessionStorage.removeItem("jh_form"); } catch (e) {}
  }
  // Cover letter: from the Jobber app (role template for this job), or offline templates
  function jobInfo() {
    const seg = location.pathname.split("/").filter(Boolean);
    const slug = /greenhouse|lever|ashbyhq|workable|smartrecruiters|recruitee/.test(host) ? (seg[0] === "embed" ? "" : seg[0]) : "";
    const h1 = txt(document.querySelector("h1, h2, .posting-headline, [class*=title]")).trim().split("\n")[0];
    const meta = (document.querySelector('meta[property="og:site_name"]') || {}).content;
    let company = meta || (slug ? slug.replace(/[-_]+/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : host.replace(/^(www|careers|jobs)\./, "").split(".")[0]);
    return { url: location.href, title: document.title, role: h1 || document.title, company,
             text: (document.body ? document.body.innerText : "").slice(0, 6000) };
  }
  S.jobInfo = jobInfo;
  function fillCover(el) {
    chrome.runtime.sendMessage({ type: "cover", job: jobInfo() }, c => {
      if (c && c.text && !el.value) { setVal(el, c.text); S.filled++; S.coverNote = c.label || ""; badge(); }
    });
  }
  async function pickOption(el, v) {
    await new Promise(r => setTimeout(r, 700));
    const opts = [...document.querySelectorAll("[role=option], [class*=option]")].filter(visible);
    const i = best(opts.map(o => txt(o)), v);
    if (i >= 0) opts[i].click();
  }

  // Floating status badge (shadow DOM so the site's CSS can't break it)
  let root;
  function badge() {
    const fields = document.querySelectorAll("input:not([type=hidden]), textarea, select").length;
    if (fields < 3 && !S.submitted) return;
    if (!root) {
      const h = document.createElement("div");
      h.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647";
      root = h.attachShadow({ mode: "open" });
      document.documentElement.appendChild(h);
    }
    const msg = S.submitted ? "Submitted. Logged as applied in Jobber." + (S.warn ? `<br><b>${S.warn}</b>` : "")
      : !AUTO && !S.filled ? "Unknown site. Click Fill to autofill."
      : `${S.filled} filled · ${S.missing ? `<b>${S.missing} need you</b> (orange)` : "nothing missing"}${S.coverNote ? " · cover letter: " + S.coverNote : ""}${S.captcha ? " · CAPTCHA may appear" : ""}`;
    root.innerHTML = `<style>div{font:13px/1.4 system-ui,sans-serif;background:#1c1c1a;color:#fff;border-radius:10px;padding:10px 12px;box-shadow:0 4px 16px rgba(0,0,0,.3);max-width:300px}
      b{color:#f0c062}button{margin-left:8px;border:0;border-radius:6px;padding:3px 9px;background:#7d9cff;color:#0d1530;font-weight:600;cursor:pointer}small{display:block;opacity:.7;margin-top:4px}</style>
      <div>Jobber: ${msg}${S.submitted ? "" : `<button id=f>Fill</button><small>Review, then press the site's Submit yourself.</small>`}</div>`;
    const f = root.getElementById("f"); if (f) f.onclick = () => fillOnce(true);
  }
  if (!AUTO && !window.__jhForce) return;  // unknown site: only fill when you click the toolbar icon

  let t;
  const run = () => { clearTimeout(t); t = setTimeout(() => { try { fillOnce(false); } catch (e) { console.warn("JH", e); } }, 700); };
  const start = () => { run(); new MutationObserver(run).observe(document.documentElement, { childList: true, subtree: true }); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  document.addEventListener("change", run, true);   // capture what you type, for learning
  document.addEventListener("input", run, true);
}
})();
