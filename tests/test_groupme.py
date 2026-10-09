from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from groupme_notify import decide, message

def report(q=2.0):
    return {"periods": {**{name:{"average_in":0.1,"valid_period":["2026-10-08 12:00:00","2026-10-09 12:00:00"]} for name in ("Day 1","Day 2","Day 3","Days 4-5","Days 6-7")},
                        "Days 1-7":{"average_in":q,"min_in":0.1,"max_in":3.0,"valid_period":["2026-10-08 12:00:00","2026-10-15 12:00:00"]}},
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
    first,state=decide(report(0.1),state,now=EVENING)
    assert first is None
    reason,state=decide(report(0.1),state,now=EVENING)
    assert "dry pattern" in reason
    assert decide(report(0.1),state,now=EVENING)[0] is None

def test_force_dry():
    assert "Manual status" in decide(report(0.1),{},force=True,now=EVENING)[0]

def test_message():
    assert "7-day county avg" in message(report(),"No material changes")

@pytest.mark.parametrize("month", [1, 10])
@pytest.mark.parametrize("hour", [0, 1, 5, 21, 23])
@pytest.mark.parametrize("force", [False, True])
def test_quiet_hours_preserve_delivery_state(month, hour, force):
    _,state=decide(report(),{},now=MORNING)
    original=dict(state)
    now=datetime(2026,month,9,hour,tzinfo=ZoneInfo("America/Denver"))
    reason,next_state=decide(report(3.0),state,force=force,now=now)
    assert reason is None
    assert next_state==original
    assert state==original

def test_overnight_change_is_still_detected_next_morning():
    _,state=decide(report(),{},now=EVENING)
    night=datetime(2026,10,9,1,tzinfo=ZoneInfo("America/Denver"))
    reason,state=decide(report(3.0),state,now=night)
    assert reason is None
    morning=night.replace(hour=7)
    assert "Material change" in decide(report(3.0),state,now=morning)[0]

def test_overnight_wet_opening_is_deferred():
    night=datetime(2026,10,9,1,tzinfo=ZoneInfo("America/Denver"))
    reason,state=decide(report(),{},now=night)
    assert reason is None and state=={}
    assert decide(report(),state,now=night.replace(hour=7))[0]=="Wet pattern detected"

def test_overnight_dry_transition_is_not_marked_delivered():
    _,state=decide(report(),{},now=EVENING)
    _,state=decide(report(0.1),state,now=EVENING)
    night=datetime(2026,10,9,1,tzinfo=ZoneInfo("America/Denver"))
    reason,deferred=decide(report(0.1),state,now=night)
    assert reason is None and deferred==state
    assert "Returned to dry pattern" in decide(report(0.1),deferred,now=night.replace(hour=7))[0]

def test_quiet_hours_use_mountain_time_for_utc_input():
    night=datetime(2026,10,9,7,tzinfo=ZoneInfo("UTC"))
    assert decide(report(),{},now=night)[0] is None
    assert decide(report(),{},now=night.replace(hour=13))[0]=="Wet pattern detected"

@pytest.mark.parametrize("hour", [6,20])
def test_delivery_window_boundaries(hour):
    now=MORNING.replace(hour=hour)
    assert decide(report(),{},now=now)[0]=="Wet pattern detected"

