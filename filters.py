"""Relevance filters: title, location, freshness, experience, salary (>= your floor), scoring."""
from __future__ import annotations

import re
from datetime import date, timedelta

from settings import money

ANNUAL = {"hourly": 2080, "daily": 260, "weekly": 52, "monthly": 12, "yearly": 1, None: 1}


def _has(text: str, words) -> str | None:
    t = f" {text.lower()} "
    for w in words:
        w = w.lower()
        pat = r"(?<![a-z])" + re.escape(w.strip()) + r"(?![a-z])"
        if re.search(pat, t):
            return w
    return None


# ---------------- salary ----------------

def to_home(amount, currency, interval, cfg) -> float | None:
    """Annual pay in your home currency units (config "currency": e.g. INR lakhs -> 25 means 25 LPA,
    USD with unit_value 1000 -> 120 means $120k)."""
    if amount in (None, "", 0):
        return None
    try:
        a = float(amount) * ANNUAL.get((interval or "yearly").lower() if interval else None, 1)
    except (TypeError, ValueError):
        return None
    home = cfg["currency"]
    cur = (currency or home["code"]).upper().replace("$", "USD")
    rate = cfg["fx_to_home"].get(cur) if cur != home["code"] else 1
    if not rate:
        return None  # unknown currency: treat as unlisted
    return round(a * rate / float(home.get("unit_value") or 1), 1)


to_lpa = to_home  # old name


_NUM = r"(\d+(?:[.,]\d+)*)\s*(k|l|lpa|lac|lacs|lakh|lakhs|cr|crore|m)?"


def parse_salary_text(text: str, cfg):
    """Free-text salary -> (min_lpa, max_lpa) or (None, None)."""
    if not text:
        return None, None
    t = text.lower().replace("₹", " inr ").replace("rs.", " inr ")
    cur = ("USD" if "$" in t or "usd" in t else "EUR" if "€" in t or re.search(r"\beur\b", t) else
           "GBP" if "£" in t or re.search(r"\bgbp\b", t) else "INR" if " inr " in f" {t} " or "lpa" in t or "lakh" in t else None)
    nums = []
    matches = list(re.finditer(_NUM, t))
    units = [m.group(2) for m in matches if m.group(2)]
    shared = units[-1] if units else ""  # "12-18 Lacs" / "$70 - 90k": unit applies to whole range
    for m in matches:
        raw, unit = m.group(1).replace(",", ""), (m.group(2) or "")
        if not unit and shared and float(raw.replace(".", "", 1) or 0) < 1000:
            unit = shared
        try:
            v = float(raw)
        except ValueError:
            continue
        if unit == "k":
            v *= 1e3
        elif unit in ("l", "lpa", "lac", "lacs", "lakh", "lakhs"):
            v *= 1e5
        elif unit in ("cr", "crore"):
            v *= 1e7
        elif unit == "m":
            v *= 1e6
        if v < 1000:  # stray small number, e.g. "2 years"
            continue
        nums.append(v)
    if not nums:
        return None, None
    interval = "hourly" if re.search(r"/\s*h(ou)?r|per hour", t) else "monthly" if re.search(r"/\s*mo|per month|monthly", t) else "yearly"
    return to_lpa(min(nums), cur, interval, cfg), to_lpa(max(nums), cur, interval, cfg)


def salary_lpa(j, cfg):
    lo = to_lpa(j.get("sal_min"), j.get("currency"), j.get("interval"), cfg)
    hi = to_lpa(j.get("sal_max"), j.get("currency"), j.get("interval"), cfg)
    if lo is None and hi is None:
        lo, hi = parse_salary_text(j.get("salary_text", ""), cfg)
    hi = hi or lo
    if hi is not None and hi < 0.08 * max(cfg["min_salary"], 1):  # parsing/stipend error
        return None, None
    return lo, hi


# ---------------- experience ----------------

