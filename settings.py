"""Your personal settings: search config, application profile, CV and resume.

Personal files live next to the app and are git-ignored:
  config.json   what to search for (roles, locations, pay, sources)  <- copied from config.example.json
  profile.json  answers for application forms                         <- copied from profile.example.json
  cv.md         your CV as text (used for fit scoring, tailoring, prep) <- copied from cv.example.md
  resume.pdf    the CV file attached to applications
  secrets.json  optional API key / Telegram token                     <- copied from secrets.example.json
Everything can be edited on the Setup page (http://127.0.0.1:8765/setup).
"""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

HERE = Path(__file__).parent
CONFIG = HERE / "config.json"
PROFILE = HERE / "profile.json"
CV = HERE / "cv.md"
RESUME = HERE / "resume.pdf"
EXAMPLES = {CONFIG: "config.example.json", PROFILE: "profile.example.json", CV: "cv.example.md"}

# Keys every config needs; older or hand-edited configs are filled from these.
DEFAULTS = {
    "setup_complete": False,
    "currency": {"code": "INR", "symbol": "₹", "unit_label": "LPA", "unit_value": 100000},
    "fx_to_home": {"INR": 1, "USD": 84, "EUR": 91, "GBP": 107},
    "min_salary": 0,
    "max_age_days": 14,
    "max_experience_years": 50,
    "search_terms": [],
    "locations": [],
    "country": "",
    "remote": {"enabled": True, "ok_regions": ["anywhere", "worldwide", "global", "remote", "work from anywhere"],
               "block_regions": []},
    "title_include": [],
    "title_exclude": ["intern", "internship"],
    "role_tiers": {"core": [], "strong": [], "related": [], "stretch": []},
    "profile": {"years_experience": 0, "skills": [], "domains": []},
    "boost_keywords": {},
    "sources": {},
    "company_boards": {},
    "results_per_query": 25,
    "recent_hours": 72,
    "copilot": {},
}


def ensure_files():
    for target, example in EXAMPLES.items():
        if not target.exists() and (HERE / example).exists():
            shutil.copy(HERE / example, target)


def load_cfg() -> dict:
    ensure_files()
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    if "min_salary_lpa" in cfg and not cfg.get("min_salary"):  # configs from before v1.0
        cfg["min_salary"] = cfg["min_salary_lpa"]
    if "usd_to_inr" in cfg and "fx_to_home" not in json.loads(CONFIG.read_text(encoding="utf-8")):
        r = cfg["usd_to_inr"]
        cfg["fx_to_home"] = {"INR": 1, "USD": r, "EUR": r * 1.08, "GBP": r * 1.27}
    if not cfg["title_include"]:  # derive from role tiers
        cfg["title_include"] = sorted({t for ts in cfg["role_tiers"].values() for t in ts})
    cfg["min_salary_lpa"] = cfg["min_salary"]  # legacy name used across the code: "salary floor in home units"
    return cfg


def save_cfg(cfg: dict):
    cfg = {k: v for k, v in cfg.items() if k != "min_salary_lpa"}
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_profile() -> dict:
    ensure_files()
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"fields": {}, "answers": {}}


def save_profile(p: dict):
    PROFILE.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")


def me() -> dict:
    """The few profile facts that templates use (name, contact, school, employer, pitch)."""
    f = load_profile().get("fields", {})
    name = f.get("full_name") or " ".join(x for x in (f.get("first_name"), f.get("last_name")) if x) or "Your Name"
    return {"name": name, "email": f.get("email", ""), "phone": f.get("phone", ""),
            "location": f.get("location", ""), "linkedin": f.get("linkedin", ""),
            "school": f.get("school", ""), "company": f.get("current_company", ""),
            "title": f.get("current_title", ""), "headline": f.get("headline", ""),
            "win": f.get("top_achievement", ""), "notice": f.get("notice_period", "")}


def file_stub(kind: str, company: str = "") -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{me()['name']}_{kind}_{company}").strip("_")


# ---------------------------------------------------------------- money in the user's currency
def money(v, cfg) -> str:
    """Annual pay stored in home units (e.g. 25 -> '₹25 LPA', 120 -> '$120k')."""
    if v is None:
        return ""
    c = cfg["currency"]
    label = c.get("unit_label", "")
    return f"{c.get('symbol', '')}{v:g}{'' if label in ('k', 'K') else ' '}{label}".strip()


# ---------------------------------------------------------------- Setup page API
def state() -> dict:
    cfg = load_cfg()
    cfg.pop("min_salary_lpa", None)
    return {"config": cfg, "profile": load_profile(),
            "cv": CV.read_text(encoding="utf-8") if CV.exists() else "",
            "resume": {"exists": RESUME.exists(), "name": load_profile().get("resume_name") or "resume.pdf"}}


def save(body: dict) -> dict:
    if isinstance(body.get("config"), dict):
        cur = load_cfg()
        cur.update(body["config"])
        cur["setup_complete"] = True
        save_cfg(cur)
    if isinstance(body.get("profile_fields"), dict):
        p = load_profile()
        p.setdefault("fields", {}).update({k: str(v)[:2000] for k, v in body["profile_fields"].items()})
        save_profile(p)
    if isinstance(body.get("cv"), str):
        CV.write_text(body["cv"][:60000], encoding="utf-8")
    if body.get("resume_b64"):
        data = base64.b64decode(body["resume_b64"].split(",")[-1])
        if not data.startswith(b"%PDF"):
            raise ValueError("Resume must be a PDF file.")
        if len(data) > 8_000_000:
            raise ValueError("Resume PDF is over 8 MB.")
        RESUME.write_bytes(data)
        p = load_profile()
        p["resume_file"] = RESUME.name
        p["resume_name"] = (body.get("resume_name") or "resume.pdf")[:120]
        save_profile(p)
    try:  # refresh the extension's offline copy
        import autofill
        autofill.write_snapshot()
    except Exception:
        pass
    return {"ok": True, **state()}
