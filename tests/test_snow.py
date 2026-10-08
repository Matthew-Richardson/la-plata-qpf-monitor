from datetime import datetime
from zoneinfo import ZoneInfo
from shapely.geometry import box
from snow_monitor import analyze
from groupme_notify import decide, message

def synthetic(q=0.1, snow=False):
    p={x:{"average_in":q,"valid_period":["2026-10-08 12:00:00","2026-10-09 12:00:00"]} for x in ("Day 1","Day 2","Day 3","Days 4-5","Days 6-7")}
    p["Days 1-7"]={**p["Day 1"],"min_in":0,"max_in":q}
    return {"periods":p,"huc10_watersheds":{h:{"periods":{"Days 1-7":{"average_in":q}}} for h in ("1408010112","1408010111","1408010407","1408010403")},
            "snow":{"active":snow,"periods":{"Day 1":[{"threshold_in":4,"minimum_probability_pct":40}] if snow else [],"Day 2":[],"Day 3":[]}}}

def test_no_snow():
    result=analyze(box(-108,37,-107,38),query=lambda *a,**k:[])
    assert result["active"] is False

def test_snow_intersection_and_probability():
    feature={"geometry":box(-107.5,37.2,-107.4,37.3).__geo_interface__,"properties":{"outlook":"Moderate (40-69%)"}}
    result=analyze(box(-108,37,-107,38),query=lambda url,params: [feature] if url.endswith("/1") else [])
    assert result["active"] is True
    assert result["periods"]["Day 1"][0]["minimum_probability_pct"]==40

def test_snow_starts_wet_alert_with_dry_qpf():
    reason,state=decide(synthetic(snow=True),{},now=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver")))
    assert reason=="Wet pattern detected"
    assert state["status"]=="wet"

def test_snow_change_triggers():
    now=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver"))
    _,state=decide(synthetic(snow=True),{},now=now)
    updated=synthetic(snow=True)
    updated["snow"]["periods"]["Day 1"][0]["minimum_probability_pct"]=70
    assert decide(updated,state,now=now)[0]=="Snowfall probability guidance changed"

def test_snow_message():
    msg=message(synthetic(snow=True),"New wet pattern")
    assert "≥4in snow at ≥40% chance" in msg
    assert "\n" in msg
