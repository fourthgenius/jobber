"""Referrals and LinkedIn hiring posts.  Page: /network.  Hooked from app.py, fetch.py and store.py.

Referrals (no setup): every job gets "Find referrers" links (LinkedIn people search for the company, filtered
to your 1st/2nd-degree network, plus alumni / ex-colleague searches from your Setup profile) and a drafted
referral ask to copy. Jobs rank higher when you know someone there: either you tick "I know someone here",
or the Chrome extension saw LinkedIn's own "N connections work here" line on a job page you opened.

LinkedIn posts: people post bulk job-link lists and recruiters post "we're hiring" without a LinkedIn Jobs
listing. Jobber finds public posts through a web search (no LinkedIn login, nothing automated on LinkedIn),
or takes a post you paste / save with the extension. Job links are followed to the real job page, read,
de-duplicated and tagged "LinkedIn post", with the poster kept as a contact.

Nothing here sends a message to anyone: every note is a draft you copy and send yourself.
"""
from __future__ import annotations

import html as _html
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

import store
from settings import me

HERE = Path(__file__).parent
PAGE = HERE / "network.html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) personal-jobber/1.0 (low volume)",
      "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = 20

DEFAULTS = {
    "referral_boost": 12,        # fit points added to jobs where you know someone (ranking only)
    "post_engine": "auto",       # auto | brave | google | duckduckgo
    "post_queries": 4,           # web searches per job search (one per role)
    "post_max": 15,              # posts opened per job search
    "post_days": 7,              # only posts from the last N days (where the engine supports it)
    "post_words": ["hiring", "we're hiring", "openings", "job alert", "referral"],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS referral (company TEXT PRIMARY KEY, label TEXT, connections INTEGER DEFAULT 0,
                                     alumni INTEGER DEFAULT 0, known INTEGER DEFAULT 0, note TEXT DEFAULT '',
                                     url TEXT DEFAULT '', updated TEXT);
CREATE TABLE IF NOT EXISTS post_leads (job_id TEXT, post_url TEXT, poster TEXT, poster_url TEXT, email TEXT,
                                       snippet TEXT, found TEXT, PRIMARY KEY (job_id, post_url));
CREATE TABLE IF NOT EXISTS posts_seen (url TEXT PRIMARY KEY, seen TEXT, jobs INTEGER DEFAULT 0, via TEXT);
"""


def conn():
    c = store.conn()
    c.executescript(SCHEMA)
    return c


def opts(cfg) -> dict:
    return {**DEFAULTS, **(cfg.get("network") or {})}


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ============================================================== referrals
def norm(company: str) -> str:
    return store._norm(company or "")


def _same(a: str, b: str) -> bool:
    """'razorpay' ~ 'razorpay software'; 'meesho' !~ 'meesho fashion'? (yes, prefix at a word boundary)."""
    if not a or not b:
        return False
    if a == b:
        return True
    s, l = sorted((a, b), key=len)
    return len(s) >= 4 and (l.startswith(s + " ") or l.endswith(" " + s))


def referrals(c) -> dict:
    return {r["company"]: dict(r) for r in c.execute("SELECT * FROM referral")}


def lookup(refs: dict, company: str) -> dict | None:
    n = norm(company)
    if n in refs:
        return refs[n]
    for k, v in refs.items():
        if _same(k, n):
            return v
    return None


def annotate(c, jobs: list[dict], cfg) -> None:
    """Add j["referral"], j["lead"] and j["rank"] (fit + referral boost) to each job."""
    c.executescript(SCHEMA)
    refs = referrals(c)
    leads = {}
    for r in c.execute("SELECT * FROM post_leads ORDER BY found DESC"):
        leads.setdefault(r["job_id"], dict(r))
    boost = int(opts(cfg)["referral_boost"])
    for j in jobs:
        r = lookup(refs, j.get("company", "")) if refs else None
        r = r if r and (r["known"] or r["connections"] or r["alumni"]) else None
        j["referral"] = {k: r[k] for k in ("connections", "alumni", "known", "note")} if r else None
        j["lead"] = leads.get(j["id"])
        j["rank"] = j.get("fit", 0) + (boost if r else 0) + (3 if j["lead"] else 0)


def save_signal(c, body: dict) -> dict:
    """From the Chrome extension: LinkedIn showed 'N connections / alumni work here' on a job page you opened."""
    company = (body.get("company") or "").strip()[:120]
    if not norm(company):
        raise ValueError("company missing")
    conns, alum = int(body.get("connections") or 0), int(body.get("alumni") or 0)
    c.execute("""INSERT INTO referral (company, label, connections, alumni, url, updated) VALUES (?,?,?,?,?,?)
                 ON CONFLICT(company) DO UPDATE SET connections=excluded.connections, alumni=excluded.alumni,
                 label=excluded.label, url=excluded.url, updated=excluded.updated""",
              (norm(company), company, conns, alum, (body.get("url") or "")[:500], _now()))
    c.commit()
    return {"ok": True, "company": company, "connections": conns, "alumni": alum}


def set_known(c, company: str, known: bool, note: str | None = None) -> dict:
    if not norm(company):
        raise ValueError("company missing")
    c.execute("""INSERT INTO referral (company, label, known, note, updated) VALUES (?,?,?,?,?)
                 ON CONFLICT(company) DO UPDATE SET known=excluded.known,
                 note=COALESCE(?, note), updated=excluded.updated""",
              (norm(company), company, int(known), note or "", _now(), note))
    c.commit()
    return {"ok": True}


LI_PEOPLE = "https://www.linkedin.com/search/results/people/?"


def _people(keywords: str, network: str | None = '["F","S"]') -> str:
    q = f"keywords={quote_plus(keywords)}&origin=GLOBAL_SEARCH_HEADER"
    return LI_PEOPLE + q + (f"&network={quote_plus(network)}" if network else "")


def referrer_links(j, cfg) -> list[dict]:
    """Searches you open yourself in LinkedIn (signed in in your own browser). Jobber never visits them."""
    co, m = (j.get("company") or "").strip(), me()
    role = ((cfg.get("search_terms") or [""])[0] or "").strip()
    out = [{"label": f"Your connections at {co}", "hint": "1st and 2nd-degree people who work there",
            "url": _people(co)}]
    if m.get("school"):
        out.append({"label": f"{m['school']} alumni at {co}", "hint": "Alumni reply to referral asks most often",
                    "url": _people(f"{co} {m['school']}", None)})
    if m.get("company"):
        out.append({"label": f"Ex-{m['company']} people at {co}", "hint": "Former colleagues of yours",
                    "url": _people(f"{co} {m['company']}", None)})
    if role:
        out.append({"label": f"{role.title()} team at {co}", "hint": "People doing this job there",
                    "url": _people(f"{co} {role}")})
    out.append({"label": f"Recruiters at {co}", "hint": "Talent acquisition",
                "url": _people(f"{co} talent acquisition", None)})
    return out


def _first_win() -> str:
    try:
        import copilot
        return copilot._first_win()
    except Exception:
        return ""


def referral_ask(j) -> str:
    m = me()
    co, role = j.get("company") or "your company", (j.get("title") or "this role")[:80]
    who = m.get("headline") or (f"a {m['title']} at {m['company']}" if m.get("title") and m.get("company") else "")
    who = who[:1].lower() + who[1:] if who and not who[1:2].isupper() else who
    win = _first_win()
    link = j.get("apply_url") or j.get("url") or ""
    lines = ["Hi {name}, hope you're doing well!",
             "",
             f"I came across the {role} role at {co}" + (f" ({link})" if link else "") + " and I'd love to apply. "
             + (f"I'm {who}" + (f", and recently I {win[:140]}." if win else ".") if who else
                (f"Recently I {win[:140]}." if win else "")),
             "",
             "Would you be open to referring me, or pointing me to the right person on the team? I can send my CV "
             "and a short note you can forward, so it takes you two minutes.",
             "",
             f"Thanks a lot,\n{m.get('name') or ''}".rstrip()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def poster_note(j, lead: dict) -> str:
    m = me()
    first = (lead.get("poster") or "").split(" ")[0] or "there"
    who = m.get("headline") or m.get("title") or ""
    return (f"Hi {first}, I saw your post about the {j.get('title', 'open')} role at {j.get('company', 'your team')}. "
            + (f"I'm {who[:1].lower() + who[1:] if who and not who[1:2].isupper() else who}" + ". " if who else "")
            + "I'd love to be considered: may I send you my CV, or is there a link you'd like me to apply through? "
            f"Thanks!\n{m.get('name') or ''}").strip()


def job_help(c, cfg, jid: str) -> dict:
    j = next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), None)
    if not j:
        raise KeyError("job not found")
    out = {"links": referrer_links(j, cfg), "ask": referral_ask(j), "referral": j.get("referral"),
           "lead": j.get("lead")}
    if j.get("lead"):
        out["poster_note"] = poster_note(j, j["lead"])
    return out


# ============================================================== LinkedIn posts: parsing
URL_RX = re.compile(r"https?://[^\s<>\"'()\[\]{}|]+|(?<![\w/@.])(?:lnkd\.in|bit\.ly|forms\.gle)/[\w-]+", re.I)
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SKIP_HOSTS = ("linkedin.com/in/", "linkedin.com/company/", "linkedin.com/feed/hashtag", "linkedin.com/posts/",
              "linkedin.com/search", "linkedin.com/groups", "youtube.com", "youtu.be", "instagram.com",
              "twitter.com", "x.com/", "t.me/", "chat.whatsapp.com", "wa.me/", "facebook.com", "medium.com",
              "topmate.io", "calendly.com")
SENIORITY = r"(?:(?:senior|sr\.?|junior|jr\.?|associate|assistant|lead|principal|staff|group|head of|chief)\s+)*"


def _clean_url(u: str) -> str:
    u = u.strip().rstrip(".,;:!?)»”’'\"")
    if not u.lower().startswith("http"):
        u = "https://" + u
    return u


def unwrap(u: str) -> str:
    """LinkedIn wraps outside links: linkedin.com/redir/redirect?url=... or /safety/go?url=..."""
    p = urlparse(u)
    if "linkedin.com" in p.netloc and ("redir" in p.path or "safety" in p.path):
        q = parse_qs(p.query)
        for k in ("url", "redirectUrl"):
            if q.get(k):
                return unquote(q[k][0])
    return u


def _skip(u: str) -> bool:
    lu = u.lower()
    return any(h in lu for h in SKIP_HOSTS)


def find_links(text: str, links=None) -> list[tuple[str, str]]:
    """(url, hint) pairs: each link with the words around it ('Razorpay - Product Manager - Bangalore')."""
    out, seen = [], set()
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        for m in URL_RX.finditer(line):
            u = unwrap(_clean_url(m.group(0)))
            if u in seen or _skip(u):
                continue
            seen.add(u)
            hint = URL_RX.sub(" ", line)
            if len(re.sub(r"[\W\d_]+", "", hint)) < 4 and i:  # link on its own line: use the line above
                hint = URL_RX.sub(" ", lines[i - 1])
            out.append((u, _tidy(hint)))
    for item in links or []:  # anchors from the extension: {"url", "text"}
        u = unwrap(_clean_url(item.get("url") or ""))
        if u and u not in seen and not _skip(u) and u.lower().startswith("http"):
            seen.add(u)
            out.append((u, _tidy(item.get("text") or "")))
    return out


def _tidy(s: str) -> str:
    s = re.sub(r"[✀-➿\U0001F000-\U0001FAFF☀-⛿️‍]", " ", s or "")  # emoji
    s = re.sub(r"^\s*(?:\d+[.)]|[-*•▪►➡→✅]+)\s*", "", s)
    s = re.sub(r"(?i)\b(apply(?: here| now| link)?|link|jd|job link)\b\s*[:\-–]*\s*$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip(" -–|:,")


def _has_word(text: str, words) -> str | None:
    import filters
    return filters._has(text, words)


def _title_in(text: str, cfg) -> str | None:
    """The role named in some text, as written there ('Senior Product Manager'), if it is one of your roles."""
    import filters
    best = None
    for kw in sorted(filters.titles(cfg), key=len, reverse=True):
        m = re.search(rf"(?<![a-z]){SENIORITY}{re.escape(kw)}(?![a-z])", text, re.I)
        if m:
            best = m.group(0).strip()
            break
    if not best:
        return None
    return best if any(ch.isupper() for ch in best) else best.title()


def _location_in(text: str, cfg) -> str:
    low = (text or "").lower()
    for L in cfg.get("locations") or []:
        if _has_word(low, list(L.get("match") or []) + [L["label"]]):
            return L["label"]
    if re.search(r"\b(remote|work from home|wfh)\b", low):
        return "Remote"
    return cfg.get("country") or ""


COMPANY_RX = re.compile(r"(?:\bat|@|\bjoin(?:ing)?|\bteam at|\bhiring at)\s+@?((?:[A-Z0-9][\w&.'’-]*)(?:\s+[A-Z0-9][\w&.'’-]*){0,3})")
NOT_COMPANY = {"we", "our", "the", "us", "my", "a", "an", "this", "linkedin", "hiring", "india", "bangalore",
               "bengaluru", "remote", "delhi", "mumbai", "gurgaon", "noida", "hyderabad", "pune", "chennai"}


def _company_in(text: str) -> str:
    for m in COMPANY_RX.finditer(text or ""):
        name = re.sub(r"[.'’]+$", "", m.group(1)).strip()
        if name.lower() not in NOT_COMPANY and not name.lower().startswith(("we ", "our ")):
            return name
    tag = re.search(r"#(\w{3,})hiring\b|#hiringat(\w{3,})", text or "", re.I)
    if tag:
        return (tag.group(1) or tag.group(2)).title()
    return ""


def split_hint(hint: str, cfg) -> dict:
    """'Razorpay - Product Manager - Bangalore' -> title, company, location (any may be empty)."""
    parts = [p.strip() for p in re.split(r"\s+[-–|•:]\s+|\s*\|\s*|:\s+|,\s+(?=[A-Z])", hint or "") if p.strip()]
    title = company = loc = ""
    for p in parts:
        if not title and _title_in(p, cfg):
            title = p if len(p) < 90 else _title_in(p, cfg)
        elif not loc and _location_in(p, cfg) not in ("", cfg.get("country") or "") and len(p) < 40:
            loc = p
        elif not company and 1 < len(p) < 60:
            company = re.sub(r"(?i)^(?:at|@)\s+", "", p)
    if not title:
        title = _title_in(hint or "", cfg) or ""
    if not company:
        company = _company_in(hint or "")
    return {"title": title, "company": company, "location": loc}


def poster_from(url: str = "", title: str = "") -> tuple[str, str]:
    """Poster name and profile from a post URL (linkedin.com/posts/<vanity>_...) or page/search title."""
    name, prof = "", ""
    m = re.search(r"linkedin\.com/posts/([\w-]+?)_", url or "")
    if m:
        prof = f"https://www.linkedin.com/in/{m.group(1)}/"
        name = re.sub(r"-[0-9a-f]{5,}$|-\d+$", "", m.group(1)).replace("-", " ").title()
    t = _html.unescape(title or "")
    for rx in (r"^(.+?) on LinkedIn:", r"^(.+?)['’]s Post\b", r"\|\s*(.+?) (?:posted|commented) on",
               r"^(.+?) posted on the topic"):
        mm = re.search(rx, t)
        if mm and len(mm.group(1)) < 60:
            name = mm.group(1).strip()
            break
    return name, prof


# ============================================================== reading job pages
def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True, **kw)
    r.raise_for_status()
    return r


def resolve(url: str) -> str:
    """Follow short links (lnkd.in, bit.ly) to the real page. lnkd.in shows a 'leaving LinkedIn' page."""
    host = urlparse(url).netloc.lower()
    if not any(s in host for s in ("lnkd.in", "bit.ly", "tinyurl", "t.co", "rb.gy", "shorturl", "linkedin.com")):
        return url
    try:
        r = _get(url)
        final = unwrap(r.url)
        if "lnkd.in" in urlparse(final).netloc or "linkedin.com/safety" in final:
            m = re.search(r'data-tracking-control-name="external_url_click"[^>]*href="([^"]+)"', r.text) or \
                re.search(r'<a[^>]+href="(https?://(?!(?:www\.)?linkedin\.com|lnkd\.in)[^"]+)"', r.text)
            if m:
                return _html.unescape(m.group(1))
        return final
    except requests.RequestException:
        return url


def _meta(page: str, name: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\'](?:og:)?{name}["\'][^>]*content=["\']([^"\']*)', page, re.I) \
        or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\'](?:og:)?{name}["\']',
                     page, re.I)
    return _html.unescape(m.group(1)).strip() if m else ""


def _jsonld_job(page: str) -> dict | None:
    for raw in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack += d
            elif isinstance(d, dict):
                t = d.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return d
                stack += [v for v in d.values() if isinstance(v, (list, dict))]
    return None


def _ld_location(d: dict) -> tuple[str, bool]:
    remote = str(d.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    locs = d.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    names = []
    for L in locs:
        a = (L or {}).get("address") or {}
        if isinstance(a, str):
            names.append(a)
        else:
            names.append(", ".join(x for x in (a.get("addressLocality"), a.get("addressRegion"),
                                               (a.get("addressCountry") or {}).get("name")
                                               if isinstance(a.get("addressCountry"), dict)
                                               else a.get("addressCountry")) if x))
    loc = " / ".join(n for n in names if n)
    return (loc or ("Remote" if remote else "")), remote


def _slug_name(slug: str) -> str:
    return re.sub(r"[-_.]+", " ", slug.split("|")[0]).strip().title()


def read_job(url: str, hint: dict, cfg) -> dict | None:
    """Turn one link into a job: the page's JobPosting data, else its title tags, else the post's words."""
    import ats
    from sources import _iso, _strip, job
    page, final = "", url
    try:
        r = _get(url)
        final, page = r.url, r.text[:600_000]
    except requests.RequestException:
        pass
    d = _jsonld_job(page) if page else None
    title = company = loc = desc = ""
    remote, posted = False, None
    if d:
        title = _strip(d.get("title") or "", 200)
        org = d.get("hiringOrganization") or {}
        company = (org.get("name") if isinstance(org, dict) else str(org)) or ""
        loc, remote = _ld_location(d)
        desc = _strip(d.get("description") or "", 6000)
        posted = _iso(d.get("datePosted"))
    elif page:
        og, tt = _meta(page, "title"), _html.unescape((re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
                                                        or [None, ""])[1]).strip()
        desc = _meta(page, "description")
        m = re.match(r"^(.*?) hiring (.*?) in (.*?) \| LinkedIn", og or tt)  # LinkedIn job page
        m2 = re.match(r"^Job Application for (.+?) at (.+)$", tt)  # Greenhouse
        if m:
            company, title, loc = m.groups()
        elif m2:
            title, company = m2.groups()
        else:
            for cand in (og, tt):
                parts = [p.strip() for p in re.split(r"\s+[-–|@]\s+|\s+at\s+", cand or "") if p.strip()]
                t = next((p for p in parts if _title_in(p, cfg)), "")
                if t:
                    title = t
                    company = next((p for p in parts if p != t and "careers" not in p.lower()
                                    and "jobs" not in p.lower()), "") or _meta(page, "site_name")
                    break
    title = title or hint.get("title") or ""
    if not _title_in(title, cfg) and hint.get("title"):
        title = hint["title"]
    det = ats.detect(final) or ats.detect(url)
    company = company or hint.get("company") or (_slug_name(det[1]) if det else "")
    if not company:
        host = urlparse(final).netloc.lower().replace("www.", "").replace("careers.", "").replace("jobs.", "")
        company = host.split(".")[0].title() if host and "linkedin" not in host else ""
    loc = loc or hint.get("location") or _location_in(f"{title} {desc[:1500]}", cfg)
    if not title or not company:
        return None
    jid = re.search(r"/jobs/view/(?:[\w-]*-)?(\d+)", final)
    return job(source="linkedin_post", title=title[:200], company=company[:120], location=loc[:120],
               remote=remote or "remote" in loc.lower(), url=final, apply_url=final, posted=posted,
               description=desc, ext_id=jid.group(1) if jid else "")


def jobs_from_post(text: str, cfg, links=None, post_url: str = "", poster: str = "", poster_url: str = "",
                   posted: str | None = None, max_links: int = 40, log=print) -> list[dict]:
    """All jobs in one post. Bulk link lists give one job per link; a recruiter's 'we're hiring' post with
    no link becomes one job pointing at the post itself, with the poster as the contact."""
    from sources import job
    text = (text or "")[:20000]
    emails = sorted(set(EMAIL_RX.findall(text)))[:3]
    lead = {"post_url": post_url, "poster": poster, "poster_url": poster_url, "email": ", ".join(emails),
            "snippet": re.sub(r"\s+", " ", text)[:400]}
    found, seen = [], set()
    for u, hint_text in find_links(text, links)[:max_links]:
        real = resolve(u)
        if real in seen or _skip(real):
            continue
        seen.add(real)
        hint = split_hint(hint_text, cfg)
        if not hint["company"]:
            hint["company"] = _company_in(text)
        j = read_job(real, hint, cfg)
        if j:
            j["posted"] = j.get("posted") or posted or str(date.today())
            j["_lead"] = lead
            found.append(j)
        time.sleep(0.4)
    if not found:  # no usable links: the post itself is the job ad
        title = _title_in(text, cfg)
        if title and re.search(r"(?i)hiring|opening|looking for|vacanc|join (?:us|our)|position|role", text):
            company = _company_in(text) or (f"via {poster}" if poster else "")
            link = post_url or poster_url or (f"mailto:{emails[0]}" if emails else "")
            if company:
                found.append(dict(job(source="linkedin_post", title=title[:200], company=company[:120],
                                      location=_location_in(text, cfg), url=link, apply_url=link,
                                      posted=posted or str(date.today()),
                                      description=text[:6000], experience_text=text[:600]),
                                  remote=bool(re.search(r"(?i)\bremote\b", text)), _lead=lead))
    return found


def save_jobs(c, cfg, found: list[dict], status: str | None = None) -> dict:
    """Filter, de-duplicate and store jobs found in posts (same rules as every other source)."""
    import ats
    import filters
    res = {"found": len(found), "kept": 0, "new": 0, "dropped": [], "jobs": []}
    for j in found:
        if not j.get("url"):
            res["dropped"].append({"title": j["title"], "company": j["company"],
                                   "why": "add the post's link so you can reach whoever posted it"})
            continue
        ev = filters.evaluate(j, cfg)
        if ev is None:
            res["dropped"].append({"title": j["title"], "company": j["company"],
                                   "why": filters.drop_reason(j, cfg) or "doesn't match your search"})
            continue
        j.update(ev)
        j["ats"] = ats.ats_of(j.get("apply_url") or j["url"])
        is_new = store.upsert(c, j)
        jid = store.key(j)
        save_lead(c, jid, j.get("_lead"))
        if status:
            store.update(c, jid, status=status)
        res["kept"] += 1
        res["new"] += is_new
        res["jobs"].append({"id": jid, "title": j["title"], "company": j["company"], "new": is_new,
                            "location": ev["location_bucket"]})
    ats.remember(c, [j.get("apply_url") or j.get("url") for j in found], log=lambda m: None)
    c.commit()
    return res


def save_lead(c, jid: str, lead: dict | None):
    if not lead or not (lead.get("post_url") or lead.get("poster")):
        return
    c.executescript(SCHEMA)
    c.execute("INSERT OR REPLACE INTO post_leads VALUES (?,?,?,?,?,?,?)",
              (jid, lead.get("post_url") or "", (lead.get("poster") or "")[:120], lead.get("poster_url") or "",
               (lead.get("email") or "")[:200], (lead.get("snippet") or "")[:400], _now()))


def add_post(c, cfg, body: dict, via: str = "paste") -> dict:
    """A post you pasted, or saved with the extension's 'Save LinkedIn post' button."""
    text, url = (body.get("text") or "").strip(), (body.get("url") or "").strip()
    if len(text) < 20 and not body.get("links"):
        raise ValueError("Paste the post's text (with its links) first.")
    poster, prof = body.get("poster") or "", body.get("poster_url") or ""
    if url and not poster:
        poster, prof = poster_from(url)
    found = jobs_from_post(text, cfg, links=body.get("links"), post_url=url, poster=poster.strip()[:120],
                           poster_url=prof)
    res = save_jobs(c, cfg, found, status=body.get("status") or None)
    if url:
        c.execute("INSERT OR REPLACE INTO posts_seen VALUES (?,?,?,?)", (url, _now(), res["kept"], via))
        c.commit()
    res["poster"] = poster
    return res


# ============================================================== finding public posts via web search
def _secrets() -> dict:
    try:
        import llm
        return llm.secrets()
    except Exception:
        return {}


def engine(cfg) -> str:
    want, s = opts(cfg)["post_engine"], _secrets()
    if want != "auto":
        return want
    if (s.get("brave_api_key") or "").strip():
        return "brave"
    if (s.get("google_cse_key") or "").strip() and (s.get("google_cse_id") or "").strip():
        return "google"
    return "duckduckgo"


def post_queries(cfg) -> list[str]:
    o = opts(cfg)
    roles = (cfg.get("search_terms") or [])[:] or (cfg.get("role_tiers", {}).get("core") or [])
    words = " OR ".join(f'"{w}"' if " " in w or "'" in w else w for w in o["post_words"])
    places = []
    for L in (cfg.get("locations") or [])[:3]:  # 'bangalore', 'bengaluru', 'delhi', 'gurgaon'...
        for w in [x for x in (L.get("match") or [L["label"]]) if len(x) > 3][:2]:
            if not any(w in p or p in w for p in places):
                places.append(w)
    if (cfg.get("remote") or {}).get("enabled"):
        places.append("remote")
    where = (" (" + " OR ".join(f'"{p}"' for p in places) + ")") if places else ""
    return [f'site:linkedin.com/posts "{r}" ({words}){where}' for r in roles[: int(o["post_queries"])]]


def _brave(q, days):
    r = requests.get("https://api.search.brave.com/res/v1/web/search", timeout=TIMEOUT,
                     headers={"X-Subscription-Token": _secrets().get("brave_api_key", ""), "Accept": "application/json"},
                     params={"q": q, "count": 20, "freshness": "pw" if days <= 7 else "pm"})
    r.raise_for_status()
    return [(x.get("url"), x.get("title", ""), _strip_tags(x.get("description", "")))
            for x in (r.json().get("web") or {}).get("results", [])]


def _google(q, days):
    s = _secrets()
    r = requests.get("https://www.googleapis.com/customsearch/v1", timeout=TIMEOUT,
                     params={"key": s.get("google_cse_key"), "cx": s.get("google_cse_id"), "q": q, "num": 10,
                             "dateRestrict": f"d{days}"})
    r.raise_for_status()
    return [(x.get("link"), x.get("title", ""), x.get("snippet", "")) for x in r.json().get("items", [])]


def _strip_tags(s):
    return _html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def parse_ddg(page: str) -> list[tuple[str, str, str]]:
    out = []
    for block in re.split(r'<div[^>]+class="[^"]*\bresult\b', page)[1:]:
        a = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a:
            continue
        href = _html.unescape(a.group(1))
        q = parse_qs(urlparse(href).query)
        url = unquote(q["uddg"][0]) if q.get("uddg") else href
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
        out.append((url, _strip_tags(a.group(2)), _strip_tags(sn.group(1) if sn else "")))
    return out


def _ddg(q, days):
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": q, "df": "w" if days <= 7 else "m"},
                      headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    if "anomaly" in r.text[:5000].lower() and "result__a" not in r.text:
        raise RuntimeError("DuckDuckGo asked for a check; try later or add a free Brave Search key")
    return parse_ddg(r.text)


ENGINES = {"brave": _brave, "google": _google, "duckduckgo": _ddg}
POST_RX = re.compile(r"linkedin\.com/(?:posts/[\w%-]+|feed/update/urn:li:(?:activity|share|ugcPost):\d+)", re.I)


def search(cfg, log=print) -> list[dict]:
    o, eng = opts(cfg), engine(cfg)
    hits, seen = [], set()
    for q in post_queries(cfg):
        try:
            res = ENGINES[eng](q, int(o["post_days"]))
        except Exception as e:
            log(f"  linkedin posts: {eng} search failed: {str(e)[:120]}")
            break
        for url, title, snip in res:
            url = (url or "").split("?")[0]
            if url and POST_RX.search(url) and url not in seen:
                seen.add(url)
                hits.append({"url": url, "title": title, "snippet": snip})
        time.sleep(2.0 if eng == "duckduckgo" else 0.3)
    return hits


def read_post(url: str) -> dict | None:
    """A public post page as anyone logged out sees it. Returns None when LinkedIn asks to sign in."""
    try:
        r = _get(url)
    except requests.RequestException:
        return None
    if r.status_code != 200 or "authwall" in r.url or len(r.text) < 2000:
        return None
    page = r.text
    body = re.search(r'<p[^>]+class="[^"]*attributed-text-segment-list__content[^"]*"[^>]*>(.*?)</p>', page, re.S) \
        or re.search(r'<div[^>]+class="[^"]*(?:share-update-card__update-text|feed-shared-update-v2__commentary)'
                     r'[^"]*"[^>]*>(.*?)</div>', page, re.S)
    raw = body.group(1) if body else ""
    links = [{"url": _html.unescape(h), "text": _strip_tags(t)}
             for h, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S)]
    text = _strip_tags(re.sub(r"<br\s*/?>", "\n", raw)) or _meta(page, "description")
    published = re.search(r'"datePublished"\s*:\s*"([^"]+)"', page)
    return {"text": text, "links": links, "title": _meta(page, "title") or "",
            "posted": published.group(1)[:10] if published else None}


