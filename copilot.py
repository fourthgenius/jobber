"""Jobber Copilot: the "after a job is found" layer.

Apply-today queue, follow-ups, fuzzy dedupe + repost flags, AI fit / cover note / tailored CV / outreach /
interview prep (llm.py), funnel analytics, company watchlist, funding radar, and a "save any job" bookmarklet.
Page: /copilot. Hooked from app.py like autofill. Never submits applications or sends messages for you.
"""
from __future__ import annotations

import difflib
import html
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import fetch
import fit as fitmod
import llm
import store
from settings import file_stub, me, money

HERE = Path(__file__).parent
PAGE = HERE / "copilot.html"
PROFILE = HERE / "profile.json"  # batch apply profile (read fresh; only "ai_answers" is written here)

DEFAULTS = {"queue_min_fit": 65, "queue_max_age_days": 2, "followup_after_days": 7, "llm_max_per_run": 30,
            "llm_min_keyword_fit": 50, "alert_top_n": 10, "alert_min_fit": 70,
            "funding_feeds": ["https://techcrunch.com/category/venture/feed/"], "watchlist_seed": [],
            "cv_angles": {}}
SCHEMA = """
CREATE TABLE IF NOT EXISTS ai (job_id TEXT, kind TEXT, data TEXT, created TEXT, PRIMARY KEY (job_id, kind));
CREATE TABLE IF NOT EXISTS meta (job_id TEXT PRIMARY KEY, applied_at TEXT, replied_at TEXT, followed_up TEXT,
                                 cv_variant TEXT);
CREATE TABLE IF NOT EXISTS outreach (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, company TEXT, person TEXT,
                                     role TEXT, status TEXT DEFAULT 'to contact', note TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS watch (company TEXT PRIMARY KEY, careers_url TEXT, added TEXT);
CREATE TABLE IF NOT EXISTS funding (link TEXT PRIMARY KEY, title TEXT, company TEXT, source TEXT, published TEXT,
                                    seen TEXT);
"""
OUT_STATUSES = ["to contact", "sent", "replied", "referred", "no reply"]


# ---------------------------------------------------------------- basics
def conn():
    c = store.conn()
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM watch LIMIT 1").fetchone():
        now = _now()
        seed = settings().get("watchlist_seed") or []
        c.executemany("INSERT OR IGNORE INTO watch VALUES (?,?,?)", [(w, "", now) for w in seed])
        c.commit()
    return c


def _now():
    return datetime.now().isoformat(timespec="seconds")


def settings(cfg=None) -> dict:
    cfg = cfg or fetch.load_cfg()
    return {**DEFAULTS, **cfg.get("copilot", {})}


def norm_company(s: str) -> str:
    return store._norm(s or "")


