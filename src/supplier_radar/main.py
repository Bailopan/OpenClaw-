from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timezone, timedelta
import json
import os
import time
import uuid
from pathlib import Path

from .adaptive import adaptive_queries
from .checkpoint import CheckpointWriter
from .classify import classify_supplier
from .dedupe import dedupe_by_domain
from .deep_scan import deep_scan_top
from .enrich import enrich_candidates
from .geo import detect_geo_evidence
from .google_sheets import append_results, append_runtime_state, load_history_from_sheet
from .history import load_history, merge_history, save_history
from .query_plan import PlannedQuery, build_query_plan
from .yandex_search import DeferredSearchClient

CONFIG = Path(os.getenv("SUPPLIER_RADAR_CONFIG", "config/pushkino.json"))
MSK = timezone(timedelta(hours=3))


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def _load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _cost_cap(config: dict) -> int:
    price = float(config.get("deferred_search_price_rub_per_request", 0.0305))
    per_run = float(config.get("max_search_cost_rub_per_run", 10))
    daily_share = float(config.get("max_search_cost_rub_per_day", 100)) / max(
        1, int(config.get("expected_runs_per_day", 24))
    )
    budget = min(per_run, daily_share)
    by_budget = int(budget // price) if price > 0 else int(config.get("max_search_requests_per_run", 100))
    return min(int(config.get("max_search_requests_per_run", 100)), by_budget)


def _checkpoint_path(run_id: str) -> Path:
    return Path(os.getenv("SUPPLIER_RADAR_CHECKPOINT_DIR", "outputs/checkpoints")) / f"{run_id}.jsonl"


def _compact(rows: list[dict]) -> list[dict]:
    return [
        {
            "title": row.get("title"),
            "url": row.get("url"),
            "supplier_score": int(row.get("supplier_score") or 0),
            "company_type": row.get("company_type"),
            "region": row.get("region"),
            "category": row.get("category"),
            "geo_verified": bool(row.get("geo_verified")),
        }
        for row in sorted(
            dedupe_by_domain(rows), key=lambda x: int(x.get("supplier_score") or 0), reverse=True
        )
    ]


def _rows(responses, meta: dict[str, PlannedQuery]) -> tuple[list[dict], int]:
    out: list[dict] = []
    errors = 0
    for response in responses:
        errors += bool(response.error)
        planned = meta.get(response.query)
        for row in response.rows:
            cls = classify_supplier(f"{row.get('title', '')} {row.get('snippet', '')}")
            row.update(cls)
            row["supplier_score"] = cls["score"]
            row["query"] = response.query
            row["branch"] = planned.branch if planned else ""
            row["region"] = planned.region if planned else ""
            row["category"] = planned.category if planned else ""
            out.append(row)
    return out, int(errors)


def _score(row: dict, config: dict) -> int:
    cls = classify_supplier(
        f"{row.get('title', '')} {row.get('snippet', '')} {row.get('page_text', '')}"
    )
    geo = detect_geo_evidence(
        str(row.get("page_text") or ""),
        list(config.get("regions") or []),
        list(config.get("address_clusters") or []),
    )
    score = (
        int(cls["score"])
        + 10 * bool(row.get("has_price"))
        + 5 * bool(row.get("has_commercial_page"))
        + 5 * bool(row.get("has_direct_contact"))
        + 5 * bool(row.get("inns"))
        + min(10, int(row.get("sku_count") or 0) // 3)
        + int(geo.get("geo_score") or 0)
    )
    row.update(cls)
    row.update(geo)
    row["supplier_score"] = min(100, score)
    return row["supplier_score"]


def _merge_changed(rows: list[dict], changed: list[dict]) -> list[dict]:
    by_url = {str(row.get("url")): row for row in changed}
    return [by_url.get(str(row.get("url")), row) for row in rows]


async def _safe_state(summary: dict, stage: str, blocker: str = "") -> None:
    try:
        await asyncio.to_thread(append_runtime_state, summary, stage, blocker)
    except Exception as exc:
        _log("state_write_error", stage=stage, error=type(exc).__name__)


async def run() -> dict:
    config = _load_config()
    run_id = f"SR-{datetime.now(MSK):%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
    started = time.monotonic()
    started_at = datetime.now(MSK)
    slot = int(os.getenv("SUPPLIER_PLAN_SLOT", int(datetime.now(UTC).timestamp() // 3600)))
    cap = _cost_cap(config)
    hard_limit = int(config.get("hard_runtime_limit_seconds", 3240))
    target_active = min(int(config.get("target_active_seconds", 3000)), hard_limit - 180)
    search_soft_limit = int(config.get("search_soft_runtime_limit_seconds", 900))
    reserve_seconds = int(config.get("shutdown_reserve_seconds", 240))

    def elapsed() -> float:
        return time.monotonic() - started

    def remaining() -> float:
        return hard_limit - elapsed()

    history_path = Path(os.getenv("SUPPLIER_RADAR_HISTORY", "outputs/history/suppliers.json"))
    local_history = load_history(history_path)
    try:
        sheet_history = await asyncio.to_thread(load_history_from_sheet)
    except Exception as exc:
        sheet_history = {}
        _log("sheet_history_error", error=type(exc).__name__)
    old_history = {**local_history, **sheet_history}

    base = build_query_plan(config, slot=slot, limit=cap)
    adaptive = adaptive_queries(old_history, list(config.get("regions") or []), limit=min(15, cap // 3))
    seen = {item.query for item in base}
    plan = [PlannedQuery(**item) for item in adaptive if item["query"] not in seen] + base
    plan = plan[:cap]

    price = float(config.get("deferred_search_price_rub_per_request", 0.0305))
    batch = max(1, min(50, int(config.get("checkpoint_batch_size", 20))))
    checkpoint = CheckpointWriter(_checkpoint_path(run_id))
    progress = {
        "run_id": run_id,
        "plan_slot": slot,
        "status": "IN_PROGRESS",
        "started_at_msk": started_at.isoformat(timespec="seconds"),
        "finished_at_msk": "",
        "requests_used": 0,
        "raw_results": 0,
        "unique_domains": 0,
        "new_candidates": 0,
        "estimated_search_cost_rub": 0,
        "checkpoint": str(checkpoint.path),
    }
    checkpoint.append(
        "run_start",
        run_id=run_id,
        planned_queries=len(plan),
        adaptive_queries=len(adaptive),
        estimated_max_search_cost_rub=round(len(plan) * price, 2),
        plan_slot=slot,
    )
    _log("run_start", run_id=run_id, planned_queries=len(plan), adaptive_queries=len(adaptive))
    await _safe_state(progress, "START")

    try:
        client = DeferredSearchClient(docs_on_page=int(config.get("results_per_query", 20)))
    except Exception as exc:
        progress.update(
            status="ERROR",
            finished_at_msk=datetime.now(MSK).isoformat(timespec="seconds"),
            duration_seconds=round(elapsed(), 2),
        )
        checkpoint.append("run_finish", **progress, error=type(exc).__name__)
        await _safe_state(progress, "INIT_ERROR", type(exc).__name__)
        _log("run_finish", **progress, error=type(exc).__name__)
        return {"summary": progress, "results": []}

    meta = {item.query: item for item in plan}
    rows: list[dict] = []
    errors = raw = done = 0
    stopped = False

    for batch_index, offset in enumerate(range(0, len(plan), batch), 1):
        if elapsed() >= search_soft_limit or remaining() <= reserve_seconds:
            stopped = True
            break
        queries = [item.query for item in plan[offset : offset + batch]]
        responses = await asyncio.to_thread(
            client.search_many,
            queries,
            workers=int(config.get("search_wait_workers", 8)),
            batch_size=batch,
        )
        batch_rows, batch_errors = _rows(responses, meta)
        rows.extend(batch_rows)
        errors += batch_errors
        batch_raw = sum(len(response.rows) for response in responses)
        raw += batch_raw
        done += len(queries)
        progress.update(
            requests_used=done,
            raw_results=raw,
            unique_domains=len(dedupe_by_domain(rows)),
            estimated_search_cost_rub=round(done * price, 2),
        )
        checkpoint.append(
            "batch_finish",
            run_id=run_id,
            batch=batch_index,
            requests_completed=done,
            batch_raw_results=batch_raw,
            batch_query_errors=batch_errors,
            results=_compact(batch_rows),
        )
        _log("batch_progress", run_id=run_id, requests_completed=done, raw_results=raw)
        await _safe_state(progress, f"SEARCH_BATCH_{batch_index}")

    rows = sorted(dedupe_by_domain(rows), key=lambda x: x.get("supplier_score", 0), reverse=True)

    enrich_limit = min(int(config.get("max_enrichment_pages_per_run", 200)), len(rows))
    enrich_chunk = max(5, min(40, int(config.get("enrichment_chunk_size", 30))))
    enriched_done = 0
    for offset in range(0, enrich_limit, enrich_chunk):
        if remaining() <= reserve_seconds:
            stopped = True
            break
        chunk = rows[offset : min(enrich_limit, offset + enrich_chunk)]
        changed = await enrich_candidates(
            chunk,
            limit=len(chunk),
            concurrency=int(config.get("enrichment_concurrency", 6)),
        )
        rows = _merge_changed(rows, changed)
        enriched_done += len(chunk)
        for row in rows:
            _score(row, config)
        rows.sort(key=lambda x: x.get("supplier_score", 0), reverse=True)
        checkpoint.append(
            "enrich_progress",
            run_id=run_id,
            enriched=enriched_done,
            strong_70_plus=sum(int(x.get("supplier_score") or 0) >= 70 for x in rows),
            geo_verified=sum(bool(x.get("geo_verified")) for x in rows),
        )
        progress.update(unique_domains=len(rows))
        await _safe_state(progress, "ENRICH")

    for row in rows:
        _score(row, config)
    rows.sort(key=lambda x: x.get("supplier_score", 0), reverse=True)

    deep_min_score = int(config.get("deep_scan_min_score", 40))
    max_deep = min(int(config.get("max_deep_suppliers_per_run", 150)), len(rows))
    deep_chunk = max(2, min(10, int(config.get("deep_scan_chunk_size", 6))))
    deep_candidates = [row for row in rows if int(row.get("supplier_score") or 0) >= deep_min_score][:max_deep]
    deep_done = 0
    for offset in range(0, len(deep_candidates), deep_chunk):
        if remaining() <= reserve_seconds:
            stopped = True
            break
        if elapsed() >= target_active and deep_done >= min(20, len(deep_candidates)):
            break
        chunk = deep_candidates[offset : offset + deep_chunk]
        changed = await deep_scan_top(
            chunk,
            top_n=len(chunk),
            max_pages=int(config.get("deep_pages_per_supplier", 6)),
            concurrency=int(config.get("deep_scan_concurrency", 4)),
        )
        rows = _merge_changed(rows, changed)
        deep_done += len(chunk)
        for row in rows:
            _score(row, config)
        rows.sort(key=lambda x: x.get("supplier_score", 0), reverse=True)
        checkpoint.append(
            "deep_scan_progress",
            run_id=run_id,
            deep_scanned=deep_done,
            elapsed_seconds=round(elapsed(), 2),
            strong_70_plus=sum(int(x.get("supplier_score") or 0) >= 70 for x in rows),
            geo_verified=sum(bool(x.get("geo_verified")) for x in rows),
        )
        await _safe_state(progress, "DEEP_SCAN")

    for row in rows:
        _score(row, config)
    rows.sort(key=lambda x: x.get("supplier_score", 0), reverse=True)

    history, history_stats = merge_history(rows, old_history, run_id)
    try:
        save_history(history_path, history)
    except OSError as exc:
        _log("local_history_write_error", error=type(exc).__name__)

    strong = [row for row in rows if int(row.get("supplier_score") or 0) >= 70]
    manufacturers = [
        row for row in rows if row.get("company_type") in ("manufacturer", "contract_manufacturer")
    ]
    geo_verified = [row for row in rows if row.get("geo_verified")]
    sku_total = sum(int(row.get("sku_count") or 0) for row in rows)
    finished_at = datetime.now(MSK)
    status = "PARTIAL" if stopped or errors else "OK"
    summary = {
        "run_id": run_id,
        "plan_slot": slot,
        "status": status,
        "started_at_msk": started_at.isoformat(timespec="seconds"),
        "finished_at_msk": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": round(elapsed(), 2),
        "target_active_seconds": target_active,
        "planned_queries": len(plan),
        "adaptive_queries": len(adaptive),
        "requests_used": done,
        "query_errors": errors,
        "raw_results": raw,
        "unique_domains": len(rows),
        "strong_70_plus": len(strong),
        "geo_verified": len(geo_verified),
        "manufacturers": len(manufacturers),
        "with_direct_contacts": sum(bool(row.get("has_direct_contact")) for row in rows),
        "with_price": sum(bool(row.get("has_price")) for row in rows),
        "enriched_suppliers": enriched_done,
        "deep_scanned_suppliers": deep_done,
        "sku_candidates_found": sku_total,
        "estimated_search_cost_rub": round(done * price, 2),
        "checkpoint": str(checkpoint.path),
        **history_stats,
        "top": [
            {
                "title": row.get("title"),
                "url": row.get("url"),
                "score": row.get("supplier_score"),
                "type": row.get("company_type"),
                "region": row.get("region"),
                "category": row.get("category"),
                "geo_verified": bool(row.get("geo_verified")),
                "geo_evidence": (row.get("geo_evidence") or [])[:2],
                "phones": (row.get("phones") or [])[:2],
                "emails": (row.get("emails") or [])[:2],
                "price": bool(row.get("has_price")),
                "sku_count": row.get("sku_count", 0),
                "sku_examples": [sku.get("name") for sku in (row.get("sku_candidates") or [])[:5]],
            }
            for row in rows[:20]
        ],
    }

    try:
        sheets = await asyncio.to_thread(append_results, rows, summary)
        summary["sheets"] = sheets
        summary["new_candidates"] = int(sheets.get("candidates_appended") or 0)
    except Exception as exc:
        summary["sheets"] = {"enabled": True, "candidates_appended": 0, "error": type(exc).__name__}

    checkpoint.append("run_finish", **summary)
    await _safe_state(summary, "FINISH", "" if status == "OK" else "partial_or_query_errors")
    _log("run_finish", **summary)
    return {"summary": summary, "results": rows[:200]}


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
