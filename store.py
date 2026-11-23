"""SQLite storage with cross-source de-duplication and application tracking."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

DB = Path(__file__).with_name("jobs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,           -- dedupe key: company + title + location bucket
  title TEXT, company TEXT, location TEXT, location_bucket TEXT, remote INTEGER,
  url TEXT, sources TEXT,        -- JSON {source: url}
  posted TEXT, first_seen TEXT, last_seen TEXT,
  salary_text TEXT, sal_min_lpa REAL, sal_max_lpa REAL, salary_listed INTEGER,
  exp_min INTEGER, score INTEGER, filtered TEXT, description TEXT,
  status TEXT DEFAULT 'new', notes TEXT DEFAULT '', status_changed TEXT
);
"""
MIGRATIONS = {"exp_max": "INTEGER", "apply_url": "TEXT", "exp_text": "TEXT", "ats": "TEXT"}

STATUSES = ["new", "saved", "applied", "interviewing", "offer", "rejected", "hidden"]


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
    for col, typ in MIGRATIONS.items():
        if col not in have:
            c.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    return c


def _norm(s: str) -> str:
    s = re.sub(r"\(.*?\)|[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(r"\b(pvt|private|ltd|limited|inc|technologies|technology|india|llp)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def key(j) -> str:
    raw = f"{_norm(j['company'])}|{_norm(j['title'])}|{j['location_bucket']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def upsert(c, j) -> bool:
    """Insert or merge. Returns True if new."""
    now = datetime.now().isoformat(timespec="seconds")
    k = key(j)
    row = c.execute("SELECT * FROM jobs WHERE id=?", (k,)).fetchone()
    if row:
        srcs = json.loads(row["sources"])
        srcs.setdefault(j["source"], j["url"])
        upd = dict(sources=json.dumps(srcs), last_seen=now)
        if not row["url"]:
            upd["url"] = j["url"]
        import ats as _ats
        cur = _ats.ats_of(row["apply_url"])
        if j.get("apply_url") and (not row["apply_url"] or (cur in ("linkedin", "naukri", "indeed", "")
                                                            and j.get("ats") not in ("linkedin", "naukri", "indeed", ""))):
            upd.update(apply_url=j["apply_url"], ats=j.get("ats") or "")
        if j.get("posted") and (not row["posted"] or j["posted"] > row["posted"]):
            upd["posted"] = j["posted"]
        if len(j.get("description") or "") > len(row["description"] or ""):
            upd["description"] = j["description"][:6000]
        if row["exp_min"] is None and j.get("exp_min") is not None:
            upd.update(exp_min=j["exp_min"], exp_max=j.get("exp_max"))
        if j["salary_listed"] and not row["salary_listed"]:  # better salary info from another source
            upd.update(salary_text=j["salary_text"], sal_min_lpa=j["sal_min_lpa"], sal_max_lpa=j["sal_max_lpa"],
                       salary_listed=1, filtered=j["filtered"], score=j["score"])
        c.execute(f"UPDATE jobs SET {', '.join(f'{k2}=?' for k2 in upd)} WHERE id=?", (*upd.values(), k))
        return False
    c.execute(
        """INSERT INTO jobs (id,title,company,location,location_bucket,remote,url,sources,posted,first_seen,last_seen,
           salary_text,sal_min_lpa,sal_max_lpa,salary_listed,exp_min,score,filtered,description,
           exp_max,apply_url,exp_text,ats)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (k, j["title"], j["company"], j["location"], j["location_bucket"], int(bool(j["remote"])), j["url"],
         json.dumps({j["source"]: j["url"]}), j.get("posted"), now, now, j.get("salary_text", ""),
         j["sal_min_lpa"], j["sal_max_lpa"], int(j["salary_listed"]), j["exp_min"], j["score"],
         j["filtered"], (j.get("description") or "")[:6000],
         j.get("exp_max"), j.get("apply_url") or j["url"], j.get("experience_text") or "", j.get("ats") or ""))
    return True


def all_jobs(c, cfg):
    import filters
    import fit
    rows = c.execute("SELECT * FROM jobs").fetchall()
    today = date.today()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d["sources"])
        if d["sal_max_lpa"] is not None and d["sal_max_lpa"] < 0.08 * max(cfg["min_salary"], 1):  # bogus tiny salary
            d.update(sal_min_lpa=None, sal_max_lpa=None, salary_listed=0,
                     filtered="; ".join(x for x in (d["filtered"] or "").split("; ") if x and "Salary" not in x))
        if d["exp_min"] is None:  # older rows: re-read experience from text
            d["exp_min"], d["exp_max"] = filters.experience_range(
                {"experience_text": d.get("exp_text"), "description": d.get("description")})
        d["fit"], d["fit_pros"], d["fit_gaps"] = fit.score(d, cfg)
        d["apply_url"] = d.get("apply_url") or d["url"]
        if not d.get("ats"):
            import ats as _ats
            d["ats"] = _ats.ats_of(d["apply_url"])
        try:
            d["age_days"] = (today - date.fromisoformat(d["posted"])).days if d["posted"] else None
        except ValueError:
            d["age_days"] = None
        out.append(d)
    # Newest first, then best fit
    out.sort(key=lambda d: (d["age_days"] if d["age_days"] is not None else 99, -d["fit"]))
    return out


def update(c, jid, status=None, notes=None):
    if status is not None:
        if status not in STATUSES:
            raise ValueError("bad status")
        c.execute("UPDATE jobs SET status=?, status_changed=? WHERE id=?",
                  (status, datetime.now().isoformat(timespec="seconds"), jid))
    if notes is not None:
        c.execute("UPDATE jobs SET notes=? WHERE id=?", (notes[:2000], jid))
    c.commit()


def repair(c):
    """Earlier versions could save LinkedIn jobs with a missing link (NaN). Drop those
    untouched rows so the next fetch re-adds them properly."""
    n = c.execute("""DELETE FROM jobs WHERE (url IS NULL OR url='' OR sources LIKE '%NaN%')
                     AND status='new' AND COALESCE(notes,'')=''""").rowcount
    c.commit()
    return n
