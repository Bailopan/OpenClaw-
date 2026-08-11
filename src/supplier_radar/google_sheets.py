from __future__ import annotations

import json
import os
from urllib.parse import urlparse

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SUPPLIERS_RANGE = "Поставщики!A:T"
RUN_LOG_RANGE = "Журнал прогонов!A:H"


def _domain(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")


def _session():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
    return AuthorizedSession(creds)


def _api(sheet_id: str, range_: str) -> str:
    from urllib.parse import quote
    return f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(range_, safe='!')}"


def append_results(rows: list[dict], summary: dict) -> dict:
    session = _session()
    sheet_id = os.getenv("SUPPLIER_SHEET_ID", "").strip()
    if session is None or not sheet_id:
        return {"enabled": False, "appended": 0, "reason": "google_credentials_missing"}

    current = session.get(_api(sheet_id, SUPPLIERS_RANGE), timeout=30)
    current.raise_for_status()
    values = current.json().get("values") or []
    existing_domains: set[str] = set()
    max_numeric_id = 0
    for raw in values[1:]:
        if raw:
            try:
                max_numeric_id = max(max_numeric_id, int(str(raw[0]).strip()))
            except (ValueError, TypeError):
                pass
        if len(raw) > 7 and raw[7]:
            existing_domains.add(_domain(str(raw[7])))

    new_rows: list[list] = []
    for row in rows:
        domain = _domain(str(row.get("url") or ""))
        if not domain or domain in existing_domains:
            continue
        if int(row.get("supplier_score") or 0) < 24:
            continue
        max_numeric_id += 1
        existing_domains.add(domain)
        contacts = ", ".join([*(row.get("phones") or []), *(row.get("emails") or [])])[:500]
        comment = (
            f"auto branch={row.get('branch','')}; query={row.get('query','')}; "
            f"score={row.get('supplier_score',0)}; INN={','.join(row.get('inns') or []) or '—'}"
        )[:1000]
        new_rows.append([
            max_numeric_id,
            str(row.get("title") or domain)[:300],
            "Автокандидат",
            str(row.get("category") or "")[:200],
            "Не проверено",
            str(row.get("region") or "")[:200],
            "",
            str(row.get("url") or "")[:1000],
            contacts,
            str(row.get("snippet") or "")[:1000],
            "",
            "Не проверено",
            "Не проверено",
            "Не проверено",
            "Не проверено",
            int(row.get("supplier_score") or 0),
            "Автопоиск — проверить",
            str(row.get("url") or "")[:1000],
            summary.get("finished_at_msk", ""),
            comment,
        ])

    if new_rows:
        url = _api(sheet_id, SUPPLIERS_RANGE) + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
        resp = session.post(url, json={"values": new_rows}, timeout=30)
        resp.raise_for_status()

    log_row = [[
        summary.get("run_id", ""),
        summary.get("finished_at_msk", ""),
        "Yandex Cloud / deferred Search API",
        summary.get("requests_used", 0),
        summary.get("raw_results", 0),
        len(new_rows),
        (rows[0].get("title") if rows else ""),
        f"cost≈{summary.get('estimated_search_cost_rub',0)} ₽; errors={summary.get('query_errors',0)}",
    ]]
    log_url = _api(sheet_id, RUN_LOG_RANGE) + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    log_resp = session.post(log_url, json={"values": log_row}, timeout=30)
    log_resp.raise_for_status()
    return {"enabled": True, "appended": len(new_rows)}
