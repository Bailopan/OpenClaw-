from supplier_radar.main import _cost_cap


def test_cost_cap_respects_ruble_budget():
    cfg = {
        "max_search_requests_per_run": 100,
        "deferred_search_price_rub_per_request": 0.0305,
        "max_search_cost_rub_per_run": 2.0,
    }
    assert _cost_cap(cfg) == 65