def run(cfg, log=print) -> list[dict]:
    """The 'LinkedIn hiring posts' source for a job search. Returns raw jobs (fetch.py filters and stores them)."""
    o = opts(cfg)
    hits = search(cfg, log)
    with conn() as c:
        seen = {r[0] for r in c.execute("SELECT url FROM posts_seen")}
    fresh = [h for h in hits if h["url"] not in seen][: int(o["post_max"])]
    log(f"  linkedin posts: {len(hits)} public posts found via {engine(cfg)}, reading {len(fresh)} new")
    out, done = [], []
    for h in fresh:
        p = read_post(h["url"])
        text = p["text"] if p and len(p["text"]) > len(h["snippet"]) else h["snippet"]
        poster, prof = poster_from(h["url"], (p or {}).get("title") or h["title"])
        jobs = jobs_from_post(text, cfg, links=(p or {}).get("links"), post_url=h["url"], poster=poster,
                              poster_url=prof, posted=(p or {}).get("posted"), log=log)
        out += jobs
        done.append((h["url"], _now(), len(jobs), "search"))
        time.sleep(1.5)
    with conn() as c:
        c.executemany("INSERT OR REPLACE INTO posts_seen VALUES (?,?,?,?)", done)
        c.commit()
    return out


# ============================================================== page + API
def overview(c, cfg) -> dict:
    c.executescript(SCHEMA)
    s = _secrets()
    leads = [dict(r) for r in c.execute(
        """SELECT p.*, j.title, j.company, j.status FROM post_leads p JOIN jobs j ON j.id=p.job_id
           ORDER BY p.found DESC LIMIT 200""")]
    refs = [dict(r) for r in c.execute("SELECT * FROM referral WHERE connections>0 OR alumni>0 OR known>0 "
                                       "ORDER BY known DESC, connections DESC, alumni DESC")]
    since = (datetime.now() - timedelta(days=7)).isoformat()
    return {"leads": leads, "referrals": refs, "engine": engine(cfg), "options": opts(cfg),
            "enabled": bool((cfg.get("sources") or {}).get("linkedin_posts")),
            "queries": post_queries(cfg), "keys": {"brave": bool(s.get("brave_api_key")),
                                                   "google": bool(s.get("google_cse_key") and s.get("google_cse_id"))},
            "posts_week": c.execute("SELECT COUNT(*) FROM posts_seen WHERE seen>?", (since,)).fetchone()[0]}


