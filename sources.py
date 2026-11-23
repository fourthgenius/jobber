"""Job source fetchers. Each returns a list of normalized job dicts.

Order of preference: official public APIs / feeds first, then JobSpy
(open-source scraper) for LinkedIn, Indeed, Naukri, which have no public API.
"""
from __future__ import annotations

import html
import random
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (personal jobber; low volume)"}


def _first_word(cfg) -> str:
    """Short query for boards that take one keyword (e.g. 'product', 'engineer')."""
    t = (cfg.get("search_terms") or ["product"])[0]
    return t.split()[-1] if len(t.split()) > 1 else t
TIMEOUT = 25


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _strip(s: str | None, n: int = 4000) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return s[:n]


def _iso(ts) -> str | None:
    """Accept epoch seconds/ms, ISO strings, RFC822; return YYYY-MM-DD."""
    if ts is None or ts == "":
        return None
    try:
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            v = float(ts)
            if v > 1e12:
                v /= 1000
            return datetime.fromtimestamp(v, tz=timezone.utc).date().isoformat()
        if hasattr(ts, "isoformat"):
            s = ts.isoformat()[:10]
            return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else None  # pandas NaT -> None
        s = str(ts)
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
        return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None
    except Exception:
        return None


def job(**kw) -> dict:
    base = dict(source="", ext_id="", title="", company="", location="", remote=False,
                url="", apply_url="", posted=None, salary_text="", sal_min=None, sal_max=None,
                currency=None, interval=None, experience_text="", description="")
    base.update({k: v for k, v in kw.items() if v is not None})
    return base


# ---------------- Remote boards (official public APIs / feeds) ----------------

def remotive(cfg):
    out = []
    for q in cfg["search_terms"][:6]:
        data = _get("https://remotive.com/api/remote-jobs", params={"search": q, "limit": 100}).json()
        for j in data.get("jobs", []):
            out.append(job(source="remotive", ext_id=str(j["id"]), title=j.get("title"),
                           company=j.get("company_name"), location=j.get("candidate_required_location") or "Remote",
                           remote=True, url=j.get("url"), posted=_iso(j.get("publication_date")),
                           salary_text=j.get("salary") or "", description=_strip(j.get("description"))))
        time.sleep(1)
    return out


def remoteok(cfg):
    # ToS: link back to Remote OK (we keep their URL as the apply link).
    data = _get("https://remoteok.com/api", params={"tag": _first_word(cfg).lower()}).json()
    out = []
    for j in data[1:] if data and isinstance(data[0], dict) and "legal" in data[0] else data:
        if not isinstance(j, dict) or "position" not in j:
            continue
        out.append(job(source="remoteok", ext_id=str(j.get("id")), title=j.get("position"),
                       company=j.get("company"), location=j.get("location") or "Remote", remote=True,
                       url=j.get("url"), posted=_iso(j.get("epoch") or j.get("date")),
                       sal_min=j.get("salary_min") or None, sal_max=j.get("salary_max") or None,
                       currency="USD" if j.get("salary_max") else None, interval="yearly",
                       description=_strip(j.get("description"))))
    return out


def himalayas(cfg):
    out = []
    for q in cfg["search_terms"][:6]:
        try:
            data = _get("https://himalayas.app/jobs/api/search", params={"q": q, "limit": 100}).json()
        except Exception:
            data = _get("https://himalayas.app/jobs/api", params={"limit": 100}).json()
        for j in data.get("jobs", []):
            locs = j.get("locationRestrictions") or []
            loc = ", ".join(l if isinstance(l, str) else l.get("name", "") for l in locs) or "Worldwide"
            out.append(job(source="himalayas", ext_id=str(j.get("guid") or j.get("applicationLink")),
                           title=j.get("title"), company=j.get("companyName"), location=loc, remote=True,
                           url=j.get("applicationLink") or j.get("guid"), posted=_iso(j.get("pubDate")),
                           sal_min=j.get("minSalary"), sal_max=j.get("maxSalary"),
                           currency=j.get("currency") or "USD", interval="yearly",
                           experience_text=j.get("seniority") and ",".join(j["seniority"]) or "",
                           description=_strip(j.get("description") or j.get("excerpt"))))
        time.sleep(1)
    return out


