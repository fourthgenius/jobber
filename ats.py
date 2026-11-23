"""Company career portals (ATS) via their public job APIs: fast, official, no login.

Supported: Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable.
Boards come from config.json -> "company_boards" (seeds) plus boards auto-discovered from
apply links seen on LinkedIn / Naukri / Indeed (stored in jobs.db -> boards). Boards that fail
twice in a row are skipped automatically.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import requests

from sources import UA, TIMEOUT, _iso, _strip, job

# Title filter applied to every board: built from your title keywords in run(). Default is permissive.
PM_HINT = re.compile(r".", re.I)
QUERY = ""  # keyword passed to boards that support server-side search (SmartRecruiters, Workable)


def _configure(cfg):
    global PM_HINT, QUERY
    words = cfg.get("derived_titles") or cfg.get("title_include") or []
    if words:
        PM_HINT = re.compile("|".join(re.escape(w.strip()) for w in words if w.strip()), re.I)
    from sources import _first_word
    QUERY = _first_word(cfg) if cfg.get("search_terms") else ""

# ---------- ATS detection from any URL (also used to tag each job's apply link) ----------
PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([\w.%-]+)", re.I)),
    ("workday", re.compile(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?!j/)([\w-]+)", re.I)),
]


def detect(url: str | None) -> tuple[str, str] | None:
    """Return (ats, board_key) for a URL, e.g. ('workday', 'nvidia|wd5|NVIDIAExternalCareerSite')."""
    if not url:
        return None
    for ats, rx in PATTERNS:
        m = rx.search(url)
        if m:
            key = "|".join(m.groups()) if ats == "workday" else m.group(1)
            if key.lower() in ("embed", "jobs", "v1", "api"):
                return None
            return ats, key
    return None


def ats_of(url: str | None) -> str:
    d = detect(url)
    if d:
        return d[0]
    u = (url or "").lower()
    for host, name in (("linkedin.com", "linkedin"), ("naukri.com", "naukri"), ("indeed.", "indeed"),
                       ("jobicy.com", "jobicy"), ("remotive.com", "remotive"), ("remoteok.com", "remoteok"),
                       ("himalayas.app", "himalayas"), ("weworkremotely.com", "weworkremotely")):
        if host in u:
            return name
    return "company site" if u else ""


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


# ---------- fetchers: each yields normalized jobs for one board ----------

def greenhouse(slug):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    for j in data.get("jobs", []):
        if not PM_HINT.search(j.get("title", "")):
            continue
        loc = (j.get("location") or {}).get("name", "")
        yield job(source="greenhouse", ext_id=f"{slug}-{j['id']}", title=j.get("title"),
                  company=j.get("company_name") or slug.replace("-", " ").title(), location=loc,
                  remote="remote" in loc.lower(), url=j.get("absolute_url"), apply_url=j.get("absolute_url"),
                  posted=_iso(j.get("first_published") or j.get("updated_at")),
                  description=_strip(j.get("content") or "", 6000))


def lever(slug):
    data = _get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
    for j in data:
        if not PM_HINT.search(j.get("text", "")):
            continue
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        sr = j.get("salaryRange") or {}
        yield job(source="lever", ext_id=f"{slug}-{j['id']}", title=j.get("text"), company=slug.title(),
                  location=", ".join(filter(None, locs)), remote=j.get("workplaceType") == "remote",
                  url=j.get("hostedUrl"), apply_url=j.get("applyUrl") or j.get("hostedUrl"),
                  posted=_iso(j.get("createdAt")),
                  sal_min=sr.get("min"), sal_max=sr.get("max"), currency=sr.get("currency"),
                  interval="yearly" if "year" in str(sr.get("interval", "")) else None,
                  description=_strip(j.get("descriptionPlain"), 6000))


def ashby(slug):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}).json()
    for j in data.get("jobs", []):
        if not PM_HINT.search(j.get("title", "")):
            continue
        comp = j.get("compensation") or {}
        sal = next((c for c in comp.get("summaryComponents", []) if c.get("compensationType") == "Salary"), {})
        yield job(source="ashby", ext_id=f"{slug}-{j.get('id') or j.get('jobUrl')}", title=j.get("title"),
                  company=slug.replace("-", " ").title(), location=j.get("location", ""),
                  remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
                  url=j.get("jobUrl"), apply_url=j.get("applyUrl") or j.get("jobUrl"),
                  posted=_iso(j.get("publishedAt")), salary_text=comp.get("compensationTierSummary") or "",
                  sal_min=sal.get("minValue"), sal_max=sal.get("maxValue"), currency=sal.get("currencyCode"),
                  interval="yearly" if "YEAR" in str(sal.get("interval", "")).upper() else None,
                  description=_strip(j.get("descriptionPlain"), 6000))


def _wd_posted(s: str) -> str | None:
    s = (s or "").lower()
    if "today" in s:
        return date.today().isoformat()
    if "yesterday" in s:
        return (date.today() - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", s)
    return (date.today() - timedelta(days=int(m.group(1)))).isoformat() if m else None


def workday(key, terms):
    tenant, wd, site = key.split("|")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    seen = set()
    for term in terms:
        for offset in (0, 20):
            r = requests.post(api, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term},
                              headers={**UA, "Accept": "application/json"}, timeout=TIMEOUT)
            r.raise_for_status()
            posts = r.json().get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath") or ""
                if path in seen or not PM_HINT.search(p.get("title", "")):
                    continue
                seen.add(path)
                url = f"{base}/{site}{path}"
                yield job(source="workday", ext_id=f"{tenant}-{path}", title=p.get("title"),
                          company=tenant.replace("-", " ").title(), location=p.get("locationsText", ""),
                          remote="remote" in (p.get("locationsText") or "").lower(),
                          url=url, apply_url=url + "/apply", posted=_wd_posted(p.get("postedOn")))
            if len(posts) < 20:
                break


def smartrecruiters(company):
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                params={"q": QUERY, "limit": 100}).json()
    for p in data.get("content", []):
        if not PM_HINT.search(p.get("name", "")):
            continue
        loc = p.get("location") or {}
        locs = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
        url = f"https://jobs.smartrecruiters.com/{company}/{p.get('id')}"
        yield job(source="smartrecruiters", ext_id=f"{company}-{p.get('id')}", title=p.get("name"),
                  company=(p.get("company") or {}).get("name") or company, location=locs,
                  remote=bool(loc.get("remote")), url=url, apply_url=url, posted=_iso(p.get("releasedDate")),
                  experience_text=(p.get("experienceLevel") or {}).get("label", ""))


def workable(slug):
    r = requests.post(f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
                      json={"query": QUERY, "location": [], "department": [], "worktype": [], "remote": []},
                      headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    for p in r.json().get("results", []):
        if not PM_HINT.search(p.get("title", "")):
            continue
        loc = p.get("location") or {}
        url = f"https://apply.workable.com/{slug}/j/{p.get('shortcode')}/"
        yield job(source="workable", ext_id=f"{slug}-{p.get('shortcode')}", title=p.get("title"),
                  company=slug.title(), location=", ".join(filter(None, [loc.get("city"), loc.get("country")])),
                  remote=bool(p.get("remote")), url=url, apply_url=url + "apply/", posted=_iso(p.get("published")))


# ---------- board registry (seeds + discovered), stored in jobs.db ----------

def _boards_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS boards (ats TEXT, key TEXT, origin TEXT, fails INTEGER DEFAULT 0,
                 last_ok TEXT, last_jobs INTEGER DEFAULT 0, PRIMARY KEY (ats, key))""")