def handle(h, method, body=None):
    import fetch
    path = urlparse(h.path).path
    cfg = fetch.load_cfg()
    try:
        with conn() as c:
            if method == "GET":
                if path in ("/network", "/network/"):
                    return h._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if path == "/api/network":
                    return h._send(200, overview(c, cfg))
                if path.startswith("/api/network/job/"):
                    return h._send(200, job_help(c, cfg, path.rsplit("/", 1)[1]))
                return h._send(404, {"error": "not found"})
            if path == "/api/network/post":
                return h._send(200, add_post(c, cfg, body, via=body.get("via") or "paste"))
            if path == "/api/network/signal":
                return h._send(200, save_signal(c, body))
            if path == "/api/network/known":
                return h._send(200, set_known(c, body.get("company") or "", bool(body.get("known")),
                                              body.get("note")))
            if path == "/api/network/keys":
                import llm
                llm.save_secrets({k: (body.get(k) or "").strip() for k in ("brave_api_key", "google_cse_key",
                                                                         "google_cse_id") if k in body})
                return h._send(200, {"ok": True, "engine": engine(cfg)})
            if path == "/api/network/source":
                import settings
                raw = json.loads(settings.CONFIG.read_text(encoding="utf-8"))
                raw.setdefault("sources", {})["linkedin_posts"] = bool(body.get("on"))
                settings.CONFIG.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                return h._send(200, {"ok": True})
            return h._send(404, {"error": "not found"})
    except (ValueError, KeyError) as e:
        return h._send(400, {"error": str(e).strip("'")})