def jobicy(cfg):
    data = _get("https://jobicy.com/api/v2/remote-jobs", params={"count": 100, "tag": cfg["search_terms"][0]}).json()
    out = []
    for j in data.get("jobs", []):
        out.append(job(source="jobicy", ext_id=str(j.get("id")), title=html.unescape(j.get("jobTitle", "")),
                       company=j.get("companyName"), location=j.get("jobGeo") or "Remote", remote=True,
                       url=j.get("url"), posted=_iso(j.get("pubDate")),
                       sal_min=j.get("annualSalaryMin"), sal_max=j.get("annualSalaryMax"),
                       currency=j.get("salaryCurrency"), interval="yearly",
                       experience_text=j.get("jobLevel") or "",
                       description=_strip(j.get("jobDescription") or j.get("jobExcerpt"))))
    return out


def weworkremotely(cfg):
    xml = _get(cfg.get("weworkremotely_feed") or "https://weworkremotely.com/remote-jobs.rss").content
    out = []
    for it in ET.fromstring(xml).iter("item"):
        t = (it.findtext("title") or "")
        company, _, title = t.partition(":")
        out.append(job(source="weworkremotely", ext_id=it.findtext("guid") or it.findtext("link"),
                       title=title.strip() or t, company=company.strip(),
                       location=it.findtext("region") or "Anywhere", remote=True,
                       url=it.findtext("link"), posted=_iso(it.findtext("pubDate")),
                       description=_strip(it.findtext("description"))))
    return out


# ---------------- Company career pages (official ATS APIs) ----------------

def _greenhouse(slug):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        yield job(source="greenhouse", ext_id=f"{slug}-{j['id']}", title=j.get("title"),
                  company=j.get("company_name") or slug.title(), location=loc,
                  remote="remote" in loc.lower(), url=j.get("absolute_url"),
                  posted=_iso(j.get("first_published") or j.get("updated_at")),
                  description=_strip(html.unescape(j.get("content") or "")))


def _lever(slug):
    data = _get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
    for j in data:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        sr = j.get("salaryRange") or {}
        yield job(source="lever", ext_id=f"{slug}-{j['id']}", title=j.get("text"), company=slug.title(),
                  location=", ".join(filter(None, locs)), remote=j.get("workplaceType") == "remote",
                  url=j.get("hostedUrl"), posted=_iso(j.get("createdAt")),
                  sal_min=sr.get("min"), sal_max=sr.get("max"), currency=sr.get("currency"),
                  interval=(sr.get("interval") or "").replace("per-year-salary", "yearly") or None,
                  description=_strip(j.get("descriptionPlain")))


def _ashby(slug):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                params={"includeCompensation": "true"}).json()
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        sal = next((c for c in comp.get("summaryComponents", []) if c.get("compensationType") == "Salary"), {})
        yield job(source="ashby", ext_id=f"{slug}-{j.get('id') or j.get('jobUrl')}", title=j.get("title"),
                  company=slug.title(), location=j.get("location", ""),
                  remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
                  url=j.get("jobUrl"), posted=_iso(j.get("publishedAt")),
                  salary_text=comp.get("compensationTierSummary") or "",
                  sal_min=sal.get("minValue"), sal_max=sal.get("maxValue"), currency=sal.get("currencyCode"),
                  interval="yearly" if "YEAR" in str(sal.get("interval", "")).upper() else None,
                  description=_strip(j.get("descriptionPlain")))


def company_boards(cfg, log=print):
    fns = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby}
    out = []
    for ats, slugs in cfg.get("company_boards", {}).items():
        for slug in slugs:
            try:
                out.extend(fns[ats](slug))
            except Exception as e:
                log(f"  skip {ats}/{slug}: {str(e)[:80]}")
            time.sleep(0.5)
    return out


# ---------------- LinkedIn / Indeed / Naukri / Glassdoor via JobSpy ----------------

