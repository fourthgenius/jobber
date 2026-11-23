"""Interview prep, tailored to one job: company brief, questions from the JD and your CV gaps, suggested
answers built from your own CV and stories, and a mock interview that scores each answer and follows up.

Works fully without an AI key (coach.py: JD parsing, free company sources, a scoring rubric). With a Claude
API key in secrets.json the brief, questions and feedback are written by Claude instead (llm.py).
Page: /prep (?job=<id>). Routes: GET /api/prep, POST /api/prep/{job,answer,notes,story,brief,mock,practiced,jobq}.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import coach
import fetch
import llm
import store
from settings import money

HERE = Path(__file__).parent
PAGE = HERE / "prep.html"
QFILE = HERE / "questions.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, situation TEXT, task TEXT,
                                    action TEXT, result TEXT, tags TEXT DEFAULT '[]', updated TEXT);
CREATE TABLE IF NOT EXISTS mock (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, question TEXT, answer TEXT,
                                 rating INTEGER, seconds INTEGER, feedback TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS practiced (question TEXT PRIMARY KEY, n INTEGER DEFAULT 0, last TEXT);
CREATE TABLE IF NOT EXISTS prep_notes (company TEXT PRIMARY KEY, notes TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS prep_cache (job_id TEXT PRIMARY KEY, data TEXT, ai INTEGER DEFAULT 0, updated TEXT);
"""


def conn():
    c = store.conn()
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(mock)")}
    for col in ("job_id", "scores"):
        if col not in cols:
            c.execute(f"ALTER TABLE mock ADD COLUMN {col} TEXT")
    return c


def _now():
    return datetime.now().isoformat(timespec="seconds")


def bank() -> dict:
    try:
        return json.loads(QFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"roles": {"general": {"label": "General", "match": [], "questions": []}}, "ask_them": [],
                "competencies": []}


def role_of(title: str, cfg: dict | None = None) -> str:
    """Best role type for a job title (or, with no title, for your search terms)."""
    roles = bank()["roles"]
    texts = [title] if title else (cfg or fetch.load_cfg()).get("search_terms", [])
    best, top = "general", 0
    for k, r in roles.items():
        n = sum(1 for t in texts for m in r.get("match", []) if re.search(rf"(?<![a-z]){re.escape(m)}(?![a-z])",
                                                                          (t or "").lower()))
        if n > top:
            best, top = k, n
    return best


# ---------------------------------------------------------------- stories
def stories(c) -> list[dict]:
    out = []
    for r in c.execute("SELECT * FROM stories ORDER BY updated DESC"):
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        out.append(d)
    return out


def save_story(c, b: dict) -> dict:
    if b.get("delete"):
        c.execute("DELETE FROM stories WHERE id=?", (b["id"],))
    else:
        f = {k: str(b.get(k) or "")[:3000] for k in ("title", "situation", "task", "action", "result")}
        if not f["title"].strip():
            raise ValueError("Give the story a short title.")
        tags = json.dumps([str(t)[:40] for t in (b.get("tags") or [])][:10])
        if b.get("id"):
            c.execute("UPDATE stories SET title=?, situation=?, task=?, action=?, result=?, tags=?, updated=? "
                      "WHERE id=?", (*f.values(), tags, _now(), b["id"]))
        else:
            c.execute("INSERT INTO stories (title, situation, task, action, result, tags, updated) "
                      "VALUES (?,?,?,?,?,?,?)", (*f.values(), tags, _now()))
    c.commit()
    return {"stories": stories(c)}


# ---------------------------------------------------------------- company brief (rule-based)
_ABOUT = re.compile(r"\b(we are|we're|our mission|we build|we help|founded|backed by|our customers|our platform|"
                    r"is a leading|is building)\b", re.I)
_WANT = re.compile(r"\b(you will|you'll|responsib|require|must have|you have|experience (with|in)|"
                   r"looking for|nice to have|bonus|qualifications?)\b", re.I)


