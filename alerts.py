"""Telegram alerts: a daily digest of fresh, high-fit jobs, follow-ups due, and new funding news.

Setup: put your bot token in secrets.json (telegram_bot_token), send any message to your bot, then run
`python alerts.py --chat-id` (or press "Find my chat" on /copilot). Daily: `python alerts.py --fetch`
(daily.bat runs this; install it from /copilot or install_daily.bat).
"""
from __future__ import annotations

import html
import sys

import requests

import copilot
import fetch
import llm
from settings import money

API = "https://api.telegram.org/bot{}/{}"


def _token() -> str:
    t = (llm.secrets().get("telegram_bot_token") or "").strip()
    if not t or t.startswith("PASTE"):
        raise RuntimeError("Add telegram_bot_token in secrets.json (or on the Copilot page).")
    return t


def _call(method, **params):
    for attempt in range(3):  # Telegram can be slow from some networks
        try:
            r = requests.post(API.format(_token(), method), json=params, timeout=45)
            break
        except requests.RequestException:
            if attempt == 2:
                raise
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description', r.status_code)}")
    return data["result"]


def find_chat_id() -> dict:
    """Reads the latest message sent to your bot and stores its chat id."""
    if _call("getWebhookInfo").get("url"):
        _call("deleteWebhook")  # a webhook hides messages from getUpdates
    ups = _call("getUpdates", limit=50, allowed_updates=["message", "my_chat_member"])
    chats = [(u.get("message") or u.get("my_chat_member") or {}).get("chat") for u in ups]
    chats = [c for c in chats if c]
    if not chats:
        me = _call("getMe").get("username", "?")
        raise RuntimeError(f"No messages yet for @{me}: open it in Telegram, press Start or send 'hi', then try again.")
    chat = chats[-1]
    llm.save_secrets({"telegram_chat_id": str(chat["id"])})
    return {"ok": True, "chat_id": chat["id"], "name": chat.get("first_name") or chat.get("title") or ""}


def send(text: str) -> None:
    chat = str(llm.secrets().get("telegram_chat_id") or "").strip()
    if not chat:
        find_chat_id()
        chat = str(llm.secrets().get("telegram_chat_id"))
    for i in range(0, len(text), 3900):  # Telegram limit is 4096 chars
        _call("sendMessage", chat_id=chat, text=text[i:i + 3900], parse_mode="HTML", disable_web_page_preview=True)


def test() -> dict:
    send("✅ Jobber is connected. Your daily job digest will arrive here.")
    return {"ok": True}


def digest(c, cfg) -> str:
    s = copilot.settings(cfg)
    jobs = copilot.enrich(c, cfg)
    q = [j for j in copilot.queue(jobs, cfg) if j["best_fit"] >= s["alert_min_fit"]][: s["alert_top_n"]]
    fu = copilot.followups(jobs, cfg)
    fund = [f for f in copilot.funding_list(c) if f["seen"][:10] == copilot._now()[:10]][:3]
    e = html.escape
    lines = [f"<b>Jobber · {len(q)} to apply today</b>"]
    for j in q:
        tag = "⭐ " if j["watch"] else ""
        pay = f" · {money(j['sal_max_lpa'], cfg)}" if j.get("sal_max_lpa") else ""
        age = "today" if j["age_days"] == 0 else f"{j['age_days']}d ago" if j["age_days"] is not None else "new"
        lines.append(f"\n{tag}<b>{e(j['title'])}</b> · {e(j['company'])}\n{j['best_fit']}% fit{pay} · {age} · "
                     f"<a href=\"{e(j['apply_url'] or j['url'])}\">Apply</a>")
    if not q:
        lines.append("\nNo new high-fit jobs since yesterday.")
    if fu:
        lines.append(f"\n<b>{len(fu)} follow-ups due</b> (applied 7+ days, no reply): "
                     + ", ".join(e(j["company"]) for j in fu[:6]))
    if fund:
        lines.append("\n<b>Just funded</b> (often hiring before they post jobs):")
        lines += [f"• <a href=\"{e(f['link'])}\">{e(f['title'][:110])}</a>" for f in fund]
    lines.append("\nOpen http://127.0.0.1:8765/copilot for drafts and tracking.")
    return "\n".join(lines)


def send_digest() -> dict:
    cfg = fetch.load_cfg()
    with copilot.conn() as c:
        text = digest(c, cfg)
    send(text)
    return {"ok": True, "chars": len(text)}


if __name__ == "__main__":
    if "--chat-id" in sys.argv:
        print(find_chat_id())
    else:
        if "--fetch" in sys.argv:
            fetch.run()  # fetch.run also runs the Copilot post-fetch step
        try:
            print(send_digest())
        except RuntimeError as e:  # Telegram not set up: the fetch still ran
            print(f"Digest not sent: {e}")
