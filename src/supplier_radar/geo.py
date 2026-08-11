from __future__ import annotations

LOCATION_SIGNALS = (
    "склад",
    "производств",
    "производственная площадка",
    "самовывоз",
    "отгруз",
    "адрес склада",
    "адрес производства",
    "цех",
    "распределительный центр",
    "логистический комплекс",
)


def _norm(value: str) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def detect_geo_evidence(text: str, regions: list[str], address_clusters: list[str] | None = None) -> dict:
    """Conservative geography signal: locality/address must be near a warehouse/production term."""
    raw = _norm(text)
    if not raw:
        return {"geo_verified": False, "geo_score": 0, "geo_evidence": []}

    terms: list[str] = []
    for value in [*(regions or []), *((address_clusters or []))]:
        term = _norm(value)
        if term and term not in terms:
            terms.append(term)

    evidence: list[str] = []
    for term in terms:
        start = 0
        while True:
            pos = raw.find(term, start)
            if pos < 0:
                break
            left = max(0, pos - 180)
            right = min(len(raw), pos + len(term) + 180)
            window = raw[left:right]
            if any(signal in window for signal in LOCATION_SIGNALS):
                evidence.append(window[:420])
                break
            start = pos + len(term)
        if len(evidence) >= 3:
            break

    strong = bool(evidence)
    return {
        "geo_verified": strong,
        "geo_score": 15 if strong else 0,
        "geo_evidence": evidence,
    }
