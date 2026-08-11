from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .classify import supplier_score
from .dedupe import dedupe_by_domain
from .yandex_search import search

CONFIG = Path("config/pushkino.json")


async def run() -> list[dict]:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    rows: list[dict] = []
    budget = int(cfg.get("max_search_requests_per_run", 100))
    requests_used = 0

    for region in cfg["regions"]:
        for category in cfg["categories"]:
            if requests_used >= budget:
                break
            query = f'{category} оптом производитель склад {region}'
            found = await search(query, results=20)
            requests_used += 1
            for item in found:
                text = f"{item.get('title', '')} {item.get('snippet', '')}"
                item["supplier_score"] = supplier_score(text)
                item["query"] = query
                item["region"] = region
                item["category"] = category
                rows.append(item)

    return sorted(dedupe_by_domain(rows), key=lambda x: x.get("supplier_score", 0), reverse=True)


def main() -> None:
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