def _sentences(text: str) -> list[str]:
    text = re.sub(r"[*#_`>|]+", " ", text or "")
    parts = re.split(r"(?<=[.!?])\s+|\n+|\s+[-•·]\s+", text)
    return [re.sub(r"\s+", " ", p).strip(" -•·") for p in parts if 30 <= len(p.strip()) <= 260]


def research_links(company: str) -> dict:
    q = quote_plus(company)
    return {"Website & about": f"https://www.google.com/search?q={q}+about+us",
            "Recent news": f"https://news.google.com/search?q={q}",
            "Employee reviews": f"https://www.google.com/search?q={q}+glassdoor+reviews",
            "Interview experiences": f"https://www.google.com/search?q={q}+interview+questions+experience",
            "People on LinkedIn": f"https://www.linkedin.com/search/results/people/?keywords={q}",
            "Funding & size": f"https://www.google.com/search?q={q}+funding+employees"}


def _company_info(company: str) -> dict | None:
    try:
        data = json.loads((HERE / "companies.json").read_text(encoding="utf-8"))["companies"]
    except (OSError, ValueError, KeyError):
        return None
    k = store._norm(company)
    return next((co for co in data if store._norm(co["name"].split("(")[0]) == k), None)


def _gap(j) -> dict:
    import copilot
    return copilot.keyword_gap(j)


def likely_questions(j: dict, n_role: int = 6) -> list[dict]:
    b = bank()
    role = role_of(j.get("title") or "")
    qs = [dict(q, src=b["roles"][role]["label"]) for q in b["roles"][role]["questions"][:n_role]]
    if role != "general":
        qs += [dict(q, src="Behavioural") for q in b["roles"]["general"]["questions"][:4]]
    g = _gap(j) if j.get("description") else {"matched": [], "missing": []}
    for kw in g["missing"][:3]:
        qs.append({"q": f"This role needs {kw}. What's your experience with it?", "type": "from the JD",
                   "tip": "Be honest; show the closest thing you've done and how fast you'd learn it.", "src": "JD"})
    for kw in g["matched"][:2]:
        qs.append({"q": f"Walk me through a time you used {kw} to get a result.", "type": "from the JD",
                   "tip": "Use a STAR story with a number.", "src": "JD"})
    return qs


def _rank_stories(st: list[dict], words: list[str]) -> list[dict]:
    w = {x.lower() for x in words}

    def score(s):
        text = " ".join([s["title"], s["situation"], s["task"], s["action"], s["result"], *s["tags"]]).lower()
        return sum(1 for x in w if x in text) + len(s["tags"]) * 0.1
    return sorted(st, key=score, reverse=True)


def rule_brief(c, j: dict, cfg: dict) -> str:
    co = j.get("company") or "the company"
    sents = _sentences(j.get("description") or "")
    about = [s for s in sents if _ABOUT.search(s)][:5]
    want = [s for s in sents if _WANT.search(s) and s not in about][:7]
    info = _company_info(co)
    g = _gap(j) if j.get("description") else {"matched": [], "missing": []}
    notes = c.execute("SELECT notes FROM prep_notes WHERE company=?", (co,)).fetchone()
    st = _rank_stories(stories(c), g["matched"] + g["missing"] + (j.get("title") or "").split())
    meta = [x for x in (j.get("location"), money(j.get("sal_max_lpa"), cfg) if j.get("sal_max_lpa") else "",
                        f"{j['exp_min']}+ yrs asked" if j.get("exp_min") is not None else "") if x]
    md = [f"# {j.get('title') or 'Interview'} at {co}", " · ".join(meta)]
    md.append("\n## About the company")
    if info:
        md.append(f"- {info.get('sector', '')} ({str(info.get('category', '')).replace('_', ' ')})")
    md += [f"- {s}" for s in about] or ["- The job post doesn't describe the company. Use the research links "
                                        "below and note 3 facts: what they sell, to whom, and how they make money."]
    md.append("\n## What they want")
    md += [f"- {s}" for s in want] or ["- No requirements captured. Re-read the job post and list the top 5."]
    md.append("\n## Your fit")
    md.append("- Covered by your CV: " + (", ".join(g["matched"]) or "n/a"))
    md.append("- Prepare an answer for: " + (", ".join(g["missing"]) or "nothing obvious"))
    md.append("\n## Likely questions")
    md += [f"- {q['q']}" for q in likely_questions(j)]
    md.append("\n## Stories to bring (from your story bank)")
    md += [f"- **{s['title']}**: {s['result'][:160]}" for s in st[:5]] or \
          ["- Your story bank is empty. Add 5-6 STAR stories on the Story bank tab: they answer most questions."]
    md.append("\n## Questions to ask them")
    md += [f"- {q}" for q in bank().get("ask_them", [])[:5]]
    if notes and notes[0]:
        md.append("\n## Your notes\n" + notes[0])
    return "\n".join(md)


