from datetime import datetime
from zoneinfo import ZoneInfo
from groupme_notify import decide, message

def report(q=2.0):
    return {"periods": {**{name:{"average_in":0.1} for name in ("Day 1","Day 2","Day 3","Days 4-5","Days 6-7")},
                        "Days 1-7":{"average_in":q,"min_in":0.1,"max_in":3.0}},
            "huc10_watersheds":{code:{"periods":{"Days 1-7":{"average_in":q}}}
            for code in ("1408010112","1408010111","1408010407","1408010403")}}
MORNING=datetime(2026,10,8,7,tzinfo=ZoneInfo("America/Denver"))
EVENING=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver"))

def test_wet_opening():
    reason,s=decide(report(),{},now=MORNING)
    assert "Wet pattern" in reason and s["status"]=="wet"

def test_unchanged_morning_only():
    _,state=decide(report(),{},now=EVENING)
    reason,state=decide(report(),state,now=MORNING)
    assert reason=="No material changes"
    assert decide(report(),state,now=MORNING)[0] is None

def test_material_change_anytime():
    _,state=decide(report(),{},now=EVENING)
    assert "Material change" in decide(report(2.3),state,now=EVENING)[0]

def test_dry_transition_once():
    _,state=decide(report(),{},now=EVENING)
    assert decide(report(0.1),state,now=EVENING)[0] is None
    _,state=decide(report(0.1),state,now=EVENING)
    reason,state=decide(report(0.1),state,now=EVENING)
    assert "dry pattern" in reason
    assert decide(report(0.1),state,now=EVENING)[0] is None

def test_force_dry():
    assert "Manual status" in decide(report(0.1),{},force=True,now=EVENING)[0]

def test_message():
    assert "7-day county avg" in message(report(),"No material changes")
