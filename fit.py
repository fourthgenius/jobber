"""CV fit score (0-100) with plain-English reasons. Profile comes from config.json -> "profile"."""
from __future__ import annotations

import re

import filters
from settings import money

TIERS = [("core", 35, "Core role for you"), ("strong", 32, "Strong role match"),
         ("related", 24, "Related role"), ("stretch", 16, "Stretch role")]


def role_tiers(cfg) -> list[tuple[int, str, list[str]]]:
    """(points, label, title keywords) from config "role_tiers"."""
    rt = cfg.get("role_tiers") or {}
    out = [(pts, label, rt.get(k) or []) for k, pts, label in TIERS]
    if not any(kws for _, _, kws in out):  # no tiers set: every included title is a strong match
        out[1] = (32, "Strong role match", cfg.get("title_include") or [])
    return out


def score(j: dict, cfg: dict) -> tuple[int, list[str], list[str]]:
    """Returns (fit 0-100, reasons it fits, gaps)."""
    prof = cfg.get("profile", {})
    yrs = float(prof.get("years_experience") or 0)
    title = (j.get("title") or "").lower()
    text = f"{title} {j.get('company') or ''} {j.get('description') or ''}".lower()
    pros, gaps = [], []

    # 1) Role (35)
    role = 10
    for pts, label, kws in role_tiers(cfg):
        if filters._has(title, kws):
            role = pts
            pros.append(label)
            break
    if re.search(r"\b(senior|sr\.?|lead)\b", title):
        role -= 8
        gaps.append("Senior title")

    # 2) Experience (25)
    lo, hi = j.get("exp_min"), j.get("exp_max")
    if lo is None:
        exp = 16
    elif lo <= yrs + 0.5:
        exp = 25
        pros.append(f"Experience fits ({lo}{'-' + str(hi) if hi else '+'} yrs)")
    elif lo <= yrs + 1.5:
        exp = 18
        gaps.append(f"Asks {lo}+ yrs, you have ~{yrs:g} (stretch)")
    elif lo <= yrs + 3:
        exp = 10
        gaps.append(f"Asks {lo}+ yrs, you have ~{yrs:g}")
    else:
        exp = 0
        gaps.append(f"Asks {lo}+ yrs: likely too senior")

    # 3) Skills + domain from your CV (30)
    hits = [k for k in prof.get("skills", []) + prof.get("domains", []) if filters._has(text, [k])]
    skills = min(30, 4 * len(hits))
    if hits:
        pros.append("Matches your CV: " + ", ".join(dict.fromkeys(hits[:6])))
    elif not (j.get("description") or "").strip():
        skills = 12  # no description to judge; neutral
    # 4) Pay (10)
    floor = cfg["min_salary"]
    hi_pay = j.get("sal_max_lpa")
    if hi_pay is None:
        pay = 6
    elif hi_pay >= floor:
        pay = 10
        pros.append(f"Pays up to {money(hi_pay, cfg)}")
    else:
        pay = 0
        gaps.append(f"Pay tops out at {money(hi_pay, cfg)}")

    fit = max(0, min(100, role + exp + skills + pay))
    return fit, pros, gaps