def rule_pack(j: dict) -> str:
    """Used by Copilot's 'Prep pack' button."""
    with conn() as c:
        return rule_brief(c, j, fetch.load_cfg())


def _job(c, cfg, jid):
    return next((x for x in store.all_jobs(c, cfg) if x["id"] == jid), None)


def brief(c, cfg, b: dict) -> dict:
    j = _job(c, cfg, b["job_id"]) if b.get("job_id") else None
    if not j:
        co = (b.get("company") or "").strip()
        if not co:
            raise ValueError("Pick a job or type a company name.")
        j = {"company": co, "title": (b.get("title") or "").strip(), "description": b.get("jd") or ""}
    out = {"company": j["company"], "title": j.get("title"), "links": research_links(j["company"]),
           "role": role_of(j.get("title") or "", cfg), "ai": False}
    notes = c.execute("SELECT notes FROM prep_notes WHERE company=?", (j["company"],)).fetchone()
    out["notes"] = notes[0] if notes else ""
    if b.get("ai") and llm.enabled():
        out["markdown"] = llm.company_brief(j["company"], j if j.get("description") else None, out["notes"])
        out["ai"] = True
    else:
        out["markdown"] = rule_brief(c, j, cfg)
    out["questions"] = likely_questions(j)
    return out



# ---------------------------------------------------------------- prep for one job (coach.py / llm.py)
def _custom_job(b: dict) -> dict:
    co = (b.get("company") or "").strip()
    title = (b.get("title") or "").strip()
    if not co and not title:
        raise ValueError("Pick a job, or type a company and role.")
    return {"id": "custom:" + store._norm(co + " " + title)[:80], "company": co or "the company",
            "title": title or "this role", "description": b.get("jd") or "", "location": "", "status": "custom"}


def build_brief(c, j: dict, cfg: dict, ai: bool = False) -> dict:
    co = j.get("company") or ""
    info = _company_info(co)
    g = _gap(j) if j.get("description") else {"matched": [], "missing": []}
    role = role_of(j.get("title") or "", cfg)
    wiki = coach.wikipedia(co) if co and co != "the company" else None
    news = coach.news(co) if co and co != "the company" else []
    b = {"what_they_do": coach.what_they_do(j), "wiki": wiki, "products": coach.products(j), "news": news,
         "peers": coach.peers(co, info), "sector": (info or {}).get("sector", ""),
         "category": str((info or {}).get("category", "")).replace("_", " "),
         "rounds": coach.rounds(role, info), "fit": g, "links": research_links(co), "role": role,
         "role_label": bank()["roles"].get(role, {}).get("label", "General"), "ai": False, "takeaways": []}
    if wiki and not b["what_they_do"]:
        b["what_they_do"] = coach.sentences(wiki["summary"])[:2]
    if ai and llm.enabled():
        b.update(llm.prep_brief(j, {k: b[k] for k in ("what_they_do", "products", "news", "peers", "sector")},
                                wiki["summary"] if wiki else ""))
        b["ai"] = True
    return b


