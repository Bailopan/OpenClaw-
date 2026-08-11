from __future__ import annotations

PRODUCTION = ("производител", "собственное производство", "производственная компания", "фабрик", "завод")
PRIVATE_LABEL = ("контрактное производство", "стм", "private label", "oem", "под вашей торговой маркой", "под вашим брендом")
WHOLESALE = ("опт", "оптов", "дистрибьютор", "дилер", "b2b", "оптовые продажи")
COMMERCIAL = ("прайс", "прайс-лист", "минимальная партия", "минимальный заказ", "мелкий опт", "крупный опт", "юрлиц")
WAREHOUSE = ("склад", "отгрузка со склада", "самовывоз")
LOCAL = ("пушкино", "ивантеев", "королев", "королёв", "мытищ", "щелков", "щёлков", "софрино", "правдинск", "мамонтов", "фрязино", "красноармейск")
RETAIL = ("розничный магазин", "доставка от 1 штуки", "интернет-магазин", "купить в розницу")
AGGREGATOR = ("маркетплейс", "avito", "озон", "wildberries", "каталог компаний", "справочник организаций")
FASHION = ("одежда", "обувь", "сумки", "бижутерия", "ювелир", "ремни")


def _has(text: str, markers: tuple[str, ...]) -> bool:
    return any(x in text for x in markers)


def classify_supplier(text: str) -> dict:
    value = (text or "").lower()
    production = _has(value, PRODUCTION)
    private_label = _has(value, PRIVATE_LABEL)
    wholesale = _has(value, WHOLESALE)
    commercial = _has(value, COMMERCIAL)
    warehouse = _has(value, WAREHOUSE)
    local = _has(value, LOCAL)
    retail = _has(value, RETAIL)
    aggregator = _has(value, AGGREGATOR)
    fashion = _has(value, FASHION)

    score = 0
    if production: score += 25
    if private_label: score += 20
    if wholesale: score += 15
    if commercial: score += 10
    if warehouse: score += 5
    if local: score += 10
    if retail: score -= 30
    if aggregator: score -= 40
    if fashion: score -= 20

    if private_label:
        company_type = "contract_manufacturer"
    elif production:
        company_type = "manufacturer"
    elif wholesale:
        company_type = "wholesaler_distributor"
    elif retail:
        company_type = "retail"
    elif aggregator:
        company_type = "aggregator"
    else:
        company_type = "unknown"

    return {
        "score": max(0, min(100, score)),
        "company_type": company_type,
        "is_manufacturer": production,
        "private_label": private_label,
        "wholesale": wholesale,
        "commercial_terms": commercial,
        "warehouse": warehouse,
        "local": local,
        "retail": retail,
        "aggregator": aggregator,
        "fashion": fashion,
    }


def supplier_score(text: str) -> int:
    return int(classify_supplier(text)["score"])


def is_supplier_candidate(text: str, threshold: int = 25) -> bool:
    return supplier_score(text) >= threshold
