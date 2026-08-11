from supplier_radar.yandex_search import parse_search_xml


def test_parse_search_xml():
    xml = """<yandexsearch><response><results><grouping><group><doc><url>https://example.ru</url><title>Оптовый склад</title><passages><passage>Прайс для юрлиц</passage></passages></doc></group></grouping></results></response></yandexsearch>"""
    rows = parse_search_xml(xml)
    assert rows[0]["url"] == "https://example.ru"
    assert "Прайс" in rows[0]["snippet"]