_EXP_RANGE = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_PLUS = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s*(?:of\s+)?(?:\w+\s+){0,3}experience", re.I)
_EXP_MIN = re.compile(r"(?:minimum|min\.?|at least)\s*(?:of\s*)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)


def experience_range(j) -> tuple[int | None, int | None]:
    """(min, max) years asked, from the structured field first, then the description."""
    for text in (j.get("experience_text") or "", (j.get("description") or "")[:3000]):
        if not text:
            continue
        m = _EXP_RANGE.search(text)
        if m and int(m.group(1)) <= 20:
            return int(m.group(1)), int(m.group(2))
        for rx in (_EXP_MIN, _EXP_PLUS):
            m = rx.search(text)
            if m and int(m.group(1)) <= 20:
                return int(m.group(1)), None
    return None, None


def min_experience(j) -> int | None:
    return experience_range(j)[0]


# ---------------- location ----------------

def location_bucket(j, cfg) -> str | None:
    """Which of your locations a job belongs to (its label), "Remote", or None to drop it.
    config "locations": [{"label": "Berlin", "match": ["berlin"], "search": "Berlin, Germany"}, ...]
    With no locations set, every on-site job is kept as "Any location"."""
    loc = (j.get("location") or "").lower()
    for L in cfg["locations"]:
        if _has(loc, L.get("match") or [L["label"]]):
            return L["label"]
    region = _region_only(loc, cfg)
    if region:  # e.g. Indeed's "KA, India": no city, only the state
        for L in cfg["locations"]:
            words = list(L.get("match") or [L["label"]]) + [L["label"]]
            if any(region in REGIONS.get(w.lower().strip(), ()) for w in words):
                return L["label"]
    is_remote = j.get("remote") or "remote" in loc or "anywhere" in loc
    R = cfg["remote"]
    if is_remote and R.get("enabled"):
        ok = [r for r in list(R.get("ok_regions") or []) + ([cfg["country"].lower()] if cfg.get("country") else [])
              if r.lower().strip() != "remote"]  # "remote" itself says nothing about where
        block = list(R.get("block_regions") or [])
        if cfg.get("country"):  # remote jobs tied to another country ("Remote - USA") don't fit you
            block += [b for b in ELSEWHERE if not _has(b, [cfg["country"]])]
        if _has(loc, block) and not _has(loc, ok):
            return None
        if _remote_only(loc) or _has(loc, ok) or not block:
            return "Remote"
        return None
    if not cfg["locations"] and not is_remote:
        return "Any location"
    if cfg.get("keep_unspecified_location", True) and _unspecified(loc, cfg):
        return UNSPECIFIED
    return None


# Places a remote job can be limited to. With your country set, a remote job naming one of these
# (and none of your ok_regions) is skipped: e.g. "Remote - USA", "US-Remote", "Remote, Germany".
ELSEWHERE = ["us", "usa", "u.s.", "u.s.a.", "united states", "america", "americas", "canada", "mexico", "brazil",
             "latam", "uk", "united kingdom", "england", "ireland", "europe", "eu", "emea", "germany", "france",
             "spain", "portugal", "netherlands", "poland", "italy", "sweden", "switzerland", "israel",
             "australia", "new zealand", "japan", "philippines", "india", "pst", "est", "cet", "us timezones"]


def _remote_only(loc: str) -> bool:
    """'Remote', 'Fully remote', '100% Remote (WFH)': remote with no place named."""
    rest = re.sub(r"remote|fully|full[- ]time|100%|work from home|wfh|anywhere|\(|\)|-|,|/|\|", " ", loc)
    return not rest.strip()


# State/region names and codes for big job cities, so "KA, India" or "Haryana, India" (a site that gave
# only the state) still lands in your city. Only used when the location names no city at all.
REGIONS = {
    "bangalore": ("karnataka", "ka"), "bengaluru": ("karnataka", "ka"),
    "delhi": ("delhi", "dl", "nct of delhi"), "new delhi": ("delhi", "dl", "nct of delhi"),
    "ncr": ("delhi", "dl", "haryana", "hr", "uttar pradesh", "up"),
    "gurgaon": ("haryana", "hr"), "gurugram": ("haryana", "hr"),
    "noida": ("uttar pradesh", "up"),
    "mumbai": ("maharashtra", "mh"), "pune": ("maharashtra", "mh"),
    "hyderabad": ("telangana", "ts", "tg"), "chennai": ("tamil nadu", "tn"),
    "kolkata": ("west bengal", "wb"), "ahmedabad": ("gujarat", "gj"),
}


def _region_only(loc: str, cfg) -> str | None:
    """'KA, India' -> 'ka'; 'Haryana, India' -> 'haryana'; anything naming a city -> None."""
    parts = [p.strip() for p in re.split(r"[,|/]", loc) if p.strip()]
    country = (cfg.get("country") or "").lower().strip()
    if country and parts and parts[-1] == country:
        parts = parts[:-1]
    if len(parts) != 1:
        return None
    known = {r for rs in REGIONS.values() for r in rs}
    return parts[0] if parts[0] in known else None


UNSPECIFIED = "City not stated"
_VAGUE = {"", "multiple locations", "various locations", "various", "multiple cities", "pan india", "pan-india",
          "hybrid", "on-site", "onsite", "flexible"}


def _unspecified(loc: str, cfg) -> bool:
    """A job that names your country but no city, e.g. LinkedIn's "India" or "Multiple locations, India".
    Often open to your cities, so it is kept (as "City not stated") and ranked lower."""
    country = (cfg.get("country") or "").lower().strip()
    if not country:
        return False
    pat = rf"(?<![a-z]){re.escape(country)}(?![a-z])"
    if not re.search(pat, loc):
        return False
    rest = re.sub(pat, " ", loc)
    rest = re.sub(r"[()\[\],;|/-]+", " ", rest)
    return " ".join(rest.split()) in _VAGUE


def titles(cfg):
    return cfg.get("derived_titles") or cfg["title_include"]


def drop_reason(j, cfg, today: date | None = None) -> str | None:
    """Why evaluate() would drop this job (None if it is kept). Used by diagnose.py and fetch stats."""
    today = today or date.today()
    title = j.get("title") or ""
    if not _has(title, titles(cfg)):
        return "title not in role list"
    ex = _has(title, cfg["title_exclude"])
    if ex:
        return f"title excluded ({ex.strip()})"
    if not location_bucket(j, cfg):
        return "location not wanted"
    if _age(j, today) > max_age(j, cfg):
        return "older than max_age_days"
    return None


# Company career portals list only open roles, so an older posting there is usually still hiring;
# job boards keep stale posts. Portal jobs get a longer window ("portal_max_age_days") and rank lower.
PORTALS = {"greenhouse", "lever", "ashby", "smartrecruiters", "workable", "workday"}


def max_age(j, cfg) -> int:
    if j.get("source") in PORTALS:
        return max(cfg["max_age_days"], int(cfg.get("portal_max_age_days", 45)))
    return cfg["max_age_days"]


def _age(j, today) -> int:
    try:
        return (today - date.fromisoformat(j["posted"])).days if j.get("posted") else 0
    except (ValueError, TypeError):
        return 0


# ---------------- main evaluation ----------------

def evaluate(j, cfg, today: date | None = None) -> dict | None:
    """Return enrichment dict, or None to drop the job entirely.
    Soft fails (salary below floor, too senior) are kept but marked `filtered`."""
    today = today or date.today()
    title = j.get("title") or ""
    if not _has(title, titles(cfg)) or _has(title, cfg["title_exclude"]):
        return None
    bucket = location_bucket(j, cfg)
    if not bucket:
        return None
    if _age(j, today) > max_age(j, cfg):
        return None

    lo, hi = salary_lpa(j, cfg)
    exp, exp_max = experience_range(j)
    floor = cfg["min_salary"]
    reasons = []
    if floor and hi is not None and hi < floor:
        reasons.append(f"Salary below {money(floor, cfg)}")
    if exp is not None and exp > cfg["max_experience_years"]:
        reasons.append(f"Needs {exp}+ yrs")

    text = f"{title} {j.get('company', '')} {j.get('description', '')[:2000]}".lower()
    score = 10
    tl = title.lower()
    if _has(tl, cfg["role_tiers"].get("core") or []):
        score += 3
    if "senior" in tl or "sr." in tl:
        score -= 2 if (exp is None or exp > 3) else 0
    for kw, w in cfg["boost_keywords"].items():
        if _has(text, [kw]):
            score += w
    if floor and hi is not None and hi >= floor:
        score += 3
    if bucket == UNSPECIFIED:
        score -= 3
    if _age(j, today) > cfg["max_age_days"]:  # older career-portal posting
        score -= 2
    if reasons:  # below salary/experience bar: sink to the bottom
        score -= 10
    return dict(location_bucket=bucket, sal_min_lpa=lo, sal_max_lpa=hi,
                salary_listed=hi is not None, exp_min=exp, exp_max=exp_max, score=score,
                filtered="; ".join(reasons))
