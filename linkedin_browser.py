"""LinkedIn via a real, LOGGED-OUT browser (Chrome or Edge driven by Playwright).

No LinkedIn account is used, so nothing can be flagged on your profile. It reads the public
job-search pages anyone can see without signing in, slowly, newest jobs first. LinkedIn still
limits logged-out traffic per internet connection; when it starts blocking, we stop for this run.

Test on its own:  .venv\\Scripts\\python linkedin_browser.py
"""
from __future__ import annotations

import random
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus

PROFILE = Path(__file__).with_name(".li_guest")  # cookies of a guest visitor only; never signed in


def _ctx(p, headless=False):
    kw = dict(headless=headless, args=[] if headless else ["--start-minimized"],
              viewport={"width": 1280, "height": 900})
    for channel in ("chrome", "msedge", None):
        try:
            return p.chromium.launch_persistent_context(str(PROFILE), channel=channel, **kw) if channel \
                else p.chromium.launch_persistent_context(str(PROFILE), **kw)
        except Exception:
            continue
    raise RuntimeError("No Edge/Chrome found for LinkedIn")


def _blocked(page) -> bool:
    u = page.url
    if any(x in u for x in ("/authwall", "/checkpoint", "/login", "/uas/")):
        return True
    try:
        t = page.title().lower()
    except Exception:
        t = ""
    return "429" in t or "too many requests" in t or "sign in" in t and "jobs" not in t


def _pause(a=3.0, b=6.0):
    time.sleep(a + random.random() * (b - a))


