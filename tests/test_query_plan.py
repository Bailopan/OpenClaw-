from supplier_radar.query_plan import build_query_plan


def test_plan_uses_multiple_branches_and_limit():
    cfg = {
        "regions": ["Пушкино", "Мытищи"],
        "categories": ["хозтовары", "упаковка"],
        "address_clusters": ["Кудринское шоссе 2 Пушкино"],
        "seed_entities": ["Империя Пластик"],
    }
    plan = build_query_plan(cfg, slot=42, limit=20)
    assert len(plan) == 20
    assert len({x.query for x in plan}) == 20
    assert "A_direct" in {x.branch for x in plan}
    assert "C_address_cluster" in {x.branch for x in plan}