def _norm_title(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\(.*?\)|[-–|,/].*$", " ", s)  # drop "- Bangalore", "(Remote)" tails
    s = re.sub(r"[^a-z ]", " ", s)
    s = re.sub(r"\b(sr|senior|jr|junior|i|ii|iii|the|of|and|for)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ai_all(c, kind: str) -> dict:
    return {r[0]: json.loads(r[1]) for r in c.execute("SELECT job_id, data FROM ai WHERE kind=?", (kind,))}


def _ai_get(c, jid, kind):
    r = c.execute("SELECT data FROM ai WHERE job_id=? AND kind=?", (jid, kind)).fetchone()
    return json.loads(r[0]) if r else None


def _ai_put(c, jid, kind, data):
    c.execute("INSERT OR REPLACE INTO ai VALUES (?,?,?,?)", (jid, kind, json.dumps(data), _now()))
    c.commit()


def sync_meta(c):
    """Infer applied / replied dates from status changes (the dashboard owns status)."""
    c.execute("""INSERT OR IGNORE INTO meta (job_id) SELECT id FROM jobs
                 WHERE status IN ('applied','interviewing','offer','rejected')""")
    c.execute("""UPDATE meta SET applied_at=(SELECT COALESCE(status_changed, first_seen) FROM jobs WHERE id=job_id)
                 WHERE applied_at IS NULL""")
    c.execute("""UPDATE meta SET replied_at=(SELECT status_changed FROM jobs WHERE id=job_id)
                 WHERE replied_at IS NULL AND job_id IN
                 (SELECT id FROM jobs WHERE status IN ('interviewing','offer','rejected'))""")
    c.commit()


# ---------------------------------------------------------------- enrich: dedupe, reposts, AI fit, watchlist
def enrich(c, cfg) -> list[dict]:
    jobs = store.all_jobs(c, cfg)
    sync_meta(c)
    fits = _ai_all(c, "fit")
    meta = {r["job_id"]: dict(r) for r in c.execute("SELECT * FROM meta")}
    watch = {norm_company(r[0]) for r in c.execute("SELECT company FROM watch")}
    groups: dict[str, list[dict]] = {}
    today = date.today()
    for j in jobs:
        f = fits.get(j["id"])
        j["ai"] = f
        j["best_fit"] = f["score"] if f else j["fit"]
        j["meta"] = meta.get(j["id"], {})
        nc = norm_company(j["company"])
        j["watch"] = bool(nc) and any(w and (w == nc or w in nc.split() or nc.startswith(w)) for w in watch)
        try:
            fs = date.fromisoformat((j.get("first_seen") or "")[:10])
            ls = date.fromisoformat((j.get("last_seen") or "")[:10])
            j["evergreen"] = (ls - fs).days >= 30
        except ValueError:
            j["evergreen"] = False
        j["dup_of"] = None
        groups.setdefault(nc, []).append(j)
    rank = {"offer": 0, "interviewing": 1, "applied": 2, "saved": 3, "new": 4, "rejected": 5, "hidden": 6}
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=lambda d: (rank.get(d["status"], 9), d["age_days"] if d["age_days"] is not None else 99))
        keep: list[dict] = []
        for j in g:
            t = _norm_title(j["title"])
            for k in keep:
                if difflib.SequenceMatcher(None, t, _norm_title(k["title"])).ratio() >= 0.88:
                    j["dup_of"] = k["id"]
                    break
            else:
                keep.append(j)
    _ = today
    return jobs


def queue(jobs, cfg) -> list[dict]:
    s = settings(cfg)
    senior = re.compile(r"\b(senior|sr\.?|lead|principal|staff)\b", re.I)
    out = [j for j in jobs if j["status"] in ("new", "saved") and not j["dup_of"] and not j["filtered"]
           and not (senior.search(j["title"] or "") and (j["exp_min"] is None or j["exp_min"] > 3))
           and j["best_fit"] >= s["queue_min_fit"]
           and (j["age_days"] is None and (j.get("first_seen") or "")[:10] >= str(date.today() - timedelta(days=2))
                or j["age_days"] is not None and j["age_days"] <= s["queue_max_age_days"])]
    out.sort(key=lambda j: (not j["watch"], -j["best_fit"], j["age_days"] if j["age_days"] is not None else 9))
    return out[:40]


def followups(jobs, cfg) -> list[dict]:
    days = settings(cfg)["followup_after_days"]
    out = []
    for j in jobs:
        m = j["meta"]
        if j["status"] != "applied" or m.get("replied_at") or m.get("followed_up") or not m.get("applied_at"):
            continue
        try:
            age = (datetime.now() - datetime.fromisoformat(m["applied_at"])).days
        except ValueError:
            continue
        if age >= days:
            out.append({**j, "applied_days": age})
    return sorted(out, key=lambda j: -j["applied_days"])


# ---------------------------------------------------------------- analytics
def _tier(title: str, tiers) -> str:
    t = (title or "").lower()
    for _, label, kws in tiers:
        if any(k in t for k in kws):
            return label
    return "Other"


def analytics(jobs, cfg=None) -> dict:
    tiers = fitmod.role_tiers(cfg or fetch.load_cfg())
    applied = [j for j in jobs if j["status"] in ("applied", "interviewing", "offer", "rejected")]

    def funnel(key):
        rows: dict[str, dict] = {}
        for j in applied:
            k = key(j) or "unknown"
            r = rows.setdefault(k, {"name": k, "applied": 0, "replied": 0, "interview": 0, "offer": 0})
            r["applied"] += 1
            r["replied"] += j["status"] in ("interviewing", "offer", "rejected")
            r["interview"] += j["status"] in ("interviewing", "offer")
            r["offer"] += j["status"] == "offer"
        out = sorted(rows.values(), key=lambda r: -r["applied"])
        for r in out:
            r["rate"] = round(100 * r["interview"] / r["applied"]) if r["applied"] else 0
        return out

    weeks = []
    for w in range(7, -1, -1):
        start = date.today() - timedelta(days=date.today().weekday() + 7 * w)
        end = start + timedelta(days=7)
        n = sum(1 for j in applied if str(start) <= (j["meta"].get("applied_at") or "")[:10] < str(end))
        weeks.append({"week": start.strftime("%d %b"), "applied": n})
    tot = funnel(lambda j: "All")[0] if applied else {"applied": 0, "replied": 0, "interview": 0, "offer": 0, "rate": 0}
    return {"total": tot, "weeks": weeks,
            "by_source": funnel(lambda j: next(iter(j["sources"]), "")),
            "by_portal": funnel(lambda j: j.get("ats")),
            "by_role": funnel(lambda j: _tier(j["title"], tiers)),
            "by_cv": funnel(lambda j: j["meta"].get("cv_variant") or "base CV"),
            "pipeline": {s: sum(1 for j in jobs if j["status"] == s and not j["dup_of"])
                         for s in ("new", "saved", "applied", "interviewing", "offer", "rejected")}}


# ---------------------------------------------------------------- keyword gap (works without AI)
GENERAL_TERMS = [
            # engineering / data / design / marketing / sales / ops (product terms below)
            "javascript", "typescript", "react", "node", "java", "golang", "kubernetes", "docker", "aws", "gcp",
            "azure", "microservices", "system design", "ci/cd", "terraform", "excel", "power bi", "looker",
            "statistics", "dbt", "spark", "airflow", "pytorch", "tensorflow", "nlp", "computer vision",
            "user experience", "ux", "ui", "prototyping", "design system", "accessibility", "seo", "sem",
            "content marketing", "performance marketing", "crm", "salesforce", "hubspot", "lead generation",
            "account management", "negotiation", "forecasting", "supply chain", "procurement", "budgeting",
            "financial modeling", "project management", "customer success", "communication", "leadership",
]
PM_TERMS = ["a/b testing", "experimentation", "sql", "python", "analytics", "roadmap", "prioritization", "okr",
            "go-to-market", "gtm", "user research", "stakeholder", "growth", "retention", "activation", "conversion",
            "funnel", "monetization", "pricing", "b2b", "b2c", "saas", "api", "platform", "payments", "lending",
            "fintech", "trading", "genai", "llm", "rag", "machine learning", "ai", "mixpanel", "amplitude",
            "tableau", "jira", "figma", "agile", "scrum", "prd", "kpi", "p&l", "market sizing", "competitive analysis",
            "cross-functional", "0 to 1", "product-led growth", "plg", "onboarding", "kyc", "compliance",
            "data-driven", "strategy", "operations", "fundraising", "investor relations", "hiring", "partnerships"]


def keyword_gap(j, cfg=None) -> dict:
    jd = (j.get("description") or "").lower()
    cv = llm.cv_text().lower()
    prof = (cfg or fetch.load_cfg()).get("profile", {})
    terms = list(dict.fromkeys([*(k.lower() for k in prof.get("skills", []) + prof.get("domains", [])),
                                *PM_TERMS, *GENERAL_TERMS]))
    present = [t for t in terms if re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", jd)]
    return {"matched": [t for t in present if t in cv], "missing": [t for t in present if t not in cv]}


# ---------------------------------------------------------------- templates (fallbacks when no API key)
def _first_win():
    """Your headline achievement (Setup > Profile), else the first bullet of your CV."""
    w = me()["win"].strip()
    if w:
        return w.rstrip(".")
    b = next((ln[2:].strip() for ln in llm.cv_text().splitlines() if ln.startswith("- ")), "")
    return (b[:1].lower() + b[1:]).rstrip(".") if b else "delivered measurable results in my current role"


COVER_FILE = HERE / "cover_templates.json"


def cover_templates() -> dict:
    try:
        return json.loads(COVER_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"default_hook": "", "templates": {}}


def save_cover_templates(data: dict):
    cur = cover_templates()
    if isinstance(data.get("templates"), dict):
        for k, t in data["templates"].items():
            if k in cur["templates"] and isinstance(t, dict) and isinstance(t.get("body"), str):
                cur["templates"][k]["body"] = t["body"][:6000]
    if isinstance(data.get("default_hook"), str):
        cur["default_hook"] = data["default_hook"][:400]
    COVER_FILE.write_text(json.dumps(cur, indent=2, ensure_ascii=False), encoding="utf-8")
    return cur


def pick_template(j) -> str:
    T = cover_templates()["templates"]
    title, text = (j.get("title") or "").lower(), (j.get("description") or "").lower()
    best, top = "general", 0
    for k, t in T.items():
        n = sum(3 * (m in f" {title} ") + min(text.count(m), 3) for m in t.get("match", []))
        if n > top:
            best, top = k, n
    return best if best in T else next(iter(T), "")


def render_cover(j, key=None, hook=None) -> dict:
    C = cover_templates()
    key = key if key in C["templates"] else pick_template(j)
    t = C["templates"].get(key, {"body": "", "label": ""})
    co, role = (j.get("company") or "your company").strip(), (j.get("title") or "open").strip()
    role = re.sub(r"\s*[-–|(].*$", "", role) or role  # "PM - Bangalore (Remote)" -> "PM"
    h = (hook or C.get("default_hook") or "").replace("{company}", co)
    text = t["body"]
    m = me()
    for k, v in {"{role}": role, "{company}": co, "{hook}": h, "{name}": m["name"],
                 "{headline}": _pitch() or "a professional in this field",
                 "{achievement}": _first_win(), "{current_company}": m["company"] or "my current company",
                 "{current_title}": m["title"] or "my current role",
                 "{date}": date.today().strftime("%d %B %Y")}.items():
        text = text.replace(k, v)
    return {"text": re.sub(r"[ \t]+\n", "\n", text).strip(), "template": key, "label": t.get("label", key),
            "templates": {k: v.get("label", k) for k, v in C["templates"].items()}}


def tpl_cover(j):
    return render_cover(j)["text"]


def find_job_by_url(c, cfg, url: str):
    u = (url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    if not u:
        return None
    for j in store.all_jobs(c, cfg):
        for cand in [j.get("apply_url"), j.get("url"), *j.get("sources", {}).values()]:
            cu = (cand or "").split("#")[0].split("?")[0].rstrip("/").lower()
            if cu and (u.startswith(cu) or cu.startswith(u)):
                return j
    return None


def _lc(s: str) -> str:
    """'Data analyst ...' -> 'data analyst ...' (keeps acronyms like 'PM' or 'AI')."""
    return s[:1].lower() + s[1:] if len(s) > 1 and not s[1].isupper() else s


def _pitch() -> str:
    m = me()
    who = m["headline"] or (f"a {m['title']} at {m['company']}" if m["company"] and m["title"] else m["title"] or "")
    return _lc(who.strip())


def tpl_outreach(j):
    co, role, m = j["company"], j["title"][:60], me()
    who = _pitch()
    intro = f"I'm {who}" if who else "I"
    school = m["school"]
    return {"hiring_manager_note": f"Hi! {intro}{',' if who else ''} and I just applied for {role} at {co}. "
                                   f"Recently I {_first_win()[:110]}. Would love 10 mins to learn what you're solving.",
            "alumni_note": (f"Hi! Fellow {school} alum here. " if school else "Hi! ") +
                           f"I'm applying for {role} at {co}. Would you be open to a quick chat or a referral?",
            "recruiter_note": f"Hi! I applied for {role} at {co}." + (f" I'm {who}." if who else "") +
                              (f" Notice period: {m['notice']}." if m["notice"] else "") +
                              " Happy to share anything that helps.",
            "who_to_find": ["The hiring manager for this team", f"{school} alumni at the company" if school
                            else "Former colleagues at the company", "Talent acquisition / recruiters"]}


def tpl_follow(j, days):
    m = me()
    return (f"Subject: Following up on my application for {j['title']}\n\nHi,\n\nI applied for the {j['title']} "
            f"role at {j['company']} {days} days ago and wanted to check on next steps. "
            f"{'As ' + _pitch() + ', I' if _pitch() else 'I'} {_first_win()}; I think that maps well to this role. "
            f"Happy to share more.\n\nThanks,\n{m['name']}")


def people_links(j) -> dict:
    co, m = j["company"], me()
    role = (fetch.load_cfg().get("search_terms") or ["hiring manager"])[0]
    li = "https://www.linkedin.com/search/results/people/?keywords="
    out = {"Hiring manager": li + quote_plus(f"{co} {role} manager")}
    if m["school"]:
        out[f"{m['school']} alumni"] = li + quote_plus(f"{co} {m['school']}")
    if m["company"]:
        out[f"Ex-{m['company']} there"] = li + quote_plus(f"{co} {m['company']}")
    out["Recruiters"] = li + quote_plus(f"{co} talent acquisition")
    return out


# ---------------------------------------------------------------- AI actions (cached)
def ai_action(c, cfg, kind, jid, body) -> dict:
    j = next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), None)
    if not j:
        raise KeyError("job not found")
    force = bool(body.get("force"))
    cached = None if force else _ai_get(c, jid, kind)
    if cached is not None and kind != "answers":
        return {"data": cached, "cached": True, "ai": True}
    on = llm.enabled()
    if kind == "fit":
        if not on:
            raise llm.NoKey("Add your Claude API key to score with AI.")
        data = llm.fit(j)
    elif kind == "cover":
        saved = _ai_get(c, jid, "cover_edit")
        if body.get("save") is not None:  # user edited the letter
            _ai_put(c, jid, "cover_edit", {"text": str(body["save"])[:8000], "template": body.get("template")})
            return {"data": {"text": str(body["save"])}, "cached": True, "ai": False}
        if saved and not body.get("template") and not body.get("hook") and not force:
            r = render_cover(j, saved.get("template"))
            return {"data": {**r, "text": saved["text"], "edited": True}, "cached": True, "ai": False}
        r = render_cover(j, body.get("template"), body.get("hook"))
        if on and body.get("ai"):
            r["text"] = llm.cover_note(j)
        return {"data": r, "cached": False, "ai": on and bool(body.get("ai"))}
    elif kind == "outreach":
        data = llm.outreach(j) if on else tpl_outreach(j)
        data["links"] = people_links(j)
    elif kind == "followup":
        days = int(body.get("days") or 7)
        data = llm.follow_up(j, days) if on else tpl_follow(j, days)
    elif kind == "prep":
        data = llm.interview_prep(j) if on else rule_prep(j)
    elif kind == "cv":
        f = _ai_get(c, jid, "fit") or {}
        kws = f.get("keywords") or keyword_gap(j)["missing"]
        data = llm.tailor_cv(j, kws) if on else rule_cv(j)
        c.execute("INSERT OR IGNORE INTO meta (job_id) VALUES (?)", (jid,))
        c.execute("UPDATE meta SET cv_variant=? WHERE job_id=?",
                  (("ai:" if on else "rule:") + (f.get("best_cv") or angle_of(j)), jid))
    elif kind == "answers":
        qs = [q for q in (body.get("questions") or []) if isinstance(q, str) and q.strip()][:12]
        if not qs:
            raise ValueError("Paste at least one question.")
        if not on:
            raise llm.NoKey("Add your Claude API key to draft answers.")
        data = llm.screening_answers(j, qs)
        _save_ai_answers(data)
    else:
        raise ValueError("unknown action")
    if on or kind in ("cv", "prep"):
        _ai_put(c, jid, kind, data)
    return {"data": data, "cached": False, "ai": on}


def _save_ai_answers(items):
    """Expose AI answers to batch apply under profile.json -> ai_answers (same key format as 'answers')."""
    if not PROFILE.exists():
        return
    try:
        p = json.loads(PROFILE.read_text(encoding="utf-8"))
    except ValueError:
        return
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()  # noqa: E731
    p.setdefault("ai_answers", {}).update({norm(a["question"]): a["answer"] for a in items if a.get("question")})
    PROFILE.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")


def score_new(c, cfg, log=print) -> int:
    """AI-score the most promising unscored jobs (capped per run to control cost)."""
    if not llm.enabled():
        return 0
    s = settings(cfg)
    done = set(_ai_all(c, "fit"))
    cand = [j for j in enrich(c, cfg) if j["id"] not in done and not j["dup_of"] and not j["filtered"]
            and j["status"] in ("new", "saved") and j["fit"] >= s["llm_min_keyword_fit"]
            and (j["age_days"] is None or j["age_days"] <= 7)]
    cand.sort(key=lambda j: (not j["watch"], -j["fit"]))
    n = 0
    for j in cand[: s["llm_max_per_run"]]:
        try:
            _ai_put(c, j["id"], "fit", llm.fit(j))
            n += 1
        except Exception as e:  # keep going; one bad JD shouldn't stop the run
            log(f"  AI fit failed for {j['company']}: {str(e)[:100]}")
            if "auth" in str(e).lower() or "credit" in str(e).lower():
                break
    log(f"AI fit: scored {n} jobs")
    return n


# ---------------------------------------------------------------- funding radar
RAISE = re.compile(r"^(.{2,60}?)\s+(?:raises|bags|secures|lands|nets|gets|picks up|closes)\b", re.I)
FUND_HINT = re.compile(r"raise|funding|series [a-e]|seed|pre-series|crore|\$\d", re.I)


def funding_refresh(c, cfg, log=print) -> int:
    import requests
    from sources import UA
    new = 0
    for feed in settings(cfg)["funding_feeds"]:
        try:
            r = requests.get(feed, headers={**UA, "Accept": "application/rss+xml, application/xml"}, timeout=30)
            root = ET.fromstring(r.content.lstrip(b"\xef\xbb\xbf \r\n\t"))
        except Exception as e:
            log(f"  funding feed failed {urlparse(feed).netloc}: {str(e)[:80]}")
            continue
        for it in root.iter("item"):
            title = html.unescape((it.findtext("title") or "").strip())
            link = (it.findtext("link") or "").strip()
            if not title or not link or not FUND_HINT.search(title):
                continue
            m = RAISE.search(title)
            company = re.sub(r"^(?:[\w-]+-backed|[\w-]+ startup|edtech|fintech|healthtech|saas)\s+", "",
                             m.group(1), flags=re.I).strip(" '\"") if m else ""
            new += c.execute("INSERT OR IGNORE INTO funding VALUES (?,?,?,?,?,?)",
                             (link, title, company, urlparse(feed).netloc.replace("www.", ""),
                              (it.findtext("pubDate") or "")[:16], _now())).rowcount
    c.commit()
    log(f"Funding radar: {new} new funding stories")
    return new


def funding_list(c) -> list[dict]:
    term = (fetch.load_cfg().get("search_terms") or [""])[0]
    jobs_by_co = {}
    for r in c.execute("SELECT company, COUNT(*) n FROM jobs WHERE status!='hidden' GROUP BY company"):
        jobs_by_co[norm_company(r[0])] = r[1]
    watch = {norm_company(r[0]) for r in c.execute("SELECT company FROM watch")}
    out = []
    for r in c.execute("SELECT * FROM funding ORDER BY seen DESC, published DESC LIMIT 60"):
        d = dict(r)
        nc = norm_company(d["company"])
        d["open_jobs"] = jobs_by_co.get(nc, 0) if nc else 0
        d["watching"] = nc in watch
        d["careers_search"] = "https://www.google.com/search?q=" + quote_plus(
            f"{d['company'] or d['title']} careers {term}")
        out.append(d)
    return out


# ---------------------------------------------------------------- company directory (Radar)
def _co_key(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", name or "")
    return norm_company(n.split("/")[0])


def companies(c) -> dict:
    try:
        data = json.loads((HERE / "companies.json").read_text(encoding="utf-8"))["companies"]
    except (OSError, ValueError, KeyError):
        data = []
    counts: dict[str, int] = {}
    for r in c.execute("SELECT company FROM jobs WHERE status IN ('new','saved') AND COALESCE(filtered,'')=''"):
        k = norm_company(r[0])
        counts[k] = counts.get(k, 0) + 1
    watch = {norm_company(r[0]) for r in c.execute("SELECT company FROM watch")}
    cfg = fetch.load_cfg()
    term, country = (cfg.get("search_terms") or [""])[0], cfg.get("country") or ""
    out = []
    for co in data:
        k = _co_key(co["name"])
        n = sum(v for ck, v in counts.items() if ck and (ck == k or ck.startswith(k + " ") or k.startswith(ck + " ")))
        q = co["name"].split("(")[0].split("/")[0].strip()
        out.append({**co, "open_jobs": n, "watching": k in watch or any(w == k for w in watch),
                    "careers": "https://www.google.com/search?q=" + quote_plus(f"{q} careers {term} {country}".strip()),
                    "linkedin": "https://www.linkedin.com/jobs/search/?keywords=" + quote_plus(f"{q} {term}".strip())
                                + ("&location=" + quote_plus(country) if country else "")})
    return {"companies": out}


# ---------------------------------------------------------------- watchlist + saving jobs from anywhere
def watch_add(c, company, url=""):
    import ats
    company = (company or "").strip()[:80]
    if not company:
        raise ValueError("company name needed")
    c.execute("INSERT OR REPLACE INTO watch VALUES (?,?,?)", (company, (url or "").strip()[:300], _now()))
    learned = ats.remember(c, [url]) if url else 0
    c.commit()
    return {"ok": True, "portal_tracked": bool(learned) or bool(url and ats.detect(url))}


def save_job(c, cfg, f) -> str:
    """Save a job seen anywhere (Wellfound, iimjobs, Instahyre, a company site...) into the tracker."""
    import ats
    import filters
    from sources import job
    url = (f.get("url") or "").strip()
    if not url.startswith("http"):
        raise ValueError("missing job link")
    j = job(source=urlparse(url).netloc.replace("www.", "") or "saved", title=(f.get("title") or "").strip()[:200],
            company=(f.get("company") or "").strip()[:120], location=(f.get("location") or "").strip()[:120],
            url=url, apply_url=url, posted=str(date.today()), description=(f.get("text") or "")[:6000])
    if not j["title"] or not j["company"]:
        raise ValueError("title and company are needed")
    j["remote"] = "remote" in (j["location"] + j["title"]).lower()
    ev = filters.evaluate(j, cfg) or dict(location_bucket=filters.location_bucket(j, cfg) or "other",
                                          sal_min_lpa=None, sal_max_lpa=None, salary_listed=False,
                                          exp_min=None, exp_max=None, score=0, filtered="")
    j.update(ev)
    j["ats"] = ats.ats_of(url)
    store.upsert(c, j)
    ats.remember(c, [url])
    jid = store.key(j)
    store.update(c, jid, status="saved")
    return jid


BOOKMARKLET = ("javascript:(()=>{const s=(window.getSelection()+'').trim();const t=(s||document.body.innerText)"
               ".slice(0,4500);const og=(n)=>(document.querySelector('meta[property=\"og:'+n+'\"]')||{}).content||'';"
               "const u='http://127.0.0.1:8765/copilot/save?'+new URLSearchParams({url:location.href,title:og('title')"
               "||document.title,site:og('site_name')||location.hostname,text:t});window.open(u,'_blank',"
               "'width=560,height=640');})()")


def _save_form(q: dict, msg: str = "") -> bytes:
    title = q.get("title", "")
    company = q.get("company") or q.get("site", "")
    m = re.match(r"^(.*?)\s+(?:at|@|-|–|\|)\s+(.*?)(?:\s+[-–|].*)?$", title)
    if m and not q.get("company"):
        title, company = m.group(1), m.group(2)
    e = lambda s: html.escape(s or "", quote=True)  # noqa: E731
    body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><title>Save to Jobber</title><link rel="stylesheet" href="/theme.css"></head>
<body><div class="wrap" style="max-width:520px;padding-top:24px"><h2 style="font-family:var(--f-display)">Save to
Jobber</h2>{f'<p class="pill good">{e(msg)}</p>' if msg else ''}
<form method="post" action="/copilot/save" class="panel" style="padding:16px;display:grid;gap:10px">
<label class="label">Role title<input name="title" value="{e(title)}" required style="width:100%;padding:8px"></label>
<label class="label">Company<input name="company" value="{e(company)}" required style="width:100%;padding:8px"></label>
<label class="label">Location<input name="location" value="{e(q.get('location', ''))}" placeholder="City / Remote"
style="width:100%;padding:8px"></label><input type="hidden" name="url" value="{e(q.get('url', ''))}">
<label class="label">Job description (edit if needed)<textarea name="text" rows="8" style="width:100%;padding:8px">{e(q.get('text', ''))}</textarea></label>
<button class="btn primary">Save job</button></form><p class="small muted">Saved jobs land in Saved on the dashboard and
in the Copilot queue.</p></div></body></html>"""
    return body.encode()


# ---------------------------------------------------------------- tailored CV page
def _md_html(md: str) -> str:
    out, inlist = [], False
    for line in md.splitlines():
        s = html.escape(line.strip())
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*", r"<i>\1</i>", s)
        if s.startswith("- "):
            if not inlist:
                out.append("<ul>")
                inlist = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        if inlist:
            out.append("</ul>")
            inlist = False
        if s.startswith("### "):
            out.append(f"<h3>{s[4:]}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2>{s[3:]}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1>{s[2:]}</h1>")
        elif s:
            out.append(f"<p>{s}</p>")
    if inlist:
        out.append("</ul>")
    return "\n".join(out)


def cv_page(c, cfg, jid) -> bytes:
    md = _ai_get(c, jid, "cv")
    j = next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), {"title": "", "company": ""})
    note = "" if md else "<p class='np'>No tailored version yet: showing your base CV.</p>"
    md = md or llm.cv_text()
    fname = file_stub("CV", j.get("company", ""))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(fname)}</title>
<style>@page{{size:A4;margin:12mm 14mm}}body{{font:10.5pt/1.38 "Segoe UI",Calibri,Arial,sans-serif;color:#111;
max-width:820px;margin:20px auto;padding:0 16px}}h1{{font-size:20pt;margin:0;text-align:center}}h1+p{{text-align:center;
margin:2px 0 8px;color:#333}}h2{{font-size:11pt;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid
#999;margin:12px 0 4px}}h3{{font-size:10.5pt;margin:8px 0 0}}p{{margin:2px 0}}ul{{margin:2px 0 4px 18px;padding:0}}
li{{margin:1px 0}}.np{{background:#fff4d6;padding:8px;border-radius:6px}}.bar{{position:sticky;top:0;background:#fff;
padding:8px 0;display:flex;gap:8px}}button{{padding:8px 14px;font-weight:600;cursor:pointer}}
@media print{{.bar,.np{{display:none}}body{{margin:0}}}}</style></head><body>
<div class="bar"><button onclick="print()">Print / Save as PDF</button><span style="color:#666;align-self:center">
Choose "Save as PDF", file name {html.escape(fname)}.pdf. Check every line is true before using it.</span></div>
{note}{_md_html(md)}</body></html>""".encode()


# ---------------------------------------------------------------- rule-based CV tailoring + prep (no API key)
def angles() -> dict:
    """CV angles from config "copilot" -> "cv_angles": {"name": {"keywords": [...], "summary": "..."}}.
    The rule-based tailor swaps in the matching summary and reorders bullets; it never adds facts."""
    out = {}
    for k, v in (settings().get("cv_angles") or {}).items():
        if isinstance(v, dict):
            out[k] = (v.get("keywords") or [], v.get("summary") or "")
    return out


def angle_of(j) -> str:
    text = f"{j.get('title', '')} {j.get('description') or ''}".lower()
    best, hits = "general", 0
    A = angles()
    for name in A:  # tie-break: config order
        kws = A[name][0]
        n = sum(len(re.findall(rf"(?<![a-z]){re.escape(k.strip())}(?:s|es)?(?![a-z])", text)) for k in kws)
        n += 3 if any(re.search(rf"(?<![a-z]){re.escape(k.strip())}(?![a-z])", (j.get("title") or "").lower())
                      for k in kws) else 0
        if n > hits:
            best, hits = name, n
    return best


def rule_cv(j) -> str:
    """Reorder bullets by JD relevance and swap in a role-specific summary. Never adds facts."""
    md = llm.cv_text()
    angle = angle_of(j)
    ANGLES = {k: v for k, v in angles().items() if v[1]}
    jd = (j.get("description") or "").lower() + " " + (j.get("title") or "").lower()
    words = set(re.findall(r"[a-z][a-z0-9+/&-]{2,}", jd)) - {"the", "and", "for", "with", "you", "our", "will"}
    kws = ANGLES.get(angle, ([], ""))[0]

    def rel(b):
        bl = b.lower()
        return sum(1 for w in set(re.findall(r"[a-z][a-z0-9+/&-]{2,}", bl)) if w in words) + \
            3 * sum(1 for k in kws if k.strip() in bl)

    out, block = [], []

    def flush():
        out.extend(sorted(block, key=rel, reverse=True))
        block.clear()
    lines = md.splitlines()
    in_summary = False
    for ln in lines:
        if ln.startswith("- ") and not in_summary:
            block.append(ln)
            continue
        flush()
        if ln.startswith("## "):
            in_summary = ln.strip().lower() == "## summary"
            out.append(ln)
            if in_summary and angle in ANGLES:
                out.append(ANGLES[angle][1])
            continue
        if in_summary and angle in ANGLES and ln.strip():
            continue  # replaced by the angle summary
        out.append(ln)
    flush()
    return "\n".join(out)


def rule_prep(j) -> str:
    import prep
    return prep.rule_pack(j)


def cover_page(c, cfg, jid) -> bytes:
    j = next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), None) or {"title": "", "company": ""}
    saved = _ai_get(c, jid, "cover_edit")
    text = saved["text"] if saved else render_cover(j)["text"]
    fname = file_stub("Cover_Letter", j.get("company", ""))
    m = me()
    contact = " · ".join(html.escape(x) for x in (m["email"], m["phone"], m["location"]) if x)
    paras = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in text.split("\n\n"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(fname)}</title>
