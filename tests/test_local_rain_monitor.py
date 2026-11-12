from datetime import datetime, timezone

from local_rain_monitor import _allocated, _rain_on_snow, brief, interval


def test_interval_and_allocation():
    start,end=interval("2026-11-12T00:00:00+00:00/PT6H")
    assert (end-start).total_seconds()==21600
    values=[{"validTime":"2026-11-12T00:00:00+00:00/PT6H","value":6.0}]
    result=_allocated(values,start,end)
    assert sum(result.values())==6.0


def test_consecutive_day_rain_on_snow():
    daily={
        "snow":{"2026-11-14":25.4},
        "rain":{"2026-11-15":6.35},
    }
    assert _rain_on_snow(daily)==[("2026-11-14","2026-11-15",0.25)]


def test_brief_labels_rain_only_and_dates():
    result={
        "start":"2026-11-12T01:00:00+00:00",
        "end_3day":"2026-11-15T01:00:00+00:00",
        "end_7day":"2026-11-19T01:00:00+00:00",
        "issued":"2026-11-12T01:22:09+00:00",
        "drainages":{
            "Junction":{"rain_3day_in":0.21,"rain_7day_in":0.39,"rain_on_snow":[]},
            "Hermosa":{"rain_3day_in":0.32,"rain_7day_in":0.61,"rain_on_snow":[]},
            "Stevens":{"rain_3day_in":0.26,"rain_7day_in":0.48,"rain_on_snow":[]},
        },
    }
    text=brief(result)
    assert "3-day (Wed 11/11–Sat 11/14)" in text
    assert "7-day (Wed 11/11–Wed 11/18)" in text
    assert "Rain only; modeled snow excluded." in text
    assert "Rain-on-snow: no consecutive-day" in text
