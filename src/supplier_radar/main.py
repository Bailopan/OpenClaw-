from __future__ import annotations
import asyncio
from datetime import UTC,datetime,timezone,timedelta
import json,os,time,uuid
from pathlib import Path
from .checkpoint import CheckpointWriter
from .classify import classify_supplier
from .dedupe import dedupe_by_domain
from .enrich import enrich_candidates
from .deep_scan import deep_scan_top
from .adaptive import adaptive_queries
from .google_sheets import append_results
from .history import load_history,merge_history,save_history
from .query_plan import build_query_plan,PlannedQuery
from .yandex_search import DeferredSearchClient
CONFIG=Path(os.getenv("SUPPLIER_RADAR_CONFIG","config/pushkino.json")); MSK=timezone(timedelta(hours=3))
def _log(event:str,**fields): print(json.dumps({"event":event,**fields},ensure_ascii=False),flush=True)
def _load_config(): return json.loads(CONFIG.read_text(encoding="utf-8"))
def _cost_cap(c):
 p=float(c.get("deferred_search_price_rub_per_request",.0305)); b=min(float(c.get("max_search_cost_rub_per_run",10)),float(c.get("max_search_cost_rub_per_day",100))/max(1,int(c.get("expected_runs_per_day",24)))); return min(int(c.get("max_search_requests_per_run",100)),int(b//p)) if p>0 else int(c.get("max_search_requests_per_run",100))
def _checkpoint_path(r): return Path(os.getenv("SUPPLIER_RADAR_CHECKPOINT_DIR","outputs/checkpoints"))/f"{r}.jsonl"
def _compact(rows): return [{"title":x.get("title"),"url":x.get("url"),"supplier_score":int(x.get("supplier_score") or 0),"company_type":x.get("company_type"),"region":x.get("region"),"category":x.get("category")} for x in sorted(dedupe_by_domain(rows),key=lambda x:int(x.get("supplier_score") or 0),reverse=True)]
def _rows(responses,meta):
 out=[]; errors=0
 for r in responses:
  errors+=bool(r.error); m=meta.get(r.query)
  for x in r.rows:
   cls=classify_supplier(f"{x.get('title','')} {x.get('snippet','')}"); x.update(cls); x["supplier_score"]=cls["score"]; x["query"]=r.query; x["branch"]=m.branch if m else ""; x["region"]=m.region if m else ""; x["category"]=m.category if m else ""; out.append(x)
 return out,int(errors)
def _score(x):
 cls=classify_supplier(f"{x.get('title','')} {x.get('snippet','')} {x.get('page_text','')}"); s=int(cls["score"])+10*bool(x.get("has_price"))+5*bool(x.get("has_commercial_page"))+5*bool(x.get("has_direct_contact"))+5*bool(x.get("inns"))+min(10,int(x.get("sku_count") or 0)//3); x.update(cls); x["supplier_score"]=min(100,s); return x["supplier_score"]
async def run():
 cfg=_load_config(); run_id=f"SR-{datetime.now(MSK):%Y%m%d-%H%M}-{uuid.uuid4().hex[:6]}"; started=time.monotonic(); started_at=datetime.now(MSK); slot=int(os.getenv("SUPPLIER_PLAN_SLOT",int(datetime.now(UTC).timestamp()//3600))); cap=_cost_cap(cfg); history_path=Path(os.getenv("SUPPLIER_RADAR_HISTORY","outputs/history/suppliers.json")); old_history=load_history(history_path)
 base=build_query_plan(cfg,slot=slot,limit=cap); adaptive=adaptive_queries(old_history,list(cfg.get("regions") or []),limit=min(15,cap//3)); seen={x.query for x in base}; plan=[PlannedQuery(**x) for x in adaptive if x["query"] not in seen]+base; plan=plan[:cap]
 price=float(cfg.get("deferred_search_price_rub_per_request",.0305)); batch=max(1,min(100,int(cfg.get("checkpoint_batch_size",100)))); soft=int(cfg.get("soft_runtime_limit_seconds",0)); cp=CheckpointWriter(_checkpoint_path(run_id)); cp.append("run_start",run_id=run_id,planned_queries=len(plan),adaptive_queries=len(adaptive),estimated_max_search_cost_rub=round(len(plan)*price,2),plan_slot=slot); _log("run_start",run_id=run_id,planned_queries=len(plan),adaptive_queries=len(adaptive))
 client=DeferredSearchClient(docs_on_page=int(cfg.get("results_per_query",20))); meta={x.query:x for x in plan}; rows=[]; errors=raw=done=0; stopped=False
 for bi,off in enumerate(range(0,len(plan),batch),1):
  if soft and time.monotonic()-started>=soft: stopped=True; break
  qs=[x.query for x in plan[off:off+batch]]; responses=await asyncio.to_thread(client.search_many,qs,workers=int(cfg.get("search_wait_workers",8)),batch_size=batch); br,be=_rows(responses,meta); rows+=br; errors+=be; raw+=sum(len(r.rows) for r in responses); done+=len(qs); cp.append("batch_finish",run_id=run_id,batch=bi,requests_completed=done,batch_raw_results=sum(len(r.rows) for r in responses),batch_query_errors=be,results=_compact(br)); _log("batch_progress",run_id=run_id,requests_completed=done,raw_results=raw)
 rows=sorted(dedupe_by_domain(rows),key=lambda x:x.get("supplier_score",0),reverse=True); rows=await enrich_candidates(rows,limit=int(cfg.get("max_enrichment_pages_per_run",200)),concurrency=int(cfg.get("enrichment_concurrency",6)))
 for x in rows:_score(x)
 rows.sort(key=lambda x:x.get("supplier_score",0),reverse=True); deep_n=min(30,len(rows)); rows=await deep_scan_top(rows,top_n=deep_n,max_pages=int(cfg.get("deep_pages_per_supplier",6)),concurrency=int(cfg.get("deep_scan_concurrency",4)))
 for x in rows:_score(x)
 rows.sort(key=lambda x:x.get("supplier_score",0),reverse=True); history,hs=merge_history(rows,old_history,run_id); save_history(history_path,history); strong=[x for x in rows if int(x.get("supplier_score") or 0)>=70]; manufacturers=[x for x in rows if x.get("company_type") in ("manufacturer","contract_manufacturer")]; sku_total=sum(int(x.get("sku_count") or 0) for x in rows)
 summary={"run_id":run_id,"status":"PARTIAL" if stopped or errors else "OK","started_at_msk":started_at.isoformat(timespec="seconds"),"finished_at_msk":datetime.now(MSK).isoformat(timespec="seconds"),"duration_seconds":round(time.monotonic()-started,2),"planned_queries":len(plan),"adaptive_queries":len(adaptive),"requests_used":done,"query_errors":errors,"raw_results":raw,"unique_domains":len(rows),"strong_70_plus":len(strong),"manufacturers":len(manufacturers),"with_direct_contacts":sum(bool(x.get("has_direct_contact")) for x in rows),"with_price":sum(bool(x.get("has_price")) for x in rows),"deep_scanned_suppliers":deep_n,"sku_candidates_found":sku_total,"estimated_search_cost_rub":round(done*price,2),"checkpoint":str(cp.path),**hs,"top":[{"title":x.get("title"),"url":x.get("url"),"score":x.get("supplier_score"),"type":x.get("company_type"),"region":x.get("region"),"category":x.get("category"),"phones":x.get("phones",[])[:2],"emails":x.get("emails",[])[:2],"price":bool(x.get("has_price")),"sku_count":x.get("sku_count",0),"sku_examples":[s.get("name") for s in x.get("sku_candidates",[])[:5]]} for x in rows[:20]]}
 try: summary["sheets"]=await asyncio.to_thread(append_results,rows,summary)
 except Exception as e: summary["sheets"]={"enabled":True,"appended":0,"error":type(e).__name__}
 cp.append("run_finish",**summary); _log("run_finish",**summary); return {"summary":summary,"results":rows[:200]}
def main(): print(json.dumps(asyncio.run(run()),ensure_ascii=False,indent=2))
if __name__=="__main__": main()
