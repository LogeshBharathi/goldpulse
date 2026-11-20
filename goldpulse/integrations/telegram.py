"""Telegram Bot API sender (plain text; no parse_mode so nothing needs escaping)."""
from __future__ import annotations

TELEGRAM_LIMIT = 4096


def send_message(token: str, chat_id: str, text: str, session=None, timeout: float = 10.0) -> dict:
    if not token or not chat_id:
        raise RuntimeError("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    if session is None:
        import requests
        session = requests.Session()
    r = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                     json={"chat_id": chat_id, "text": text[:TELEGRAM_LIMIT],
                           "disable_web_page_preview": True}, timeout=timeout)
    body = r.json()
    if r.status_code != 200 or not body.get("ok"):
        raise RuntimeError(f"Telegram send failed: HTTP {r.status_code} {str(body)[:200]}")
    return body
