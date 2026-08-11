from urllib.parse import urlparse


def canonical_domain(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    return host.removeprefix("www.")


def dedupe_by_domain(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for row in rows:
        domain = canonical_domain(str(row.get("url") or ""))
        key = domain or str(row.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result
