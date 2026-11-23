"""Search health check: which sources return jobs, and which filters drop them.

Runs every enabled source once (nothing is saved to your job list) and prints, per source:
fetched, kept, and why the rest were dropped, with sample titles/locations.

    .venv\\Scripts\\python diagnose.py            (all enabled sources)
    .venv\\Scripts\\python diagnose.py naukri     (only the named sources)
Writes the full report to diagnose_report.json.
"""
from __future__ import annotations

import collections
import json
import sys
import time
from datetime import date, timedelta

import filters
import settings
import sources
import store


def why(j, cfg) -> str:
    t = j.get("title") or ""
    if not isinstance(t, str) or not t or not j.get("url"):
        return "no title/url"
    if hasattr(filters, "drop_reason"):  # newer filters.py explains itself
        r = filters.drop_reason(j, cfg)
        if r:
            return r
    else:
        if not filters._has(t, cfg["title_include"]):
            return "title not in role list"
        ex = filters._has(t, cfg["title_exclude"])
        if ex:
            return f"title excluded ({ex.strip()})"
        if not filters.location_bucket(j, cfg):
            return "location not wanted"
        if j.get("posted"):
            try:
                if date.fromisoformat(j["posted"]) < date.today() - timedelta(days=cfg["max_age_days"]):
                    return "older than max_age_days"
            except ValueError:
                pass
    ev = filters.evaluate(j, cfg)
    if ev is None:
        return "dropped (other)"
    return "kept, below bar: " + ev["filtered"].split(";")[0] if ev.get("filtered") else "kept"


def main(only: list[str]):
    cfg = settings.load_cfg()
    enabled = {k for k, v in cfg["sources"].items() if v}
    if only:
        enabled &= set(only)
    print("Search terms:", cfg["search_terms"])
    print("Queries sent to job sites:", sources.queries(cfg))
    print("Locations:", [l["label"] for l in cfg["locations"]], "| remote:", cfg["remote"].get("enabled"))
    print("Floor:", cfg["min_salary"], "| max exp:", cfg["max_experience_years"], "| max age days:", cfg["max_age_days"])
    runs: dict[str, list] = {}

    def take(name, fn):
        t = time.time()
        try:
            runs[name] = fn() or []
            print(f"{name}: {len(runs[name])} fetched in {time.time() - t:.0f}s", flush=True)
        except Exception as e:
            runs[name] = []
            print(f"{name}: FAILED {str(e)[:200]}", flush=True)

    for name, fn in sources.OFFICIAL.items():
        if name in enabled:
            take(name, lambda fn=fn: fn(cfg))
    for name, fn in getattr(sources, "EXTRA", {}).items():
        if name in enabled:
            take(name, lambda fn=fn: fn(cfg))
    js = [s for s in sources.JOBSPY if s in enabled]
    if "linkedin" in js and cfg.get("linkedin_mode", "browser") == "browser":
        try:
            import linkedin_browser
            take("linkedin", lambda: linkedin_browser.fetch(cfg, set(), print))
            js.remove("linkedin")
        except ImportError:
            print("linkedin: Playwright not installed -> jobspy")
    if "linkedin" in js and hasattr(sources, "linkedin_guest") and cfg.get("linkedin_mode") == "guest":
        take("linkedin", lambda: sources.linkedin_guest(cfg, print))
        js.remove("linkedin")
    for s in js:
        take(s, lambda s=s: sources.jobspy_sites(cfg, [s], print))
    if "company_boards" in enabled:
        import ats
        with store.conn() as c:
            take("career_portals", lambda: ats.run(cfg, c, print))

    report, seen = {}, set()
    total_kept = 0
    for name, jobs in runs.items():
        reasons = collections.Counter()
        samples = collections.defaultdict(list)
        for j in jobs:
            r = why(j, cfg)
            key = (j.get("url") or "").split("?")[0]
            if r.startswith("kept") and key in seen:
                r = "duplicate of another source"
            if r.startswith("kept"):
                seen.add(key)
                total_kept += 1
            reasons[r] += 1
            if len(samples[r]) < 8:
                samples[r].append(f"{j.get('title')} | {j.get('company')} | {j.get('location')} | {j.get('posted')}")
        report[name] = {"fetched": len(jobs), "reasons": dict(reasons.most_common()), "samples": samples}
        print(f"\n== {name}: {len(jobs)} fetched")
        for r, n in reasons.most_common():
            print(f"   {n:4d}  {r}")
            for s in samples[r][:3]:
                print(f"           e.g. {s[:120]}")
    print(f"\nTOTAL kept (unique, before matching against jobs already saved): {total_kept}")
    with open("diagnose_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)


if __name__ == "__main__":
    main(sys.argv[1:])
