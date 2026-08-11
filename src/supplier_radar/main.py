from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timezone, timedelta
import json
import os
from pathlib import Path
import time
import uuid

from .classify import supplier_score
from .dedupe import dedupe_by_domain
from .enrich import enrich_candidates
from .google_sheets import append_results
from .query_plan import build_query_plan
from .yandex_search import DeferredSearchClient

CONFIG = Path(os.getenv("SUPPLIER_RADAR_CONFIG", "config/pushkino.json"))
MSK = timezone(timedelta(hours=3))


def _log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def _load_config() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    raw_seeds = os.getenv("SUPPLIER_SEEDS_JSON", "").strip()
    if raw_seeds:
        try:
            extra = json.loads(raw_seeds)
            if isinstance(extra, list):
                cfg["seed_entities"] = [str(x).strip() for x in extra if str(x).strip()]
        except json.JSONDecodeError:
            _log("config_warning", reason="invalid_SUPPLIER_SEEDS_JSON")
    return cfg


def _cost_cap(cfg: dict) -> int:
    request_cap = int(cfg.get("max_search_requests_per_run", 100))
    price = float(cfg.get("deferred_search_price_rub_per_request", 0.0305))
    budget = float(cfg.get("max_search_cost_rub_per_run", 10.0))
    daily_budget = float(cfg.get("max_search_cost_rub_per_day", 100.0))
    runs_per_day = max(1, int(cfg.get("expected_runs_per_day", 24)))
    budget = min(budget, daily_budget / runs_per_day)
    if price <= 0:
        return request_cap
    return min(request_cap, int(budget // price))


async def run() -> dict:
    cfg = _load_config()
    run_id = f"SR-{datetime.now(MSK):%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"
    started = time.monotonic()
    slot = int(datetime.now(UTC).timestamp() // 3600)
    max_queries = _cost_cap(cfg)
    plan = build_query_plan(cfg, slot=slot, limit=max_queries)
    price = float(cfg.get("deferred_search_price_rub_per_request", 0.0305))

    _log("run_start", run_id=run_id, planned_queries=len(plan), mode="deferred")
    client = DeferredSearchClient(docs_on_page=int(cfg.get("results_per_query", 20)))
    responses = await asyncio.to_thread(
        client.search_many,
        [item.query for item in plan],
        workers=int(cfg.get("search_wait_workers", 8)),
    )
    meta_by_query = {item.query: item for item in plan}
    rows: list[dict] = []
    errors = 0
    for response in responses:
        meta = meta_by_query.get(response.query)
        if response.error:
            errors += 1
            _log("query_error", run_id=run_id, query=response.query[:180], error=response.error)
        for item in response.rows:
            text = f"{item.get('title', '')} {item.get('snippet', '')}"
            item["supplier_score"] = supplier_score(text)
            item["query"] = response.query
            item["branch"] = meta.branch if meta else ""
            item["region"] = meta.region if meta else ""
            item["category"] = meta.category if meta else ""
            rows.append(item)

    rows = sorted(dedupe_by_domain(rows), key=lambda x: x.get("supplier_score", 0), reverse=True)
    enrichment_limit = int(cfg.get("max_enrichment_pages_per_run", 40))
    rows = await enrich_candidates(
        rows,
        limit=enrichment_limit,
        concurrency=int(cfg.get("enrichment_concurrency", 8)),
    )
    for item in rows:
        page_text = str(item.get("page_text") or "")
        if page_text:
            item["supplier_score"] = max(
                int(item.get("supplier_score") or 0),
                supplier_score(f"{item.get('title','')} {item.get('snippet','')} {page_text}"),
            )
    rows.sort(key=lambda x: x.get("supplier_score", 0), reverse=True)

    finished_at = datetime.now(MSK)
    summary = {
        "run_id": run_id,
        "status": "OK" if errors < len(plan) else "PARTIAL",
        "started_at_msk": datetime.fromtimestamp(time.time() - (time.monotonic() - started), MSK).isoformat(timespec="seconds"),
        "finished_at_msk": finished_at.isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 2),
        "requests_used": len(plan),
        "query_errors": errors,
        "raw_results": sum(len(r.rows) for r in responses),
        "unique_domains": len(rows),
        "candidates_24_plus": sum(1 for r in rows if int(r.get("supplier_score") or 0) >= 24),
        "estimated_search_cost_rub": round(len(plan) * price, 2),
        "top": [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "score": r.get("supplier_score"),
                "branch": r.get("branch"),
                "region": r.get("region"),
            }
            for r in rows[:20]
        ],
    }
    try:
        summary["sheets"] = await asyncio.to_thread(append_results, rows, summary)
    except Exception as exc:
        summary["sheets"] = {"enabled": True, "appended": 0, "error": type(exc).__name__}
        _log("sheets_error", run_id=run_id, error=type(exc).__name__)

    _log("run_finish", **summary)
    return {"summary": summary, "results": rows[:200]}


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