def _posted(card_time: str | None, text: str) -> str | None:
    if card_time and re.match(r"\d{4}-\d{2}-\d{2}", card_time):
        return card_time[:10]
    m = re.search(r"(\d+)\s+(minute|hour|day|week|month)s?\s+ago", text or "", re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"minute": 0, "hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
    return (date.today() - timedelta(days=days)).isoformat()


# Public (logged-out) search cards use "base-search-card" markup; signed-in style kept as fallback.
CARDS_JS = r"""
() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/jobs/view/"]')) {
    const m = a.href.match(/\/jobs\/view\/(?:[^/?]*-)?(\d{6,})/); if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const card = a.closest('li') || a.closest('.base-card') || a.parentElement;
    const q = s => { const e = card.querySelector(s); return e ? e.innerText.trim() : ''; };
    const lines = card.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    const title = (q('.base-search-card__title') || a.getAttribute('aria-label') || a.innerText || lines[0] || '')
                    .split('\n')[0].replace(/ with verification$/i, '').trim();
    const rest = lines.filter(l => l !== title);
    const t = card.querySelector('time');
    out.push({ id: m[1], title,
      company: q('.base-search-card__subtitle') || q('.artdeco-entity-lockup__subtitle') || rest[0] || '',
      location: q('.job-search-card__location') || q('.artdeco-entity-lockup__caption') || rest[1] || '',
      salary: q('.job-search-card__salary-info'),
      time: t ? (t.getAttribute('datetime') || '') : '', text: card.innerText });
  }
  return out;
}
"""

DETAIL_JS = r"""
() => {
  const pick = sels => { for (const s of sels) { const e = document.querySelector(s); if (e && e.innerText.trim()) return e.innerText.trim(); } return ''; };
  return {
    desc: pick(['.show-more-less-html__markup', '.description__text', '.jobs-description__content', '#job-details']),
    criteria: pick(['.description__job-criteria-list']),
    salary: pick(['.compensation__salary', '.salary']),
  };
}
"""


def fetch(cfg, known_urls: set[str] | None = None, log=print) -> list[dict]:
    """Option "linkedin_headless": true runs the browser without a window. LinkedIn sends hidden
    browsers to its sign-in wall more often; if that happens before any job is found, we retry once
    with the normal (minimised) window. We never disguise the browser."""
    if cfg.get("linkedin_headless"):
        jobs, blocked = _fetch(cfg, known_urls, log, headless=True)
        if jobs or not blocked:
            return jobs
        log("  LinkedIn blocked the hidden browser; retrying with a normal window...")
    return _fetch(cfg, known_urls, log, headless=False)[0]


def _fetch(cfg, known_urls, log, headless=False) -> tuple[list[dict], bool]:
    from playwright.sync_api import sync_playwright
    from sources import job

    known_urls = known_urls or set()
    from sources import queries
    q = " OR ".join(f'"{t}"' for t in queries(cfg))
    locs = cfg.get("linkedin_locations") or [loc["search"] for loc in cfg["locations"] if loc.get("search")]
    remote_where = cfg.get("country") or "Worldwide"
    scrolls = cfg.get("linkedin_scrolls", 3)          # each "see more" = one more request
    max_details = cfg.get("linkedin_max_details", 25)
    windows = [cfg.get("recent_hours", 72) * 3600, cfg["max_age_days"] * 86400]  # newest first
    found: dict[str, dict] = {}
    blocked = False

    with sync_playwright() as p:
        ctx = _ctx(p, headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            searches = [(loc, False, w) for w in windows for loc in locs] + ([(remote_where, True, windows[0])] if cfg["remote"].get("enabled") else [])
            for loc, remote, secs in searches:
                url = (f"https://www.linkedin.com/jobs/search?keywords={quote_plus(q)}&location={quote_plus(loc)}"
                       f"&f_TPR=r{secs}&sortBy=DD&position=1&pageNum=0" + ("&f_WT=2" if remote else ""))
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                _pause(3, 6)
                if _blocked(page):
                    log("  LinkedIn is limiting logged-out visits from your connection; stopping LinkedIn for this run.")
                    blocked = True
                    break
                try:
                    page.keyboard.press("Escape")  # close the "sign in" pop-up if shown
                except Exception:
                    pass
                for _ in range(scrolls):
                    page.mouse.wheel(0, 6000)
                    _pause(2.5, 4.5)
                    try:
                        page.click("button.infinite-scroller__show-more-button", timeout=1500)
                        _pause(2, 4)
                    except Exception:
                        pass
                cards = page.evaluate(CARDS_JS)
                if not cards:  # sometimes the first load comes back empty; one slow retry
                    _pause(10, 15)
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    _pause(4, 7)
                    if _blocked(page):
                        log("  LinkedIn is limiting logged-out visits from your connection; stopping LinkedIn for this run.")
                        blocked = True
                        break
                    page.mouse.wheel(0, 6000)
                    _pause(2.5, 4.5)
                    cards = page.evaluate(CARDS_JS)
                new = 0
                for c in cards:
                    if c["id"] not in found and c["title"]:
                        found[c["id"]] = dict(c, remote=remote)
                        new += 1
                label = "remote" if remote else loc.split(",")[0]
                log(f"  linkedin {label} (last {max(1, secs // 86400)}d): {len(cards)} jobs, {new} new")
                _pause(8, 15)

            # Details (description, experience, salary) for the newest jobs not stored yet
            todo = [c for c in found.values() if f"https://www.linkedin.com/jobs/view/{c['id']}/" not in known_urls]
            todo.sort(key=lambda c: _posted(c["time"], c["text"]) or "", reverse=True)
            done = 0
            for c in ([] if blocked else todo[:max_details]):
                page.goto(f"https://www.linkedin.com/jobs/view/{c['id']}/", wait_until="domcontentloaded", timeout=45000)
                _pause(3, 6)
                if _blocked(page):
                    log("  LinkedIn started limiting job pages; keeping what we have.")
                    break
                d = page.evaluate(DETAIL_JS)
                c.update(desc=d["desc"], criteria=d["criteria"], salary=c.get("salary") or d["salary"])
                done += 1
            log(f"  linkedin: {len(found)} jobs, full details for {done}")
        finally:
            ctx.close()

    out = []
    for c in found.values():
        desc = (c.get("desc") or "")
        if c.get("criteria"):
            desc = f"{desc}\n\n{c['criteria']}"
        out.append(job(source="linkedin", ext_id=c["id"], title=c["title"], company=c["company"],
                       location=c["location"] + (" (Remote)" if c["remote"] else ""),
                       remote=c["remote"] or "remote" in c["location"].lower(),
                       url=f"https://www.linkedin.com/jobs/view/{c['id']}/",
                       posted=_posted(c["time"], c["text"]),
                       salary_text=c.get("salary") or "", description=desc[:6000]))
    return out, blocked


if __name__ == "__main__":
    import json
    import settings
    cfg = settings.load_cfg()
    jobs = fetch(cfg)
    print(len(jobs), "jobs")
    for j in jobs[:10]:
        print(j["posted"], "|", j["title"], "|", j["company"], "|", j["location"], "| desc", len(j["description"]))
