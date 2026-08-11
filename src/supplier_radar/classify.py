SUPPLIER_MARKERS = (
    "опт", "оптов", "производител", "дистрибьютор", "дилер",
    "склад", "прайс", "минимальная партия", "юрлиц", "b2b",
)
RETAIL_ONLY_MARKERS = ("розничный магазин", "доставка от 1 штуки", "маркетплейс")


def supplier_score(text: str) -> int:
    value = text.lower()
    score = sum(12 for marker in SUPPLIER_MARKERS if marker in value)
    score -= sum(15 for marker in RETAIL_ONLY_MARKERS if marker in value)
    return max(0, min(100, score))


def is_supplier_candidate(text: str, threshold: int = 24) -> bool:
    return supplier_score(text) >= threshold
