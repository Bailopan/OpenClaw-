from supplier_radar.classify import is_supplier_candidate, supplier_score
from supplier_radar.dedupe import dedupe_by_domain


def test_dedupe_domain():
    rows = [{"url": "https://www.example.ru/a"}, {"url": "https://example.ru/b"}]
    assert len(dedupe_by_domain(rows)) == 1


def test_supplier_classification():
    text = "Производитель. Оптовые поставки со склада. Скачать прайс."
    assert supplier_score(text) >= 24
    assert is_supplier_candidate(text)
