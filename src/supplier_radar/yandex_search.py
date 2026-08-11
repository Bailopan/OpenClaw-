from __future__ import annotations

import base64
import os
import xml.etree.ElementTree as ET

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
        rows.append({"url": url, "title": title, "snippet": passages})
    return rows


async def search(query: str, *, results: int = 20) -> list[dict]:
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