<style>@page{{size:A4;margin:22mm 22mm}}body{{font:11pt/1.55 Georgia,"Times New Roman",serif;color:#111;max-width:720px;
margin:24px auto;padding:0 16px}}.hd{{border-bottom:1px solid #999;padding-bottom:8px;margin-bottom:18px}}
.hd b{{font-size:15pt}}.hd div{{font:9.5pt/1.4 Arial,sans-serif;color:#444}}p{{margin:0 0 10px}}
.bar{{position:sticky;top:0;background:#fff;padding:8px 0;display:flex;gap:8px;font:13px Arial,sans-serif}}
button{{padding:8px 14px;font-weight:600;cursor:pointer}}@media print{{.bar{{display:none}}body{{margin:0}}}}</style></head>
<body><div class="bar"><button onclick="print()">Print / Save as PDF</button><span style="color:#666;align-self:center">
Save as {html.escape(fname)}.pdf</span></div><div class="hd"><b>{html.escape(m["name"])}</b><div>{contact + " · " if contact else ""}{date.today().strftime("%d %B %Y")}</div></div>{paras}</body></html>""".encode()


# ---------------------------------------------------------------- Windows daily schedule
def install_schedule(time_hhmm="08:30") -> dict:
    if sys.platform != "win32":
        raise RuntimeError("Daily schedule setup is for Windows; on Mac/Linux add `python alerts.py --fetch` to cron.")
    bat = HERE / "daily.bat"
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", time_hhmm, "/TN", "JobberDaily",
                        "/TR", f'"{bat}"'], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return {"ok": True, "time": time_hhmm}


# ---------------------------------------------------------------- HTTP
def _card(j, cfg=None) -> dict:
    cfg = cfg or fetch.load_cfg()
    keep = ("id", "title", "company", "location", "status", "salary_text", "sal_max_lpa", "age_days", "fit",
            "best_fit", "ai", "watch", "evergreen", "dup_of", "apply_url", "url", "ats", "sources", "fit_pros",
            "fit_gaps", "exp_min", "exp_max", "notes", "meta", "applied_days", "filtered")
    return {**{k: j.get(k) for k in keep}, "pay": money(j.get("sal_max_lpa"), cfg),
            "pay_ok": j.get("sal_max_lpa") is not None and j["sal_max_lpa"] >= cfg["min_salary"]}


def overview(c, cfg) -> dict:
    jobs = enrich(c, cfg)
    s = llm.secrets()
    tg = bool((s.get("telegram_bot_token") or "").strip()) and not s["telegram_bot_token"].startswith("PASTE") \
        and bool(str(s.get("telegram_chat_id") or "").strip())
    by_id = {j["id"]: j for j in jobs}
    outreach_rows = [dict(r) for r in c.execute("SELECT * FROM outreach ORDER BY updated DESC")]
    return {
        "queue": [_card(j, cfg) for j in queue(jobs, cfg)],
        "followups": [_card(j, cfg) for j in followups(jobs, cfg)],
        "interviews": [_card(j, cfg) for j in jobs if j["status"] == "interviewing"],
        "applied": [_card(j, cfg) for j in jobs if j["status"] in ("applied", "saved") and not j["dup_of"]][:80],
        "duplicates": sum(1 for j in jobs if j["dup_of"]),
        "evergreen": sum(1 for j in jobs if j["evergreen"] and j["status"] == "new"),
        "analytics": analytics(jobs, cfg),
        "outreach": [{**o, "job": _card(by_id[o["job_id"]], cfg) if o["job_id"] in by_id else None} for o in outreach_rows],
        "watch": [dict(r) for r in c.execute("SELECT * FROM watch ORDER BY company")],
        "funding": funding_list(c),
        "settings": {**settings(cfg), "ai": llm.enabled(), "money": cfg["currency"],
                     "min_salary": cfg["min_salary"], "telegram": tg, "model": s.get("model") or llm.MODEL,
                     "bookmarklet": BOOKMARKLET},
    }


def handle(h, method, body=None):
    path = urlparse(h.path).path
    q = {k: v[0] for k, v in parse_qs(urlparse(h.path).query).items()}
    cfg = fetch.load_cfg()
    try:
        with conn() as c:
            if method == "GET":
                if path in ("/copilot", "/copilot/"):
                    return h._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if path == "/copilot/save":
                    return h._send(200, _save_form(q), "text/html; charset=utf-8")
                if path.startswith("/copilot/cv/"):
                    return h._send(200, cv_page(c, cfg, path.rsplit("/", 1)[1]), "text/html; charset=utf-8")
                if path == "/api/copilot":
                    return h._send(200, overview(c, cfg))
                if path == "/api/copilot/cover":  # used by the Chrome extension on any application form
                    j = find_job_by_url(c, cfg, q.get("url", "")) or {"title": q.get("title", ""),
                                                                      "company": q.get("company", "")}
                    saved = _ai_get(c, j["id"], "cover_edit") if j.get("id") else None
                    r = render_cover(j)
                    if saved:
                        r["text"] = saved["text"]
                    return h._send(200, {**r, "job_id": j.get("id"), "matched": bool(j.get("id"))})
                if path == "/api/copilot/cover-templates":
                    return h._send(200, cover_templates())
                if path == "/api/copilot/companies":
                    return h._send(200, companies(c))
                if path.startswith("/copilot/cover/"):
                    return h._send(200, cover_page(c, cfg, path.rsplit("/", 1)[1]), "text/html; charset=utf-8")
                if path.startswith("/api/copilot/gap/"):
                    jid = path.rsplit("/", 1)[1]
                    j = next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), None)
                    return h._send(200, keyword_gap(j) if j else {"matched": [], "missing": []})
                return h._send(404, {"error": "not found"})
            # POST
            if path == "/copilot/save":  # form post from the bookmarklet popup (urlencoded)
                try:
                    save_job(c, cfg, body)
                    return h._send(200, _save_form({}, "Saved. You can close this window."), "text/html; charset=utf-8")
                except ValueError as e:
                    return h._send(200, _save_form(body, f"Not saved: {e}"), "text/html; charset=utf-8")
            m = re.match(r"^/api/copilot/ai/(\w+)/([\w-]+)$", path)
            if m:
                return h._send(200, ai_action(c, cfg, m.group(1), m.group(2), body))
            if path == "/api/copilot/cover-templates":
                return h._send(200, save_cover_templates(body))
            if path == "/api/copilot/score":
                return h._send(200, {"scored": score_new(c, cfg)})
            if path == "/api/copilot/followed":
                c.execute("INSERT OR IGNORE INTO meta (job_id) VALUES (?)", (body["id"],))
                c.execute("UPDATE meta SET followed_up=? WHERE job_id=?", (_now(), body["id"]))
                c.commit()
                return h._send(200, {"ok": True})
            if path == "/api/copilot/outreach":
                now = _now()
                if body.get("delete"):
                    c.execute("DELETE FROM outreach WHERE id=?", (body["id"],))
                elif body.get("id"):
                    st = body.get("status")
                    if st and st not in OUT_STATUSES:
                        raise ValueError("bad status")
                    c.execute("UPDATE outreach SET status=COALESCE(?,status), note=COALESCE(?,note), updated=? "
                              "WHERE id=?", (st, body.get("note"), now, body["id"]))
                else:
                    c.execute("INSERT INTO outreach (job_id, company, person, role, note, updated) VALUES "
                              "(?,?,?,?,?,?)", (body.get("job_id"), body.get("company", "")[:120],
                                                body.get("person", "")[:120], body.get("role", "")[:120],
                                                body.get("note", "")[:1000], now))
                c.commit()
                return h._send(200, {"ok": True})
            if path == "/api/copilot/watch":
                if body.get("delete"):
                    c.execute("DELETE FROM watch WHERE company=?", (body["company"],))
                    c.commit()
                    return h._send(200, {"ok": True})
                return h._send(200, watch_add(c, body.get("company"), body.get("url", "")))
            if path == "/api/copilot/funding":
                return h._send(200, {"new": funding_refresh(c, cfg)})
            if path == "/api/copilot/secrets":
                llm.save_secrets({k: body[k] for k in ("anthropic_api_key", "telegram_bot_token",
                                                       "telegram_chat_id", "model") if body.get(k)})
                return h._send(200, {"ok": True, "ai": llm.enabled()})
            if path == "/api/copilot/telegram/test":
                import alerts
                return h._send(200, alerts.test())
            if path == "/api/copilot/telegram/chatid":
                import alerts
                return h._send(200, alerts.find_chat_id())
            if path == "/api/copilot/alert":
                import alerts
                return h._send(200, alerts.send_digest())
            if path == "/api/copilot/open-extension":
                ext = HERE / "chrome-extension"
                if sys.platform != "win32" or not ext.exists():
                    raise RuntimeError(f"Open this folder yourself: {ext}")
                import os
                os.startfile(str(ext))  # noqa: S606 (opens Explorer on the user's own machine)
                return h._send(200, {"ok": True})
            if path == "/api/copilot/schedule":
                return h._send(200, install_schedule(body.get("time") or "08:30"))
            return h._send(404, {"error": "not found"})
    except llm.NoKey as e:
        return h._send(400, {"error": str(e), "need_key": True})
    except (ValueError, KeyError, RuntimeError) as e:
        return h._send(400, {"error": str(e)})


def post_fetch(log=print) -> dict:
    """Runs after every fetch: AI-score new jobs, refresh funding radar."""
    cfg = fetch.load_cfg()
    out = {}
    with conn() as c:
        try:
            out["ai_scored"] = score_new(c, cfg, log)
        except Exception as e:
            log(f"AI fit skipped: {str(e)[:120]}")
        try:
            out["funding_new"] = funding_refresh(c, cfg, log)
        except Exception as e:
            log(f"Funding radar skipped: {str(e)[:120]}")
    return out
