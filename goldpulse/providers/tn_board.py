"""Tamil Nadu 22K board rates from BankBazaar's Chennai gold-rate page.

The page's last-10-days table gives 22K for 8 grams (one pavun) per date, before
GST, matching the Chennai jeweller board. robots.txt allows this page; the
price-history endpoint it disallows is not used.
"""
from __future__ import annotations

import html
import re
from datetime import date, datetime

import requests

from goldpulse.providers.base import ProviderError

BANKBAZAAR_CHENNAI = "https://www.bankbazaar.com/gold-rate-chennai.html"
SOURCE = "BankBazaar (Chennai board)"
_HEADERS = {"User-Agent": "goldpulse/1.0 (local live test)"}

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)


def _text(cell: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", cell))).strip()


def _rupees(text: str) -> float:
    m = re.match(r"\D*?(\d[\d,]*(?:\.\d+)?)", text)
    if not m:
        raise ValueError(text)
    return float(m.group(1).replace(",", ""))


def parse_bankbazaar_history(page: str) -> list[tuple[date, float]]:
    """(date, 22K per gram before GST), newest first, from the 8-gram history table."""
    rows = [[_text(c) for c in _CELL.findall(r)] for r in _ROW.findall(page)]
    out: list[tuple[date, float]] = []
    in_table = False
    for cells in rows:
        if cells[:1] == ["Date"] and len(cells) > 1 and "22K" in cells[1] and "8 gram" in cells[1]:
            in_table = True
            continue
        if not in_table:
            continue
        try:
            day = datetime.strptime(cells[0], "%d %b %Y").date()
            pavun = _rupees(cells[1])
        except (ValueError, IndexError):
            break
        out.append((day, pavun / 8))
    if not out:
        raise ProviderError("bankbazaar: 22K history table not found")
    return out


def fetch_board_history(session: requests.Session | None = None, timeout: float = 20.0) -> list[tuple[date, float]]:
    s = session or requests
    r = s.get(BANKBAZAAR_CHENNAI, headers=_HEADERS, timeout=timeout)
    if r.status_code >= 400:
        raise ProviderError(f"bankbazaar: HTTP {r.status_code}")
    # The page is UTF-8 but the header sends no charset, so requests would guess Latin-1.
    return parse_bankbazaar_history(r.content.decode("utf-8", "replace"))
