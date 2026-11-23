"""Batch apply, Chrome-extension edition.

Your own Chrome does the applying: the "Jobber Autofill" extension (chrome-extension/ folder,
loaded unpacked) fills each form from profile.json + your CV. You review and press Submit yourself;
the extension sees the confirmation page and reports it here, so the job is logged as applied and
any new question you answered is learned. No browser automation, so nothing for sites to flag.

The Batch apply page queues jobs and opens them in Chrome tabs a few at a time, with per-site
pacing so you don't fire off too many applications to one portal in a short window.

Routes (hooked from app.py): GET /apply, GET /api/apply/state|candidates|profile,
POST /api/apply/profile|start|opened|mark|clear|stop, GET /api/apply/ext/profile,
POST /api/apply/ext/applied|closed
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import store

HERE = Path(__file__).parent
PROFILE = HERE / "profile.json"
PAGE = HERE / "apply.html"

FAST = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable"}
EASE = {**{a: "fast" for a in FAST}, "workday": "slow", "company site": "maybe",
        "linkedin": "redirect", "indeed": "redirect", "naukri": "login"}
# Per-portal pacing: at most N applications per M minutes. Lever and SmartRecruiters
# have temporarily blocked bursts of applications, so they get the tightest limits.
DEFAULT_PACING = {"lever": [3, 30], "smartrecruiters": [3, 30], "default": [8, 30]}
LOCK = threading.RLock()
STATE = {"items": [], "batch_size": 5, "sent": []}   # sent: [(ats, ts)]


# ---------------- profile ----------------
def load_profile() -> dict:
    import settings
    return settings.load_profile()


def save_profile(p: dict):
    PROFILE.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    write_snapshot()


ASK = ["email", "phone", "linkedin", "location", "notice_period", "current_ctc", "expected_ctc"]


def missing_fields(p) -> list[str]:
    return [k for k in ASK if not str(p["fields"].get(k, "")).strip()]


def pacing(p=None) -> dict:
    return {**DEFAULT_PACING, **((p or load_profile()).get("pacing") or {})}


def ext_profile() -> dict:
    p = load_profile()
    resume = HERE / p.get("resume_file", "resume.pdf")
    try:
        cover = json.loads((HERE / "cover_templates.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cover = {"default_hook": "", "templates": {}}
    # your own answers win; AI screening answers (copilot.py) fill the gaps
    return {"fields": p["fields"], "answers": {**(p.get("ai_answers") or {}), **p.get("answers", {})},
            "resume": {"name": p.get("resume_name") or resume.name,
                       "b64": base64.b64encode(resume.read_bytes()).decode() if resume.exists() else ""},
            "cover": {"default_hook": cover.get("default_hook", ""),
                      "templates": {k: {kk: t.get(kk) for kk in ("label", "match", "body")}
                                    for k, t in (cover.get("templates") or {}).items()}},
            "updated": datetime.now().isoformat(timespec="seconds")}


SNAPSHOT = HERE / "chrome-extension" / "snapshot.json"


def write_snapshot():
    """Copy of your profile inside the extension folder, so it fills forms even when the app is closed."""
    try:
        if SNAPSHOT.parent.exists():
            SNAPSHOT.write_text(json.dumps(ext_profile(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------- jobs ----------------
def form_url(url: str, ats: str) -> str:
    """Jump straight to the application form where the ATS has a separate page."""
    u = url.split("#")[0].rstrip("/")
    if ats == "lever" and re.search(r"lever\.co/[^/]+/[0-9a-f-]{20,}$", u):
        return u + "/apply"
    if ats == "ashby" and re.search(r"ashbyhq\.com/[^/]+/[0-9a-f-]{20,}$", u):
        return u + "/application"
    return url


def ids_in(url: str) -> set[str]:
    """Job ids inside a URL (UUIDs, long numbers, Workday R-numbers): used to match a
    confirmation page back to the job."""
    u = (url or "").lower()
    return set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{6,}|r-?\d{4,}", u))


def candidates():
    import fetch
    with store.conn() as c:
        jobs = store.all_jobs(c, fetch.load_cfg())
    out = []
    for j in jobs:
        if j["status"] not in ("new", "saved") or j.get("filtered"):
            continue
        ats = j.get("ats") or "company site"
        out.append({k: j.get(k) for k in ("id", "title", "company", "location", "fit", "age_days", "status",
                                          "salary_text", "apply_url")} | {"ats": ats, "ease": EASE.get(ats, "maybe")})
    return out


def recent(ats: str, minutes: int) -> int:
    cut = time.time() - minutes * 60
    return sum(1 for a, t in STATE["sent"] if a == ats and t >= cut)


def over_pace(ats: str, pace: dict) -> str:
    n, mins = pace.get(ats) or pace["default"]
    k = recent(ats, mins)
    return f"{k} {ats.title()} applications in {mins} min. Pausing {ats.title()} to avoid a block." if k >= n else ""


def mark_applied(jid: str | None, ats: str, learned: dict, how: str):
    if jid:
        with store.conn() as c:
            row = c.execute("SELECT notes, status FROM jobs WHERE id=?", (jid,)).fetchone()
            if row and row["status"] != "applied":
                note = f"Applied ({how}) {datetime.now():%Y-%m-%d %H:%M}"
                store.update(c, jid, "applied", ((row["notes"] + "\n") if row["notes"] else "") + note)
    STATE["sent"].append((ats or "other", time.time()))
    if learned:
        p = load_profile()
        p.setdefault("answers", {}).update({k: str(v) for k, v in learned.items() if k and v and len(k) < 300})
        save_profile(p)


def find_job(urls: list[str]):
    """Match a confirmation/form URL to a queued item, else to any job in the DB."""
    want = set().union(*(ids_in(u) for u in urls if u))
    if not want:
        return None, None
    with LOCK:
        for it in STATE["items"]:
            if it["state"] in ("open", "waiting") and ids_in(it["url"]) & want:
                return it["id"], it
    with store.conn() as c:
        for r in c.execute("SELECT id, apply_url, url, sources FROM jobs WHERE status != 'applied'"):
            blob = " ".join(filter(None, [r["apply_url"], r["url"], r["sources"]]))
            if ids_in(blob) & want:
                return r["id"], None
    return None, None


# ---------------- HTTP ----------------
def handle(h, method, body=None):
    path = h.path.split("?")[0]
    body = body or {}
    if method == "GET":
        if path in ("/apply", "/apply/"):
            return h._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/apply/state":
            p = load_profile()
            pace = pacing(p)
            with LOCK:
                paused = {a: over_pace(a, pace) for a in {it["ats"] for it in STATE["items"]}}
                return h._send(200, {"items": STATE["items"], "batch_size": STATE["batch_size"],
                                     "paused": {a: m for a, m in paused.items() if m},
                                     "missing": missing_fields(p)})
        if path == "/api/apply/candidates":
            return h._send(200, {"jobs": candidates()})
        if path == "/api/apply/profile":
            p = load_profile()
            return h._send(200, {"fields": p["fields"], "answers": p.get("answers", {}),
                                 "batch_size": p.get("batch_size", 5), "missing": missing_fields(p)})
        if path == "/api/apply/ext/profile":
            return h._send(200, ext_profile())
        return h._send(404, {"error": "not found"})

    if path == "/api/apply/profile":
        p = load_profile()
        if isinstance(body.get("fields"), dict):
            p["fields"].update({k: str(v) for k, v in body["fields"].items()})
        if isinstance(body.get("answers"), dict):
            p["answers"] = {k: str(v) for k, v in body["answers"].items() if k}
        if body.get("batch_size"):
            p["batch_size"] = max(1, min(15, int(body["batch_size"])))
        save_profile(p)
        return h._send(200, {"ok": True, "missing": missing_fields(p)})
    if path == "/api/apply/start":
        ids = body.get("ids") or []
        jobs = {j["id"]: j for j in candidates()}
        with LOCK:
            STATE["batch_size"] = int(body.get("batch_size") or load_profile().get("batch_size", 5))
            have = {it["id"] for it in STATE["items"] if it["state"] in ("waiting", "open")}
            for jid in ids:
                j = jobs.get(jid)
                if j and jid not in have and j.get("apply_url"):
                    STATE["items"].append({"id": jid, "title": j["title"], "company": j["company"], "ats": j["ats"],
                                           "url": form_url(j["apply_url"], j["ats"]), "state": "waiting", "info": ""})
        return h._send(200, {"ok": True})
    if path == "/api/apply/next":
        # The page asks which jobs to open now: fill free slots, skipping portals over their pace.
        pace = pacing()
        out = []
        with LOCK:
            free = STATE["batch_size"] - sum(1 for it in STATE["items"] if it["state"] == "open")
            for it in STATE["items"]:
                if free <= 0:
                    break
                n, mins = pace.get(it["ats"]) or pace["default"]
                busy = recent(it["ats"], mins) + sum(1 for x in STATE["items"] if x["state"] == "open" and x["ats"] == it["ats"])
                if it["state"] == "waiting" and busy < n:
                    it.update(state="open", info="")
                    out.append({"id": it["id"], "url": it["url"]})
                    free -= 1
        return h._send(200, {"open": out})
    if path == "/api/apply/mark":
        jid, action = body.get("id"), body.get("action")
        with LOCK:
            it = next((x for x in STATE["items"] if x["id"] == jid), None)
        if not it:
            return h._send(404, {"error": "not in queue"})
        if action == "applied":
            it.update(state="applied", info="marked by you")
            mark_applied(jid, it["ats"], {}, "batch")
        else:
            it.update(state="skipped", info="skipped")
        return h._send(200, {"ok": True})
    if path == "/api/apply/clear":
        with LOCK:
            STATE["items"] = [it for it in STATE["items"] if it["state"] in ("waiting", "open")]
        return h._send(200, {"ok": True})
    if path == "/api/apply/stop":
        with LOCK:
            for it in STATE["items"]:
                if it["state"] == "waiting":
                    it["state"] = "skipped"
        return h._send(200, {"ok": True})
    if path == "/api/apply/ext/applied":
        jid, it = (body.get("id"), None)
        if jid:
            with LOCK:
                it = next((x for x in STATE["items"] if x["id"] == jid), None)
        else:
            jid, it = find_job([body.get("url"), body.get("form_url")])
        ats = it["ats"] if it else (body.get("host") or "other").split(".")[-2] if body.get("host") else "other"
        if it:
            it.update(state="applied", info="confirmation seen")
        learned = body.get("learned") if isinstance(body.get("learned"), dict) else {}
        mark_applied(jid, ats, learned, "Chrome extension")
        return h._send(200, {"ok": True, "matched": bool(jid), "warn": over_pace(ats, pacing())})
    if path == "/api/apply/ext/closed":
        with LOCK:
            it = next((x for x in STATE["items"] if x["id"] == body.get("id")), None)
            if it and it["state"] == "open":
                it.update(state="skipped", info="tab closed")
        return h._send(200, {"ok": True})
    return h._send(404, {"error": "not found"})


write_snapshot()  # refresh the extension's offline copy whenever the app starts
