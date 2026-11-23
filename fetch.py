"""Fetch jobs from all enabled sources, filter, and store.  Usage: python fetch.py"""
from __future__ import annotations

import collections
import json
import time
from datetime import datetime
from pathlib import Path

import filters
import settings
import sources
import store

def load_cfg():
    return settings.load_cfg()


def run(log=print) -> dict:
    cfg = load_cfg()
    if not cfg["search_terms"] and not cfg.get("role_tiers", {}).get("core"):
        log("No search terms yet: open the Setup page (/setup) and choose what to search for.")
        return dict(raw=0, matched=0, new=0, below_bar=0, sources={})
    enabled = cfg["sources"]
    raw: list[dict] = []
    stats = {}

    def take(name, fn):
        t = time.time()
        try:
            got = fn()
            stats[name] = {"fetched": len(got)}
            raw.extend(got)
            log(f"{name}: {len(got)} fetched ({time.time() - t:.0f}s)")
        except Exception as e:
            stats[name] = {"fetched": 0, "error": str(e)[:200]}
            log(f"{name}: FAILED {str(e)[:150]}")

    for name, fn in sources.OFFICIAL.items():
        if enabled.get(name):
            take(name, lambda fn=fn: fn(cfg))
    log("Searching for: " + ", ".join(sources.queries(cfg)))
    js_sites = [s for s in sources.JOBSPY if enabled.get(s)]
    if "linkedin" in js_sites and cfg.get("linkedin_mode", "browser") == "browser":
        try:
            import linkedin_browser
            with store.conn() as c:
                known = {r[0] for r in c.execute("SELECT url FROM jobs WHERE url LIKE '%linkedin.com%'")}
            log("linkedin (logged-out browser): searching newest first...")
            take("linkedin", lambda: linkedin_browser.fetch(cfg, known, log))
            js_sites.remove("linkedin")
        except ImportError:
            log("linkedin: Playwright not installed (pip install playwright); using logged-out mode")
    if js_sites:
        log(f"jobspy ({', '.join(js_sites)}): searching, takes a few minutes...")
        take("jobspy", lambda: sources.jobspy_sites(cfg, js_sites, log))

    if enabled.get("company_boards"):
        import ats
        with store.conn() as c:
            ats.remember(c, [j.get("apply_url") or j.get("url") for j in raw], log)
            take("career_portals", lambda: ats.run(cfg, c, log))

    import ats as _ats
    kept = new = soft = 0
    dropped = collections.Counter()
    by_site: dict[str, dict] = {}  # per job site (naukri, greenhouse...), not per fetcher
    with store.conn() as c:
        for j in raw:
            st = by_site.setdefault(j.get("source") or "?", {"fetched": 0, "kept": 0, "new": 0})
            st["fetched"] += 1
            if not isinstance(j.get("title"), str) or not isinstance(j.get("url"), str) or not j["url"]:
                dropped["no title/url"] += 1
                continue
            ev = filters.evaluate(j, cfg)
            if ev is None:
                dropped[filters.drop_reason(j, cfg) or "other"] += 1
                continue
            j.update(ev)
            j["ats"] = _ats.ats_of(j.get("apply_url") or j["url"])
            kept += 1
            soft += bool(ev["filtered"])
            is_new = store.upsert(c, j)
            new += is_new
            st["kept"] += 1
            st["new"] += is_new
        c.commit()
    summary = dict(raw=len(raw), matched=kept, new=new, below_bar=soft, sources=stats, by_site=by_site,
                   dropped=dict(dropped.most_common()), queries=sources.queries(cfg))
    for name, st in sorted(by_site.items(), key=lambda kv: -kv[1]["kept"]):
        log(f"  {name}: {st['fetched']} fetched, {st['kept']} relevant, {st['new']} new")
    if dropped:
        log("  dropped: " + ", ".join(f"{n} {r}" for r, n in dropped.most_common()))
    log(f"Done: {len(raw)} raw -> {kept} relevant ({soft} below salary/exp bar), {new} new")
    try:  # last search's numbers, for troubleshooting (python diagnose.py goes deeper)
        (Path(__file__).with_name("last_search.json")).write_text(
            json.dumps(dict(summary, at=datetime.now().isoformat(timespec="seconds")), indent=1), encoding="utf-8")
    except OSError:
        pass
    try:  # Copilot: AI-score new jobs (if a key is set) + funding radar
        import copilot
        summary["copilot"] = copilot.post_fetch(log)
    except ImportError:
        pass
    except Exception as e:
        log(f"Copilot step skipped: {str(e)[:150]}")
    return summary


if __name__ == "__main__":
    run()
