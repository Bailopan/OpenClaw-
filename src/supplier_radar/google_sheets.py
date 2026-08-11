from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import os
import re
from urllib.parse import quote, urlparse

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
SUPPLIERS_RANGE = "Поставщики!A:T"
CANDIDATES_RANGE = "Автокандидаты!A:T"
RUN_LOG_RANGE = "Журнал прогонов!A:H"
STATE_RANGE = "Состояние радара!A:N"
MSK = timezone(timedelta(hours=3))
INN_RE = re.compile(r"\b(?:ИНН\s*[:№]?\s*)?(\d{10}|\d{12})\b", re.I)
PHONE_RE = re.compile(r"(?:\+7|8)[\s()\-\d]{9,18}")


def _domain(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")


def _norm_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits if len(digits) >= 10 else ""


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
    return f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{quote(range_, safe='!')}"


def _read(session, sheet_id: str, range_: str) -> list[list]:
    response = session.get(_api(sheet_id, range_), timeout=30)
    response.raise_for_status()
    return response.json().get("values") or []


def _append(session, sheet_id: str, range_: str, values: list[list]) -> None:
    if not values:
        return
    url = _api(sheet_id, range_) + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS"
    response = session.post(url, json={"values": values}, timeout=30)
    response.raise_for_status()


def _history_item(*, title: str, url: str, snippet: str, region: str, category: str, branch: str, score: int) -> dict:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "domain": _domain(url),
        "title": title,
        "url": url,
        "snippet": snippet[:1000],
        "region": region,
        "category": category,
        "branch": branch,
        "best_score": score,
        "last_score": score,
        "first_seen": now,
        "last_seen": now,
        "seen_runs": 1,
    }


def load_history_from_sheet() -> dict[str, dict]:
    """Use the verified base + staging tab as durable discovery memory."""
    session = _session()
    sheet_id = os.getenv("SUPPLIER_SHEET_ID", "").strip()
    if session is None or not sheet_id:
        return {}

    history: dict[str, dict] = {}
    try:
        verified = _read(session, sheet_id, SUPPLIERS_RANGE)
        staged = _read(session, sheet_id, CANDIDATES_RANGE)
    except Exception:
        return {}

    for raw in verified[1:]:
        if len(raw) <= 7:
            continue
        url = str(raw[7] or "")
        domain = _domain(url)
        if not domain:
            continue
        score = 0
        if len(raw) > 15:
            try:
                score = int(float(raw[15]))
            except (TypeError, ValueError):
                pass
        history[domain] = _history_item(
            title=str(raw[1] if len(raw) > 1 else domain),
            url=url,
            snippet=str(raw[9] if len(raw) > 9 else ""),
            region=str(raw[5] if len(raw) > 5 else ""),
            category=str(raw[3] if len(raw) > 3 else ""),
            branch="verified_sheet",
            score=score,
        )

    for raw in staged[1:]:
        if len(raw) <= 2:
            continue
        domain = str(raw[0] or "").strip().lower() or _domain(str(raw[2] or ""))
        if not domain:
            continue
        try:
            score = int(float(raw[3])) if len(raw) > 3 and raw[3] != "" else 0
        except (TypeError, ValueError):
            score = 0
        item = _history_item(
            title=str(raw[1] if len(raw) > 1 else domain),
            url=str(raw[2] if len(raw) > 2 else ""),
            snippet=str(raw[19] if len(raw) > 19 else ""),
            region=str(raw[5] if len(raw) > 5 else ""),
            category=str(raw[6] if len(raw) > 6 else ""),
            branch=str(raw[7] if len(raw) > 7 else "staged_sheet"),
            score=score,
        )
        previous = history.get(domain)
        if previous:
            item["best_score"] = max(int(previous.get("best_score") or 0), score)
        history[domain] = item
    return history


def _existing_identity_sets(verified: list[list], staged: list[list]) -> tuple[set[str], set[str], set[str]]:
    domains: set[str] = set()
    inns: set[str] = set()
    phones: set[str] = set()
    for raw in verified[1:]:
        if len(raw) > 7 and raw[7]:
            domains.add(_domain(str(raw[7])))
        text = " ".join(str(x) for x in raw)
        inns.update(INN_RE.findall(text))
        phones.update(p for p in (_norm_phone(x) for x in PHONE_RE.findall(text)) if p)
    for raw in staged[1:]:
        if raw:
            domains.add(str(raw[0] or "").strip().lower())
        if len(raw) > 11 and raw[11]:
            inns.update(INN_RE.findall(str(raw[11])))
        if len(raw) > 9 and raw[9]:
            phones.update(p for p in (_norm_phone(x) for x in PHONE_RE.findall(str(raw[9]))) if p)
    domains.discard("")
    return domains, inns, phones


