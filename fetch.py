"""Fetch jobs from all enabled sources, filter, and store.  Usage: python fetch.py"""
from __future__ import annotations

import time

import filters
import settings
import sources
import store

def load_cfg():
    return settings.load_cfg()


def run(log=print) -> dict:
    cfg = load_cfg()
    if not cfg["search_terms"]:
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
    with store.conn() as c:
        for j in raw:
            if not isinstance(j.get("title"), str) or not isinstance(j.get("url"), str) or not j["url"]:
                continue
            ev = filters.evaluate(j, cfg)
            if ev is None:
                continue
            j.update(ev)
            j["ats"] = _ats.ats_of(j.get("apply_url") or j["url"])
            kept += 1
            soft += bool(ev["filtered"])
            new += store.upsert(c, j)
        c.commit()
    summary = dict(raw=len(raw), matched=kept, new=new, below_bar=soft, sources=stats)
    log(f"Done: {len(raw)} raw -> {kept} relevant ({soft} below salary/exp bar), {new} new")
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
