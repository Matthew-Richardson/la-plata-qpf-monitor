from groupme_notify import material_changes, format_message

def make_report(q=2.0):
    return {
        "periods": {
            "Day 1":{"average_in":0.1},
            "Day 2":{"average_in":0.2},
            "Day 3":{"average_in":0.3},
            "Days 4-5":{"average_in":0.4},
            "Days 6-7":{"average_in":1.0},
            "Days 1-7":{"average_in":q,"min_in":0.5,"max_in":4.0}
        },
        "huc10_watersheds": {
            code: {"periods":{"Days 1-7":{"average_in":q}}}
            for code in ("1408010112","1408010111","1408010407","1408010403")
        }
    }

def test_first_report():
    assert material_changes(None,make_report())

def test_identical_forecast_not_sent():
    assert material_changes(make_report(),make_report()) == []

def test_material_change_detected():
    assert any("county" in x for x in material_changes(make_report(), make_report(2.5)))

def test_no_secret_in_message():
    msg=format_message(make_report(),["Initial verified forecast"])
    assert "7-day county avg: 2.00 in" in msg
    assert "Vallecito Creek" in msg
