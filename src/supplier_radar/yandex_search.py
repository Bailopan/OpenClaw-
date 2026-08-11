from __future__ import annotations

import base64
import os
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable

import httpx

YANDEX_SEARCH_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"


def _clean(value: str | None) -> str:
    return " ".join((value or "").split())


def parse_search_xml(raw_xml: str) -> list[dict]:
    root = ET.fromstring(raw_xml)
    rows: list[dict] = []
    for node in root.findall(".//doc"):
        url = _clean(node.findtext("url"))
        title_node = node.find("title")
        title = _clean("".join(title_node.itertext())) if title_node is not None else url
        passages = " ".join(_clean("".join(item.itertext())) for item in node.findall(".//passage"))
        if url or title or passages:
            rows.append({"url": url, "title": title, "snippet": passages})
    return rows


@dataclass
class SearchResponse:
    query: str
    rows: list[dict]
    error: str | None = None


class DeferredSearchClient:
    """Cheap Search API client using Yandex AI Studio SDK deferred web search."""

    def __init__(self, *, docs_on_page: int = 20) -> None:
        from yandex_ai_studio_sdk import AIStudio

        api_key = os.environ["YANDEX_SEARCH_API_KEY"]
        folder_id = os.environ["YANDEX_FOLDER_ID"]
        self._sdk = AIStudio(folder_id=folder_id, auth=api_key)
        self._search = self._sdk.search_api.web(
            search_type="RU",
            groups_on_page=max(1, min(100, docs_on_page)),
            docs_in_group=1,
            max_passages=5,
        )

    def start(self, query: str):
        return self._search.run_deferred(query, format="xml", timeout=30)

    @staticmethod
    def _wait(operation) -> bytes:
        try:
            raw = operation.wait(timeout=180)
        except TypeError:
            raw = operation.wait()
        if isinstance(raw, str):
            return raw.encode("utf-8")
        return bytes(raw)

    def search_many(self, queries: Iterable[str], *, workers: int = 8) -> list[SearchResponse]:
        started: list[tuple[str, object]] = []
        out: list[SearchResponse] = []
        for query in queries:
            try:
                started.append((query, self.start(query)))
            except Exception as exc:
                out.append(SearchResponse(query=query, rows=[], error=f"start:{type(exc).__name__}"))

        with ThreadPoolExecutor(max_workers=max(1, min(10, workers))) as pool:
            future_map = {pool.submit(self._wait, op): query for query, op in started}
            for future in as_completed(future_map):
                query = future_map[future]
                try:
                    raw = future.result().decode("utf-8", errors="replace")
                    out.append(SearchResponse(query=query, rows=parse_search_xml(raw)))
                except Exception as exc:
                    out.append(SearchResponse(query=query, rows=[], error=f"wait:{type(exc).__name__}"))
        return out


async def search_sync(query: str, *, results: int = 20) -> list[dict]:
    """Synchronous paid endpoint kept only as an explicit fallback/debug mode."""
    api_key = os.environ["YANDEX_SEARCH_API_KEY"]
    folder_id = os.environ["YANDEX_FOLDER_ID"]
    payload = {
        "query": {
            "searchType": "SEARCH_TYPE_RU",
            "queryText": query,
            "familyMode": "FAMILY_MODE_MODERATE",
            "page": "0",
            "fixTypoMode": "FIX_TYPO_MODE_ON",
        },
        "groupSpec": {
            "groupMode": "GROUP_MODE_FLAT",
            "groupsOnPage": str(max(1, min(100, results))),
            "docsInGroup": "1",
        },
        "maxPassages": "5",
        "l10n": "LOCALIZATION_RU",
        "folderId": folder_id,
        "responseFormat": "FORMAT_XML",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            YANDEX_SEARCH_URL,
            headers={"Authorization": f"Api-key {api_key}"},
            json=payload,
        )
        response.raise_for_status()
        raw_data = response.json().get("rawData", "")
    return parse_search_xml(base64.b64decode(raw_data).decode("utf-8", errors="replace"))
