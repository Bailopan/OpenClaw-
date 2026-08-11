from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse


def _domain(url: str) -> str:
    host = urlparse(str(url or '')).netloc.lower().split('@')[-1].split(':')[0]
    return host[4:] if host.startswith('www.') else host


def load_history(path: str | Path) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return {}
    rows = data.get('suppliers', data) if isinstance(data, dict) else {}
    return rows if isinstance(rows, dict) else {}


def merge_history(rows: list[dict], history: dict[str, dict], run_id: str) -> tuple[dict[str, dict], dict]:
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    new_domains = 0
    updated_domains = 0
    for row in rows:
        domain = _domain(row.get('url', ''))
        if not domain:
            continue
        old = history.get(domain)
        score = int(row.get('supplier_score') or 0)
        if old is None:
            new_domains += 1
            old = {'first_seen': now, 'seen_runs': 0, 'best_score': 0}
        else:
            updated_domains += 1
        history[domain] = {
            **old,
            'domain': domain,
            'title': row.get('title') or old.get('title'),
            'url': row.get('url') or old.get('url'),
            'snippet': str(row.get('snippet') or old.get('snippet') or '')[:1000],
            'region': row.get('region') or old.get('region'),
            'category': row.get('category') or old.get('category'),
            'branch': row.get('branch') or old.get('branch'),
            'best_score': max(int(old.get('best_score') or 0), score),
            'last_score': score,
            'last_seen': now,
            'last_run_id': run_id,
            'seen_runs': int(old.get('seen_runs') or 0) + 1,
        }
    stats = {
        'history_total_domains': len(history),
        'new_domains': new_domains,
        'known_domains_seen': updated_domains,
        'new_score_40_plus': sum(1 for r in rows if _domain(r.get('url', '')) and history.get(_domain(r.get('url', '')), {}).get('first_seen') == now and int(r.get('supplier_score') or 0) >= 40),
        'new_score_70_plus': sum(1 for r in rows if _domain(r.get('url', '')) and history.get(_domain(r.get('url', '')), {}).get('first_seen') == now and int(r.get('supplier_score') or 0) >= 70),
    }
    return history, stats


def save_history(path: str | Path, history: dict[str, dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {'version': 1, 'suppliers': history}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
