"""Local dashboard server.  Usage: python app.py  ->  http://127.0.0.1:8765"""
from __future__ import annotations

import json
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import fetch
import store

try:  # batch apply / autofill (separate module; app works without it)
    import autofill
except ImportError:
    autofill = None

import prep
import settings

try:  # Copilot: queue, follow-ups, outreach, insights, alerts (separate module; app works without it)
    import copilot
except ImportError:
    copilot = None

PORT = 8765
INDEX = Path(__file__).with_name("index.html")
THEME = Path(__file__).with_name("theme.css")
SETUP = Path(__file__).with_name("setup.html")
STATE = {"running": False, "log": [], "last": None, "summary": None, "started": None, "eta_seconds": None}


def _refresh():
    try:
        eta = fetch.estimate_seconds()
    except Exception:
        eta = 540
    STATE.update(running=True, log=[], started=datetime.now().isoformat(timespec="seconds"), eta_seconds=eta)
    try:
        STATE["summary"] = fetch.run(log=lambda m: STATE["log"].append(m))
    except Exception as e:  # keep server alive
        STATE["log"].append(f"ERROR: {e}")
    STATE.update(running=False, last=datetime.now().isoformat(timespec="seconds"))


def _safe(o):
    """Replace NaN/inf with None so the browser can parse the JSON."""
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    if isinstance(o, dict):
        return {k: _safe(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_safe(v) for v in o]
    return o


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(_safe(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self._get()
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def _get(self):
        if autofill and self.path.startswith(("/apply", "/api/apply")):
            return autofill.handle(self, "GET")
        if copilot and self.path.startswith(("/copilot", "/api/copilot")):
            return copilot.handle(self, "GET")
        if self.path.startswith(("/prep", "/api/prep")):
            return prep.handle(self, "GET")
        if self.path.split("?")[0] in ("/setup", "/setup/"):
            return self._send(200, SETUP.read_bytes(), "text/html; charset=utf-8")
        if self.path == "/api/setup":
            return self._send(200, settings.state())
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        if self.path.split("?")[0] == "/theme.css":
            return self._send(200, THEME.read_bytes(), "text/css; charset=utf-8")
        if self.path == "/api/jobs":
            with store.conn() as c:
                cfg = fetch.load_cfg()
                locs = [L["label"] for L in cfg["locations"]] + (["Remote"] if cfg["remote"].get("enabled") else [])
                if not cfg["locations"]:
                    locs.append("Any location")
                elif cfg.get("country") and cfg.get("keep_unspecified_location", True):
                    locs.append("City not stated")  # filters.UNSPECIFIED: e.g. LinkedIn's plain "India"
                return self._send(200, {"jobs": store.all_jobs(c, cfg), "refresh": STATE,
                                        "min_salary": cfg["min_salary"], "money": cfg["currency"],
                                        "locations": locs, "setup_complete": cfg.get("setup_complete", False)})
        if self.path == "/api/refresh":
            est = STATE["eta_seconds"]
            if not STATE["running"]:
                try:
                    est = fetch.estimate_seconds()
                except Exception:
                    pass
            return self._send(200, {**STATE, "estimate_seconds": est})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b"{}"
        if copilot and self.path.startswith(("/copilot", "/api/copilot")):
            if "form-urlencoded" in (self.headers.get("Content-Type") or ""):
                from urllib.parse import parse_qs
                body = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            else:
                body = json.loads(raw)
            return copilot.handle(self, "POST", body)
        body = json.loads(raw)
        if self.path.startswith("/api/prep"):
            return prep.handle(self, "POST", body)
        if self.path == "/api/setup":
            try:
                return self._send(200, settings.save(body))
            except (ValueError, TypeError) as e:
                return self._send(400, {"error": str(e)})
        if autofill and self.path.startswith(("/apply", "/api/apply")):
            return autofill.handle(self, "POST", body)
        if self.path == "/api/refresh":
            if not STATE["running"]:
                threading.Thread(target=_refresh, daemon=True).start()
            return self._send(202, {"started": True})
        if self.path.startswith("/api/jobs/"):
            jid = self.path.rsplit("/", 1)[1]
            try:
                with store.conn() as c:
                    store.update(c, jid, body.get("status"), body.get("notes"))
                return self._send(200, {"ok": True})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})


def _open(url):
    """Open in Chrome when installed (the Batch apply extension lives there), else the default browser."""
    import os
    for exe in (r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                r"%LocalAppData%\Google\Chrome\Application\chrome.exe"):
        exe = os.path.expandvars(exe)
        if os.path.exists(exe):
            webbrowser.register("chrome", None, webbrowser.BackgroundBrowser(exe))
            return webbrowser.get("chrome").open(url)
    webbrowser.open(url)


if __name__ == "__main__":
    settings.ensure_files()
    with store.conn() as c:
        store.repair(c)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    url = f"http://127.0.0.1:{PORT}"
    print(f"Jobber running at {url}  (Ctrl+C to stop)")
    threading.Timer(0.8, lambda: _open(url)).start()
    srv.serve_forever()
