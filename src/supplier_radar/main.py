from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timezone, timedelta
import json
import os
from pathlib import Path
import time
import uuid

from .checkpoint import CheckpointWriter
from .classify import classify_supplier, supplier_score
from .dedupe import dedupe_by_domain
from .enrich import enrich_candidates
from .google_sheets import append_results
from .history import load_history, merge_history, save_history
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
            if isinstance(extra, list): cfg["seed_entities"] = [str(x).strip() for x in extra if str(x).strip()]
        except json.JSONDecodeError: _log("config_warning", reason="invalid_SUPPLIER_SEEDS_JSON")
    return cfg

def _cost_cap(cfg: dict) -> int:
    request_cap = int(cfg.get("max_search_requests_per_run", 100)); price = float(cfg.get("deferred_search_price_rub_per_request", 0.0305))
    budget = min(float(cfg.get("max_search_cost_rub_per_run", 10.0)), float(cfg.get("max_search_cost_rub_per_day", 100.0)) / max(1, int(cfg.get("expected_runs_per_day", 24))))
    return request_cap if price <= 0 else min(request_cap, int(budget // price))

def _checkpoint_path(run_id: str) -> Path:
    return Path(os.getenv("SUPPLIER_RADAR_CHECKPOINT_DIR", "outputs/checkpoints")) / f"{run_id}.jsonl"

def _compact_rows(rows: list[dict]) -> list[dict]:
    return [{"title":x.get("title"),"url":x.get("url"),"snippet":str(x.get("snippet") or "")[:700],"supplier_score":int(x.get("supplier_score") or 0),"company_type":x.get("company_type"),"query":x.get("query"),"branch":x.get("branch"),"region":x.get("region"),"category":x.get("category")} for x in sorted(dedupe_by_domain(rows), key=lambda x:int(x.get("supplier_score") or 0), reverse=True)]

def _rows_from_responses(responses, meta_by_query: dict) -> tuple[list[dict], int]:
    rows=[]; errors=0
    for response in responses:
        meta=meta_by_query.get(response.query); errors += 1 if response.error else 0
        for item in response.rows:
            text=f"{item.get('title','')} {item.get('snippet','')}"; cls=classify_supplier(text)
            item.update(cls); item["supplier_score"]=cls["score"]; item["query"]=response.query
            item["branch"]=meta.branch if meta else ""; item["region"]=meta.region if meta else ""; item["category"]=meta.category if meta else ""; rows.append(item)
    return rows, errors

def _final_score(item: dict) -> int:
    text=f"{item.get('title','')} {item.get('snippet','')} {item.get('page_text','')}"; cls=classify_supplier(text); score=int(cls["score"])
    if item.get("has_price"): score += 10
    if item.get("has_commercial_page"): score += 5
    if item.get("has_direct_contact"): score += 5
    if item.get("inns"): score += 5
    item.update(cls); item["supplier_score"]=min(100,score)
    return item["supplier_score"]

async def run() -> dict:
    cfg=_load_config(); run_id=f"SR-{datetime.now(MSK):%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"; started=time.monotonic(); started_at_msk=datetime.now(MSK)
    slot=int(os.getenv("SUPPLIER_PLAN_SLOT", int(datetime.now(UTC).timestamp()//3600))); plan=build_query_plan(cfg,slot=slot,limit=_cost_cap(cfg)); price=float(cfg.get("deferred_search_price_rub_per_request",0.0305)); batch_size=max(1,min(100,int(cfg.get("checkpoint_batch_size",100)))); soft_limit=max(0,int(cfg.get("soft_runtime_limit_seconds",0))); checkpoint=CheckpointWriter(_checkpoint_path(run_id))
    checkpoint.append("run_start",run_id=run_id,planned_queries=len(plan),batch_size=batch_size,estimated_max_search_cost_rub=round(len(plan)*price,2),plan_slot=slot); _log("run_start",run_id=run_id,planned_queries=len(plan),batch_size=batch_size,checkpoint=str(checkpoint.path),mode="deferred")
    client=DeferredSearchClient(docs_on_page=int(cfg.get("results_per_query",20))); meta_by_query={x.query:x for x in plan}; rows=[]; errors=raw_results=completed_queries=0; stopped_early=False; total_batches=(len(plan)+batch_size-1)//batch_size if plan else 0
    for batch_index,offset in enumerate(range(0,len(plan),batch_size),start=1):
        if soft_limit and time.monotonic()-started>=soft_limit: stopped_early=True; checkpoint.append("soft_stop",run_id=run_id,completed_queries=completed_queries,elapsed_seconds=round(time.monotonic()-started,2)); break
        batch_plan=plan[offset:offset+batch_size]; batch_queries=[x.query for x in batch_plan]; checkpoint.append("batch_start",run_id=run_id,batch=batch_index,batches_total=total_batches,offset=offset,queries=len(batch_queries),estimated_batch_cost_rub=round(len(batch_queries)*price,2))
        try: responses=await asyncio.to_thread(client.search_many,batch_queries,workers=int(cfg.get("search_wait_workers",8)),batch_size=batch_size)
        except BaseException as exc: checkpoint.append("batch_abort",run_id=run_id,batch=batch_index,completed_queries=completed_queries,error=type(exc).__name__); raise
        batch_rows,batch_errors=_rows_from_responses(responses,meta_by_query); batch_raw_results=sum(len(r.rows) for r in responses); rows.extend(batch_rows); errors+=batch_errors; raw_results+=batch_raw_results; completed_queries+=len(batch_queries); compact=_compact_rows(batch_rows)
        checkpoint.append("batch_finish",run_id=run_id,batch=batch_index,batches_total=total_batches,requests_completed=completed_queries,batch_query_errors=batch_errors,batch_raw_results=batch_raw_results,batch_unique_domains=len(compact),batch_candidates_24_plus=sum(1 for r in compact if int(r.get("supplier_score") or 0)>=24),estimated_cost_rub=round(completed_queries*price,2),results=compact)
        _log("batch_progress",run_id=run_id,batch=batch_index,batches_total=total_batches,requests_completed=completed_queries,planned_queries=len(plan),raw_results=raw_results,query_errors=errors,estimated_cost_rub=round(completed_queries*price,2))
    rows=sorted(dedupe_by_domain(rows),key=lambda x:x.get("supplier_score",0),reverse=True); checkpoint.append("search_finish",run_id=run_id,stopped_early=stopped_early,requests_completed=completed_queries,raw_results=raw_results,unique_domains=len(rows),query_errors=errors,estimated_search_cost_rub=round(completed_queries*price,2),results=_compact_rows(rows))
    rows=await enrich_candidates(rows,limit=int(cfg.get("max_enrichment_pages_per_run",40)),concurrency=int(cfg.get("enrichment_concurrency",8)))
    for item in rows: _final_score(item)
    rows.sort(key=lambda x:x.get("supplier_score",0),reverse=True)
    history_path=Path(os.getenv("SUPPLIER_RADAR_HISTORY","outputs/history/suppliers.json")); history=load_history(history_path); history,history_stats=merge_history(rows,history,run_id); save_history(history_path,history)
    finished_at=datetime.now(MSK); strong=[r for r in rows if int(r.get("supplier_score") or 0)>=70]; manufacturers=[r for r in rows if r.get("company_type") in ("manufacturer","contract_manufacturer")]
    summary={"run_id":run_id,"status":"PARTIAL" if stopped_early or errors else "OK","started_at_msk":started_at_msk.isoformat(timespec="seconds"),"finished_at_msk":finished_at.isoformat(timespec="seconds"),"duration_seconds":round(time.monotonic()-started,2),"planned_queries":len(plan),"requests_used":completed_queries,"query_errors":errors,"raw_results":raw_results,"unique_domains":len(rows),"candidates_25_plus":sum(1 for r in rows if int(r.get("supplier_score") or 0)>=25),"strong_70_plus":len(strong),"manufacturers":len(manufacturers),"with_direct_contacts":sum(1 for r in rows if r.get("has_direct_contact")),"with_price":sum(1 for r in rows if r.get("has_price")),"estimated_search_cost_rub":round(completed_queries*price,2),"checkpoint":str(checkpoint.path),**history_stats,"top":[{"title":r.get("title"),"url":r.get("url"),"score":r.get("supplier_score"),"type":r.get("company_type"),"region":r.get("region"),"category":r.get("category"),"phones":r.get("phones",[])[:2],"emails":r.get("emails",[])[:2],"price":bool(r.get("has_price"))} for r in rows[:20]]}
    try: summary["sheets"]=await asyncio.to_thread(append_results,rows,summary)
    except Exception as exc: summary["sheets"]={"enabled":True,"appended":0,"error":type(exc).__name__}; _log("sheets_error",run_id=run_id,error=type(exc).__name__)
    checkpoint.append("run_finish",**summary); _log("run_finish",**summary); return {"summary":summary,"results":rows[:200]}

def main()->None: print(json.dumps(asyncio.run(run()),ensure_ascii=False,indent=2))
if __name__=="__main__": main()
