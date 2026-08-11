from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

PHONE_RE = re.compile(r"(?:\+7|8)[\s()\-\d]{9,18}")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
INN_RE = re.compile(r"(?:ИНН\s*[:№]?\s*)(\d{10}|\d{12})", re.I)
MONEY_RE = re.compile(r"(?:минимальн\w*\s+(?:заказ|парт)|от\s+\d+\s*(?:шт|руб|₽)|\d[\d\s]{2,}\s*(?:руб|₽))", re.I)


def _links(soup: BeautifulSoup, base: str) -> dict:
    result = {"price_links": [], "commercial_links": [], "messenger_links": []}
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        label = " ".join(a.stripped_strings).lower()
        absolute = urljoin(base, href)
        low = absolute.lower()
        if any(x in low or x in label for x in ("price", "прайс", ".xlsx", ".xls", ".pdf")):
            result["price_links"].append(absolute)
        if any(x in low or x in label for x in ("/opt", "wholesale", "опт", "b2b", "дилер", "partner", "партнер", "партнёр")):
            result["commercial_links"].append(absolute)
        if any(x in low for x in ("t.me/", "telegram.me/", "wa.me/", "whatsapp.com/")):
            result["messenger_links"].append(absolute)
    return {k: list(dict.fromkeys(v))[:8] for k, v in result.items()}


async def _fetch_one(client: httpx.AsyncClient, row: dict) -> dict:
    url = str(row.get("url") or "")
    if not url.startswith(("http://", "https://")):
        return row
    try:
        response = await client.get(url)
        if response.status_code >= 400:
            row["enrich_error"] = f"http_{response.status_code}"
            return row
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            row["content_type"] = content_type[:100]
            return row
        soup = BeautifulSoup(response.text[:1_000_000], "html.parser")
        commercial_links = _links(soup, str(response.url))
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.stripped_strings)[:80_000]
        row["page_text"] = text[:20_000]
        row["emails"] = sorted(set(EMAIL_RE.findall(text)))[:5]
        row["phones"] = sorted({" ".join(x.split()) for x in PHONE_RE.findall(text)})[:5]
        row["inns"] = sorted(set(INN_RE.findall(text)))[:3]
        row["commercial_terms"] = list(dict.fromkeys(x.strip() for x in MONEY_RE.findall(text)))[:8]
        row.update(commercial_links)
        row["has_price"] = bool(row["price_links"])
        row["has_commercial_page"] = bool(row["commercial_links"])
        row["has_direct_contact"] = bool(row["emails"] or row["phones"] or row["messenger_links"])
        row["final_url"] = str(response.url)
        row["domain"] = urlparse(str(response.url)).netloc.lower().removeprefix("www.")
        return row
    except Exception as exc:
        row["enrich_error"] = type(exc).__name__
        return row


async def enrich_candidates(rows: list[dict], *, limit: int = 40, concurrency: int = 8) -> list[dict]:
    selected = rows[: max(0, limit)]
    headers = {"User-Agent": "SupplierRadar/1.1 (+supplier research; commercial verification)"}
    limits = httpx.Limits(max_connections=max(2, concurrency), max_keepalive_connections=max(2, concurrency))
    async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0), follow_redirects=True, headers=headers, limits=limits) as client:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        async def guarded(row: dict) -> dict:
            async with semaphore:
                return await _fetch_one(client, row)
        enriched = await asyncio.gather(*(guarded(dict(row)) for row in selected))
    by_url = {str(row.get("url")): row for row in enriched}
    return [by_url.get(str(row.get("url")), row) for row in rows]