def build_questions(c, j: dict, cfg: dict, brief_d: dict, ai: bool = False) -> list[dict]:
    if ai and llm.enabled():
        return llm.prep_questions(j, brief_d, stories(c))
    role = brief_d.get("role") or role_of(j.get("title") or "", cfg)
    return coach.question_set(j, llm.cv_text(), stories(c), brief_d.get("fit") or {},
                              bank()["roles"].get(role, {}).get("questions", []), brief_d.get("role_label", "General"),
                              brief_d)


def job_prep(c, cfg, b: dict) -> dict:
    """Everything for one interview. Cached per job; pass refresh to rebuild, ai to use Claude."""
    j = _job(c, cfg, b["job_id"]) if b.get("job_id") and not str(b["job_id"]).startswith("custom:") else None
    if not j:
        j = _custom_job(b)
    ai = bool(b.get("ai")) and llm.enabled()
    row = c.execute("SELECT data, ai FROM prep_cache WHERE job_id=?", (j["id"],)).fetchone()
    if row and not b.get("refresh") and (not ai or row[1]):
        data = json.loads(row[0])
    else:
        br = build_brief(c, j, cfg, ai)
        data = {"brief": br, "questions": build_questions(c, j, cfg, br, ai), "ai": ai, "built": _now()}
        c.execute("INSERT OR REPLACE INTO prep_cache VALUES (?,?,?,?)", (j["id"], json.dumps(data), int(ai), _now()))
        c.commit()
    notes = c.execute("SELECT notes FROM prep_notes WHERE company=?", (j["company"],)).fetchone()
    hist = [dict(r) for r in c.execute("SELECT question, answer, scores, created FROM mock WHERE job_id=? "
                                       "ORDER BY created DESC LIMIT 60", (j["id"],))]
    for h in hist:
        h["scores"] = json.loads(h["scores"]) if h.get("scores") else None
    job = {k: j.get(k) for k in ("id", "title", "company", "location", "status", "url", "salary_text")}
    job["has_jd"] = bool((j.get("description") or "").strip())
    return {**data, "job": job, "notes": notes[0] if notes else "", "history": hist, "ai_available": llm.enabled()}


def answer(c, cfg, b: dict) -> dict:
    """Score one mock answer (rubric, or Claude with a key), save it, return feedback + a follow-up."""
    q = b.get("q") or {}
    if not q.get("q"):
        raise ValueError("question missing")
    j = _job(c, cfg, b.get("job_id", "")) if not str(b.get("job_id", "")).startswith("custom:") else None
    if b.get("ai") and llm.enabled():
        fb = llm.coach_answer(j or {"company": b.get("company"), "title": b.get("title")}, q, b.get("answer", ""),
                              b.get("history") or [], int(b.get("seconds") or 0))
        fb["ai"] = True
    else:
        fb = coach.score_answer(b.get("answer", ""), q, int(b.get("seconds") or 0), llm.cv_text(), stories(c))
        fb["ai"] = False
    if b.get("is_follow_up"):
        fb["follow_up"] = ""
    c.execute("INSERT INTO mock (role, question, answer, rating, seconds, feedback, created, job_id, scores) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (q.get("kind", ""), q["q"][:500], str(b.get("answer", ""))[:6000],
                                              round(fb["score"]), int(b.get("seconds") or 0), json.dumps(fb), _now(),
                                              b.get("job_id", ""), json.dumps(fb.get("dims", {}))))
    c.execute("INSERT INTO practiced VALUES (?,1,?) ON CONFLICT(question) DO UPDATE SET n=n+1, last=?",
              (q["q"][:500], _now(), _now()))
    c.commit()
    return fb


