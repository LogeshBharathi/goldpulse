"""Send a Telegram message when a workflow fails. Does nothing if the secrets are unset."""
from __future__ import annotations

import os
import sys

import requests


def send(text: str, token: str | None = None, chat_id: str | None = None, post=None) -> bool:
    token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("telegram not configured; no message sent")
        return False
    post = post or requests.post
    response = post(f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text}, timeout=20)
    if getattr(response, "status_code", 200) >= 400:
        raise RuntimeError(f"telegram HTTP {response.status_code}")
    return True


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    workflow = os.environ.get("GITHUB_WORKFLOW", "GoldPulse")
    text = " ".join(argv) or f"{workflow} failed. Check the Actions tab."
    try:
        send(text)
    except Exception as exc:  # noqa: BLE001
        print(f"notification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