def remember(c, urls, log=print) -> int:
    """Learn new company boards from apply links seen on job sites."""
    _boards_table(c)
    new = 0
    for u in urls:
        d = detect(u)
        if d and c.execute("INSERT OR IGNORE INTO boards (ats, key, origin) VALUES (?,?, 'discovered')", d).rowcount:
            new += 1
    c.commit()
    if new:
        log(f"  discovered {new} new company career portals from apply links")
    return new


def run(cfg, conn, log=print) -> list[dict]:
    _configure(cfg)
    _boards_table(conn)
    for ats, keys in cfg.get("company_boards", {}).items():
        for k in keys:
            conn.execute("INSERT OR IGNORE INTO boards (ats, key, origin) VALUES (?,?, 'config')", (ats, k))
    conn.commit()
    boards = conn.execute("SELECT ats, key FROM boards WHERE fails < 2 AND ats != 'workday'").fetchall()  # Workday blocks API access (Cloudflare 429)
    terms = cfg.get("workday_terms") or cfg["search_terms"][:4]
    fns = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
           "workable": workable, "workday": lambda k: workday(k, terms)}

    def one(ats, key):
        return ats, key, list(fns[ats](key))

    out, ok, bad = [], 0, 0
    with ThreadPoolExecutor(max_workers=cfg.get("ats_workers", 8)) as ex:
        futs = {ex.submit(one, a, k): (a, k) for a, k in boards if a in fns}
        for f in as_completed(futs):
            try:
                ats, key, jobs = f.result()
                out += jobs
                ok += 1
                conn.execute("UPDATE boards SET fails=0, last_ok=?, last_jobs=? WHERE ats=? AND key=?",
                             (datetime.now().isoformat(timespec="seconds"), len(jobs), ats, key))
            except Exception as e:
                bad += 1
                a, k = futs[f]
                conn.execute("UPDATE boards SET fails=fails+1 WHERE ats=? AND key=?", (a, k))
                log(f"  skip {a}/{k}: {str(e)[:70]}")
    conn.commit()
    log(f"  career portals: {ok} ok, {bad} failed, {len(out)} matching roles")
    return out