# ---------------------------------------------------------------- HTTP
def overview(c, cfg) -> dict:
    b = bank()
    allj = [j for j in store.all_jobs(c, cfg) if j["status"] not in ("hidden", "rejected")]
    rank = {"interviewing": 0, "offer": 1, "applied": 2, "saved": 3}
    allj.sort(key=lambda j: (rank.get(j["status"], 9), -(j.get("fit") or 0)))
    prepped = {r[0] for r in c.execute("SELECT job_id FROM prep_cache")}
    jobs = [{**{k: j.get(k) for k in ("id", "title", "company", "status", "location", "fit")},
             "prepped": j["id"] in prepped} for j in allj[:300]]
    return {"roles": {k: {"label": r["label"], "questions": r["questions"]} for k, r in b["roles"].items()},
            "ask_them": b.get("ask_them", []), "competencies": b.get("competencies", []),
            "default_role": role_of("", cfg), "jobs": jobs, "stories": stories(c),
            "mocks": [dict(r) for r in c.execute("SELECT * FROM mock ORDER BY created DESC LIMIT 40")],
            "practiced": {r[0]: r[1] for r in c.execute("SELECT question, n FROM practiced")},
            "ai": llm.enabled()}


def handle(h, method, body=None):
    path = urlparse(h.path).path
    q = {k: v[0] for k, v in parse_qs(urlparse(h.path).query).items()}
    cfg = fetch.load_cfg()
    try:
        with conn() as c:
            if method == "GET":
                if path in ("/prep", "/prep/"):
                    return h._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if path == "/api/prep":
                    return h._send(200, overview(c, cfg))
                if path == "/api/prep/random":
                    qs = bank()["roles"].get(q.get("role") or "general", {}).get("questions", [])
                    return h._send(200, random.choice(qs) if qs else {})
                return h._send(404, {"error": "not found"})
            body = body or {}
            if path == "/api/prep/job":
                return h._send(200, job_prep(c, cfg, body))
            if path == "/api/prep/answer":
                return h._send(200, answer(c, cfg, body))
            if path == "/api/prep/brief":
                return h._send(200, brief(c, cfg, body))
            if path == "/api/prep/notes":
                c.execute("INSERT OR REPLACE INTO prep_notes VALUES (?,?,?)",
                          (body["company"][:120], str(body.get("notes", ""))[:8000], _now()))
                c.commit()
                return h._send(200, {"ok": True})
            if path == "/api/prep/story":
                return h._send(200, save_story(c, body))
            if path == "/api/prep/practiced":
                c.execute("INSERT INTO practiced VALUES (?,1,?) ON CONFLICT(question) DO UPDATE SET n=n+1, last=?",
                          (body["question"][:500], _now(), _now()))
                c.commit()
                return h._send(200, {"ok": True})
            if path == "/api/prep/mock":
                fb = None
                if body.get("ai"):
                    if not llm.enabled():
                        raise llm.NoKey("Add a Claude API key (Copilot > Setup) for AI feedback.")
                    fb = llm.mock_feedback(body["question"], body.get("answer", ""), body.get("role", ""))
                c.execute("INSERT INTO mock (role, question, answer, rating, seconds, feedback, created) "
                          "VALUES (?,?,?,?,?,?,?)", (body.get("role", ""), body["question"][:500],
                                                     str(body.get("answer", ""))[:6000], int(body.get("rating") or 0),
                                                     int(body.get("seconds") or 0), json.dumps(fb) if fb else None,
                                                     _now()))
                c.execute("INSERT INTO practiced VALUES (?,1,?) ON CONFLICT(question) DO UPDATE SET n=n+1, last=?",
                          (body["question"][:500], _now(), _now()))
                c.commit()
                return h._send(200, {"ok": True, "feedback": fb})
            if path == "/api/prep/jobq":
                j = _job(c, cfg, body.get("job_id", ""))
                if not j:
                    raise ValueError("job not found")
                if llm.enabled():
                    return h._send(200, {"questions": [{"q": x, "type": "AI", "tip": ""} for x in llm.job_questions(j)],
                                         "ai": True})
                return h._send(200, {"questions": likely_questions(j), "ai": False})
            return h._send(404, {"error": "not found"})
    except llm.NoKey as e:
        return h._send(400, {"error": str(e), "need_key": True})
    except (ValueError, KeyError, RuntimeError) as e:
        return h._send(400, {"error": str(e)})
