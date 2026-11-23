// Jobber: referral boost on LinkedIn job pages.
// Reads the "N connections work here" / "N alumni work here" note LinkedIn already shows you on a job you
// opened, and tells your local Jobber app so jobs at that company rank higher. Read-only: it never clicks,
// scrolls, follows links or loads anything on LinkedIn.
(() => {
  if (window.__jbLinkedIn) return;
  window.__jbLinkedIn = true;
  const sent = new Set();
  const DETAIL = [".jobs-search__job-details--container", ".jobs-search__job-details", ".job-view-layout",
    ".jobs-details", ".scaffold-layout__detail", ".top-card-layout", ".decorated-job-posting__details"];
  const COMPANY = [".job-details-jobs-unified-top-card__company-name a", ".job-details-jobs-unified-top-card__company-name",
    ".jobs-unified-top-card__company-name a", ".jobs-unified-top-card__company-name", ".topcard__org-name-link",
    ".jobs-details-top-card__company-url"];
  const txt = el => (el && el.innerText || "").trim();

  function detailBox() {
    for (const s of DETAIL) { const e = document.querySelector(s); if (e && e.offsetHeight) return e; }
    return /\/jobs\/view\//.test(location.pathname) ? document.querySelector("main") : null;
  }
  function company(box) {
    for (const s of COMPANY) { const t = txt(box.querySelector(s) || document.querySelector(s)); if (t) return t.split("\n")[0]; }
    const m = document.title.match(/\|\s*([^|]+?)\s*\|\s*LinkedIn/);  // "Product Manager | Razorpay | LinkedIn"
    return m ? m[1] : "";
  }
  function count(t, rx) { const m = t.match(rx); return m ? parseInt(m[1].replace(/,/g, ""), 10) : 0; }

  function check() {
    if (!/\/jobs\//.test(location.pathname)) return;
    const box = detailBox();
    if (!box) return;
    const t = txt(box).slice(0, 20000);
    const connections = count(t, /(\d[\d,]*)\s+connections?\s+works?\s+here/i);
    const alumni = count(t, /(\d[\d,]*)\s+(?:school\s+|company\s+)?alumni\s+works?\s+here/i);
    if (!connections && !alumni) return;
    const co = company(box);
    if (!co) return;
    const key = `${co}|${connections}|${alumni}`;
    if (sent.has(key)) return;
    sent.add(key);
    try {
      chrome.runtime.sendMessage({ type: "signal", company: co, connections, alumni, url: location.href.split("?")[0] });
    } catch (e) { /* extension reloaded: ignore */ }
  }
  setInterval(check, 3000);
  check();
})();
