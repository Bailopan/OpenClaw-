from supplier_radar.geo import detect_geo_evidence


def test_geo_requires_physical_location_signal():
    result = detect_geo_evidence(
        "Наш склад: Московская область, Пушкино, Учинская 16. Самовывоз со склада.",
        ["Пушкино"],
    )
    assert result["geo_verified"] is True
    assert result["geo_score"] == 15


def test_delivery_mention_is_not_physical_location():
    result = detect_geo_evidence("Доставка по Пушкино и Московской области", ["Пушкино"])
    assert result["geo_verified"] is False
    assert result["geo_score"] == 0