def append_runtime_state(summary: dict, stage: str, blocker: str = "") -> dict:
    session = _session()
    sheet_id = os.getenv("SUPPLIER_SHEET_ID", "").strip()
    if session is None or not sheet_id:
        return {"enabled": False}
    now_msk = datetime.now(MSK).isoformat(timespec="seconds")
    row = [[
        summary.get("plan_slot", ""),
        summary.get("run_id", ""),
        summary.get("started_at_msk", ""),
        summary.get("finished_at_msk", ""),
        summary.get("status", "IN_PROGRESS"),
        stage,
        summary.get("requests_used", 0),
        summary.get("raw_results", 0),
        summary.get("unique_domains", 0),
        summary.get("new_candidates", 0),
        summary.get("estimated_search_cost_rub", 0),
        summary.get("checkpoint", ""),
        blocker,
        now_msk,
    ]]
    _append(session, sheet_id, STATE_RANGE, row)
    return {"enabled": True}


def append_results(rows: list[dict], summary: dict) -> dict:
    """Stage machine-found candidates; never auto-confirm into the verified supplier base."""
    session = _session()
    sheet_id = os.getenv("SUPPLIER_SHEET_ID", "").strip()
    if session is None or not sheet_id:
        return {"enabled": False, "candidates_appended": 0, "reason": "google_credentials_missing"}

    verified = _read(session, sheet_id, SUPPLIERS_RANGE)
    staged = _read(session, sheet_id, CANDIDATES_RANGE)
    domains, existing_inns, existing_phones = _existing_identity_sets(verified, staged)
    min_score = int(os.getenv("SUPPLIER_CANDIDATE_MIN_SCORE", "40"))
    max_append = int(os.getenv("SUPPLIER_CANDIDATE_MAX_APPEND", "120"))

    new_rows: list[list] = []
    for row in sorted(rows, key=lambda x: int(x.get("supplier_score") or 0), reverse=True):
        score = int(row.get("supplier_score") or 0)
        if score < min_score or len(new_rows) >= max_append:
            continue
        domain = _domain(str(row.get("final_url") or row.get("url") or ""))
        if not domain or domain in domains:
            continue
        inns = [str(x) for x in (row.get("inns") or []) if str(x)]
        phones_raw = [str(x) for x in (row.get("phones") or []) if str(x)]
        phones = [p for p in (_norm_phone(x) for x in phones_raw) if p]
        if any(x in existing_inns for x in inns) or any(x in existing_phones for x in phones):
            continue

        domains.add(domain)
        existing_inns.update(inns)
        existing_phones.update(phones)
        evidence = row.get("geo_evidence") or []
        status = "Приоритет — геопроверка" if row.get("geo_verified") and score >= 70 else "Проверить"
        comment = (
            f"auto; score={score}; geo={bool(row.get('geo_verified'))}; "
            f"evidence={' | '.join(str(x) for x in evidence[:3]) or '—'}"
        )[:1500]
        new_rows.append([
            domain,
            str(row.get("title") or domain)[:300],
            str(row.get("final_url") or row.get("url") or "")[:1000],
            score,
            str(row.get("company_type") or "")[:120],
            str(row.get("region") or "")[:150],
            str(row.get("category") or "")[:150],
            str(row.get("branch") or "")[:80],
            str(row.get("query") or "")[:500],
            ", ".join(phones_raw)[:500],
            ", ".join(str(x) for x in (row.get("emails") or []))[:500],
            ", ".join(inns)[:120],
            "Да" if row.get("has_price") else "Нет/не найден",
            int(row.get("sku_count") or 0),
            summary.get("finished_at_msk", ""),
            summary.get("finished_at_msk", ""),
            summary.get("run_id", ""),
            status,
            str(row.get("final_url") or row.get("url") or "")[:1000],
            comment,
        ])

    _append(session, sheet_id, CANDIDATES_RANGE, new_rows)

    top = " / ".join(str(x.get("title") or "") for x in rows[:5] if x.get("title"))[:1000]
    checkpoint = (
        f"run={summary.get('run_id','')}; status={summary.get('status','')}; "
        f"duration={summary.get('duration_seconds',0)}s; staged={len(new_rows)}; "
        f"strong70={summary.get('strong_70_plus',0)}; geo={summary.get('geo_verified',0)}; "
        f"cost≈{summary.get('estimated_search_cost_rub',0)} ₽; errors={summary.get('query_errors',0)}; "
        f"checkpoint={summary.get('checkpoint','')}"
    )[:3000]
    _append(session, sheet_id, RUN_LOG_RANGE, [[
        summary.get("run_id", ""),
        summary.get("finished_at_msk", ""),
        "Yandex Cloud / Search API / staging",
        summary.get("requests_used", 0),
        f"{summary.get('unique_domains',0)} домен / {summary.get('strong_70_plus',0)} score>=70",
        0,
        top,
        checkpoint,
    ]])

    return {
        "enabled": True,
        "candidates_appended": len(new_rows),
        "confirmed_appended": 0,
        "main_base_protected": True,
    }
