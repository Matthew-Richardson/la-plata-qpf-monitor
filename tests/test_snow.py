from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
from shapely.geometry import box
from snow_monitor import analyze
from groupme_notify import decide, message

def synthetic(q=0.1,snow=False):
    p={x:{"average_in":q,"valid_period":["2026-10-08 12:00:00","2026-10-09 12:00:00"]} for x in ("Day 1","Day 2","Day 3","Days 4-5","Days 6-7")}
    p["Days 1-7"]={**p["Day 1"],"min_in":0,"max_in":q}
    return {"area_schema":"gauge-catchments-v1","periods":p,
            "gauge_catchments":{site:{"periods":{"Days 1-7":{"average_in":q}}}
                for site in ("09352900","09352800","09362750","09361500","09365500")},
            "snow":{"active":snow,"periods":{"Day 1":[{"threshold_in":4,"minimum_probability_pct":40}] if snow else [],"Day 2":[],"Day 3":[]}}}

def grid():
    xs=np.linspace(0.05,0.95,20); ys=np.linspace(0.95,0.05,20); xx,yy=np.meshgrid(xs,ys)
    elevation=6000+yy*4000
    return xx,yy,elevation,np.ones_like(elevation,dtype=bool)

def test_no_snow_does_not_request_elevation():
    result=analyze(box(0,0,1,1),query=lambda *a,**k:[],elevation=lambda g: (_ for _ in ()).throw(AssertionError()))
    assert result["active"] is False

def test_snow_intersection_and_elevation_bands():
    feature={"geometry":box(0,0,1,1).__geo_interface__,"properties":{"outlook":"Moderate (40-69%)"}}
    result=analyze(box(0,0,1,1),query=lambda url,params:[feature] if url.endswith("/1") else [],elevation=lambda g:grid())
    assert result["active"] is True
    assert result["periods"]["Day 1"][0]["minimum_probability_pct"]==40
    assert all(rows[0]["minimum_probability_pct"]==40 for rows in result["elevation_bands"]["Day 1"].values())

def test_snow_starts_wet_alert_with_dry_qpf():
    reason,state=decide(synthetic(snow=True),{},now=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver")))
    assert reason=="Wet pattern detected" and state["status"]=="wet"

def test_snow_change_says_increased_or_decreased():
    now=datetime(2026,10,8,19,tzinfo=ZoneInfo("America/Denver"))
    _,state=decide(synthetic(snow=True),{},now=now)
    updated=synthetic(snow=True); updated["snow"]["periods"]["Day 1"][0]["minimum_probability_pct"]=70
    reason,state=decide(updated,state,now=now)
    assert reason=="Snow guidance increased"
    assert decide(synthetic(snow=True),state,now=now)[0]=="Snow guidance decreased"

def test_snow_message_fallback():
    msg=message(synthetic(snow=True),"Snow guidance increased")
    assert '40–69% for 4+"' in msg and "\n" in msg
