from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from groupme_notify import decide, message, nbm_comparison_line

def report(q=2.0):
    return {"periods": {**{name:{"average_in":0.1,"valid_period":["2026-10-08 12:00:00","2026-10-09 12:00:00"]} for name in ("Day 1","Day 2","Day 3","Days 4-5","Days 6-7")},
                        "Days 1-7":{"average_in":q,"min_in":0.1,"max_in":3.0,"valid_period":["2026-10-08 12:00:00","2026-10-15 12:00:00"]}},
            "huc10_watersheds":{code:{"periods":{"Days 1-7":{"average_in":q}}}
            for code in ("1408010112","1408010111","1408010407","1408010403","1408010501")}}
MORNING=datetime(2026,10,8,7,tzinfo=ZoneInfo("America/Denver"))
EVENING=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver"))

def test_wet_opening():
    reason,s=decide(report(),{},now=MORNING)
    assert "Wet pattern" in reason and s["status"]=="wet"

def test_unchanged_slot_only_once():
    _,state=decide(report(),{},now=EVENING)
    reason,state=decide(report(),state,now=MORNING)
    assert reason=="No material changes"
    assert decide(report(),state,now=MORNING)[0] is None

def test_wet_unchanged_sends_morning_and_evening_without_duplicate_slot():
    _,state=decide(report(),{},now=MORNING)
    assert decide(report(),state,now=MORNING.replace(hour=10))[0] is None
    reason,state=decide(report(),state,now=EVENING)
    assert reason=='No material changes'
    assert decide(report(),state,now=EVENING.replace(hour=20))[0] is None

def test_material_change_increase_says_direction():
    _,state=decide(report(),{},now=EVENING)
    reason,_=decide(report(2.3),state,now=EVENING)
    assert reason.startswith("Material change — forecast increased:")
    assert "County increased 0.30in" in reason

def test_material_change_decrease_says_direction():
    _,state=decide(report(),{},now=EVENING)
    reason,_=decide(report(1.7),state,now=EVENING)
    assert reason.startswith("Material change — forecast decreased:")
    assert "County decreased 0.30in" in reason

def test_material_change_mixed_says_each_direction():
    _,state=decide(report(),{},now=EVENING)
    changed=report(2.3)
    changed["huc10_watersheds"]["1408010112"]["periods"]["Days 1-7"]["average_in"]=1.7
    reason,_=decide(changed,state,now=EVENING)
    assert reason.startswith("Material change — forecast mixed:")
    assert "County increased 0.30in" in reason
    assert "Vallecito decreased 0.30in" in reason

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
    text=message(report(),"No material changes")
    assert "County 7-day avg" in text
    assert 'La Plata 2.00"' in text
    assert "flood guidance" not in text

def test_nbm_comparison_is_compact_and_directional():
    current=report()
    current["nbm"]={"status":"complete","totals":{
        "county":{"average_in":1.95},
        "1408010112":{"average_in":2.30},
        "1408010111":{"average_in":2.38},
        "1408010407":{"average_in":2.35},
        "1408010403":{"average_in":2.31},
        "1408010501":{"average_in":1.76}}}
    line=nbm_comparison_line(current)
    assert line=='NBM comparison: County similar at 1.95"; eastern basins 0.30–0.38" higher; La Plata 0.24" lower'
    assert line in message(current,"No material changes")

def test_missing_nbm_does_not_change_weather_message():
    current=report()
    current["nbm"]={"status":"unavailable","error":"test"}
    assert "NBM comparison" not in message(current,"No material changes")

def test_material_change_message_is_quick_read_and_directional():
    text=message(report(),"Material change — forecast increased: County increased 0.30in, Upper Pine increased 0.25in")
    assert text.startswith("LPC precipitation | Rain increased")
    assert 'Change: County increased 0.30", Upper Pine increased 0.25"' in text
    assert "Valid:" not in text and "Basins:" not in text

def test_snow_message_collapses_nested_probability_categories():
    current=report()
    current["snow"]={"active":True,"periods":{
        "Day 1":[
            {"threshold_in":4,"minimum_probability_pct":10},
            {"threshold_in":4,"minimum_probability_pct":40},
            {"threshold_in":8,"minimum_probability_pct":10}],
        "Day 2":[
            {"threshold_in":4,"minimum_probability_pct":10},
            {"threshold_in":4,"minimum_probability_pct":40},
            {"threshold_in":4,"minimum_probability_pct":70},
            {"threshold_in":8,"minimum_probability_pct":10},
            {"threshold_in":8,"minimum_probability_pct":40}],
        "Day 3":[]}}
    text=message(current,"Snow guidance increased")
    assert '40–69% for 4+"; 10–39% for 8+"' in text
    assert '70%+ for 4+"; 40–69% for 8+"' in text
    assert text.count('for 4+"')==2
    assert text.startswith("LPC precipitation | Snow increased")

def test_elevation_snow_message():
    current=report()
    current["snow"]={"active":True,"periods":{"Day 1":[{"threshold_in":4,"minimum_probability_pct":70}],"Day 2":[],"Day 3":[]},
        "elevation_bands":{"Day 1":{
            "Below 7,000 ft":[],
            "7,000–9,000 ft":[{"threshold_in":4,"minimum_probability_pct":40}],
            "Above 9,000 ft":[{"threshold_in":4,"minimum_probability_pct":70},{"threshold_in":8,"minimum_probability_pct":40}]}}}
    text=message(current,"Snow guidance increased")
    assert 'Below 7,000 ft: no mapped 10%+ chance of 4"+' in text
    assert '7,000–9,000 ft: 40–69% for 4+"' in text
    assert 'Above 9,000 ft: 70%+ for 4+"; 40–69% for 8+"' in text

def test_legacy_nested_snow_state_does_not_create_false_change():
    current=report()
    current["snow"]={"active":True,"periods":{"Day 1":[
        {"threshold_in":4,"minimum_probability_pct":10},
        {"threshold_in":4,"minimum_probability_pct":40}],"Day 2":[],"Day 3":[]}}
    _,state=decide(current,{},now=EVENING)
    state["snow_signature"]=[["Day 1",4,10],["Day 1",4,40]]
    assert decide(current,state,now=EVENING)[0] is None

def test_first_elevation_state_does_not_create_false_change():
    current=report()
    current["snow"]={"active":True,"periods":{"Day 1":[{"threshold_in":4,"minimum_probability_pct":40}],"Day 2":[],"Day 3":[]},
        "elevation_bands":{"Day 1":{"Above 9,000 ft":[{"threshold_in":4,"minimum_probability_pct":40}]}}}
    _,state=decide(current,{},now=EVENING)
    state.pop("snow_elevation_signature")
    assert decide(current,state,now=EVENING)[0] is None

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