def jobspy_sites(cfg, sites, log=print):
    """Uses python-jobspy (pip install python-jobspy). These sites have no public
    job-search API and their ToS restrict automated access, so we keep volume low,
    run from your own computer, and only read public listings (no login)."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        log("  JobSpy not installed: pip install python-jobspy  (skipping LinkedIn/Indeed/Naukri)")
        return []
    hours = cfg["max_age_days"] * 24
    search_locs = [loc["search"] for loc in cfg["locations"] if loc.get("search")]
    remote_q = [(cfg.get("country") or "Remote", True)] if cfg["remote"].get("enabled") else []

    def run(site_list, term, loc, remote, n, hours=hours):
        """One search. Returns (rows, set of sites that were rate-limited)."""
        try:
            df = scrape_jobs(site_name=site_list, search_term=term, location=loc, is_remote=remote,
                             results_wanted=n, hours_old=hours,
                             country_indeed=(cfg.get("country") or "worldwide").lower(), description_format="markdown",
                             enforce_annual_salary=True, verbose=0)
        except Exception as e:
            log(f"  jobspy '{term[:40]}' @ {loc}: {str(e)[:100]}")
            return [], set()
        limited = set()
        for err_site, err in (df.attrs.get("errors") or {}).items():
            log(f"  {err_site} error for '{term[:40]}' @ {loc}: {str(err)[:100]}")
            if "429" in str(err):
                limited.add(err_site)
        return [dict(r, _remote_q=remote) for r in df.to_dict("records")], limited

    rows = []
    # 1) LinkedIn blocks by request count, so: a few big boolean-OR searches, long pauses,
    #    back off on 429 and stop for this run if it keeps blocking.
    if "linkedin" in sites:
        q = " OR ".join(f'"{t}"' for t in cfg["search_terms"])
        li_locs = [(loc, False) for loc in search_locs] + remote_q
        n = cfg.get("linkedin_results_per_location", 100)
        # Newest first: last few days for every location, then the full window if LinkedIn still allows.
        passes = [(cfg.get("recent_hours", 72), "recent")] + [(hours, "older")]
        blocked = False
        for p_hours, p_name in passes:
            for loc, remote in li_locs:
                ok = False
                for attempt in range(3):
                    got, limited = run(["linkedin"], q, loc, remote, n, hours=p_hours)
                    rows += got
                    log(f"  linkedin {p_name} @ {loc}{' (remote)' if remote else ''}: {len(got)}")
                    if "linkedin" not in limited:
                        ok = True
                        break
                    if attempt < 2:
                        wait = 120 * (attempt + 1)
                        log(f"  LinkedIn rate-limited; pausing {wait}s then retrying...")
                        time.sleep(wait)
                if not ok:
                    log("  LinkedIn still blocking: skipping the rest this run (try again in an hour)")
                    blocked = True
                    break
                time.sleep(20 + random.random() * 20)
            if blocked:
                break

    # 2) Other sites (Naukri, Indeed...): one search per term x location.
    others = [s for s in sites if s != "linkedin"]
    if others:
        queries = [(t, loc, False) for t in cfg["search_terms"] for loc in search_locs]
        queries += [(t, r[0], True) for t in cfg["search_terms"] for r in remote_q]
        for term, loc, remote in queries:
            got, _ = run(others, term, loc, remote, cfg.get("results_per_query", 25))
            rows += got
            time.sleep(2 + random.random() * 2)

    out = []
    for r in rows:
        r = {k: _clean(v) for k, v in r.items()}  # pandas NaN/NaT -> None
        locv = r.get("location")
        if isinstance(locv, dict):
            locv = ", ".join(str(v) for v in (locv.get("city"), locv.get("state"), locv.get("country")) if v)
        out.append(job(source=r.get("site") or "jobspy", ext_id=str(r.get("id") or r.get("job_url")),
                       title=r.get("title"), company=r.get("company") or "", location=str(locv or ""),
                       remote=bool(r.get("is_remote")) or bool(r.get("_remote_q")),
                       url=r.get("job_url") or r.get("job_url_direct"),
                       apply_url=r.get("job_url_direct") or r.get("job_url"),
                       posted=_iso(r.get("date_posted")),
                       sal_min=_num(r.get("min_amount")), sal_max=_num(r.get("max_amount")),
                       currency=r.get("currency"), interval=_s(r.get("interval")),
                       experience_text=_s(r.get("experience_range")) or "",
                       description=_strip(_s(r.get("description")))))
    return out


def _clean(v):
    if v is None or (isinstance(v, float) and v != v) or str(v) in ("NaT", "nan", "NaN"):
        return None
    return v


def _num(v):
    try:
        f = float(v)
        return None if f != f else f  # NaN -> None
    except (TypeError, ValueError):
        return None


def _s(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return getattr(v, "value", v) if not isinstance(v, str) else v


OFFICIAL = {"remotive": remotive, "remoteok": remoteok, "himalayas": himalayas,
            "jobicy": jobicy, "weworkremotely": weworkremotely}
JOBSPY = ("linkedin", "indeed", "naukri", "glassdoor")
