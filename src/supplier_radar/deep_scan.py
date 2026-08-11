from __future__ import annotations

import asyncio
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

TARGET_WORDS = ("каталог", "продукц", "товар", "ассортимент", "опт", "прайс", "стм", "контракт", "производств", "b2b")
BAD_WORDS = ("ваканс", "новост", "политик", "доставка", "оплата", "личный кабинет", "корзина")
SKU_HINT = re.compile(r"\b(?:\d{2,4}\s?(?:мл|г|гр|кг|л)|\d+\s?(?:шт|pcs))\b", re.I)
PRICE_HINT = re.compile(r"\b\d[\d\s]{1,7}\s?(?:₽|руб\.?)(?:\s*/\s*шт\.?)?", re.I)


def _same_domain(base: str, url: str) -> bool:
    return urlparse(base).netloc.lower().removeprefix("www.") == urlparse(url).netloc.lower().removeprefix("www.")


def _candidate_links(soup: BeautifulSoup, base: str, limit: int) -> list[str]:
    scored=[]
    for a in soup.find_all("a", href=True):
        label=" ".join(a.stripped_strings).lower(); href=urljoin(base,str(a.get("href") or "")); low=href.lower()
        if not href.startswith(("http://","https://")) or not _same_domain(base,href): continue
        score=sum(2 for x in TARGET_WORDS if x in label or x in low)-sum(3 for x in BAD_WORDS if x in label or x in low)
        if score>0: scored.append((score,href.split("#")[0]))
    return list(dict.fromkeys(x[1] for x in sorted(scored,reverse=True)))[:limit]


def _sku_candidates(soup: BeautifulSoup, source_url: str, limit: int=30) -> list[dict]:
    found=[]; seen=set()
    selectors=("h1","h2","h3","h4",".product-title",".product-name","[itemprop=name]","a")
    for node in soup.select(",".join(selectors)):
        text=" ".join(node.stripped_strings).strip()
        if not (4<=len(text)<=140): continue
        low=text.lower()
        if any(x in low for x in ("каталог","контакты","главная","подробнее","читать","корзина")): continue
        parent=" ".join(node.parent.stripped_strings)[:500] if node.parent else text
        productish=bool(SKU_HINT.search(parent) or PRICE_HINT.search(parent) or any(x in low for x in ("средство","очиститель","гель","спрей","шампун","крем","паста","контейнер","органайзер","пакет","салфет","щетка","щётка","флакон","дозатор")))
        key=" ".join(low.split())
        if productish and key not in seen:
            seen.add(key); found.append({"name":text,"context":parent,"source_url":source_url})
            if len(found)>=limit: break
    return found


async def deep_scan_supplier(row: dict, *, max_pages: int=6) -> dict:
    base=str(row.get("final_url") or row.get("url") or "")
    if not base.startswith(("http://","https://")): return row
    headers={"User-Agent":"SupplierRadar/1.2 (+catalog and procurement research)"}
    pages=[]; skus=[]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12,connect=6),follow_redirects=True,headers=headers) as client:
            first=await client.get(base)
            if first.status_code>=400 or "html" not in first.headers.get("content-type","").lower(): return row
            soup=BeautifulSoup(first.text[:1_000_000],"html.parser"); links=_candidate_links(soup,str(first.url),max_pages)
            for url in links:
                try:
                    r=await client.get(url)
                    if r.status_code>=400 or "html" not in r.headers.get("content-type","").lower(): continue
                    ps=BeautifulSoup(r.text[:1_000_000],"html.parser"); pages.append(url); skus.extend(_sku_candidates(ps,url,30))
                except Exception: continue
    except Exception as exc:
        row["deep_scan_error"]=type(exc).__name__; return row
    unique=[]; seen=set()
    for sku in skus:
        key=" ".join(sku["name"].lower().split())
        if key not in seen: seen.add(key); unique.append(sku)
    row["deep_pages_scanned"]=pages; row["sku_candidates"]=unique[:30]; row["sku_count"]=len(unique[:30]); return row


async def deep_scan_top(rows: list[dict], *, top_n: int=30, max_pages: int=6, concurrency: int=4) -> list[dict]:
    selected=rows[:max(0,top_n)]; sem=asyncio.Semaphore(max(1,concurrency))
    async def guarded(row):
        async with sem: return await deep_scan_supplier(dict(row),max_pages=max_pages)
    enriched=await asyncio.gather(*(guarded(x) for x in selected)); by_url={str(x.get("url")):x for x in enriched}
    return [by_url.get(str(x.get("url")),x) for x in rows]
