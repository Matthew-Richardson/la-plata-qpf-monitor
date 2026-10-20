from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from operational_monitor import compare, delivered_baseline, timing_window, river
from groupme_notify import message_parts
import groupme_notify
import json
from tests.test_groupme import report

NOW=datetime(2026,10,19,18,tzinfo=timezone.utc)

def forecast(flow=1000):
    return {"rivers":{"drgc2":{"cfs":flow,"time":"2026-10-23T16:00:00Z",
        "category":"below action","at_horizon":False,"issued":NOW.isoformat()}},"timing":{},"outlooks":{}}

def test_cumulative_river_change_and_direction():
    old=forecast()
    assert not compare(forecast(1100),old,NOW)
    assert "increased 1,000 to 1,210" in compare(forecast(1210),old,NOW)[0]
    assert "decreased 1,000 to 750" in compare(forecast(750),old,NOW)[0]

def test_category_crossing_alerts_even_with_small_flow_change():
    old=forecast();new=forecast(1010)
    new['rivers']['drgc2']['category']='minor'
    assert 'below action→minor' in compare(new,old,NOW)[0]

def test_unavailable_does_not_clear_river_baseline():
    old=forecast();new={'rivers':{},'outlooks':{},'timing':{}}
    assert not compare(new,old,NOW)
    assert delivered_baseline(new,old)['rivers']==old['rivers']

def test_ero_rollover_does_not_alert_and_downgrade_does():
    v={'level':2,'start':'2026-10-21T12:00:00Z','end':'2026-10-22T12:00:00Z'}
    old={'outlooks':{v['end']:v}}
    assert not compare(deepcopy(old),old,NOW)
    new=deepcopy(old);new['outlooks'][v['end']]['level']=1
    assert 'decreased Slight to Marginal' in compare(new,old,NOW)[0]
    assert not compare({},old,NOW) # Missing outlook is not a downgrade.

def timing_data(start='2026-10-21T12:00:00Z',end='2026-10-23T12:00:00Z',open_end=False):
    return {'timing':{'Durango':{'window':{'start':start,'end':end,'open_end':open_end}}}}

def test_six_hour_timing_shift_and_horizon_guard():
    old=timing_data()
    assert not compare(timing_data(start='2026-10-21T15:00:00Z'),old,NOW)
    assert 'NWS timing' in compare(timing_data(start='2026-10-21T18:00:00Z'),old,NOW)[0]
    assert not compare(timing_data(end='2026-10-24T12:00:00Z',open_end=True),old,NOW)

def test_natural_event_progression_does_not_shift_onset():
    old=timing_data(start='2026-10-19T00:00:00Z')
    assert not compare(timing_data(start='2026-10-19T18:00:00Z'),old,NOW)

def test_hourly_probabilities_bridge_brief_gaps_but_ignore_isolated_showers():
    periods=[]
    for n in range(24):
        a=NOW+timedelta(hours=n)
        periods.append({'startTime':a.isoformat(),'endTime':(a+timedelta(hours=1)).isoformat(),
                        'probabilityOfPrecipitation':{'value':70 if n in [0,8,9,10,12,13,14] else 10}})
    w=timing_window(periods,NOW)
    assert w['start']==(NOW+timedelta(hours=8)).isoformat()
    assert w['end']==(NOW+timedelta(hours=15)).isoformat()

def test_groupme_chunks_fit_and_preserve_lines():
    text='\n'.join('Forecast '+str(n)+' '+('x'*150) for n in range(20))
    chunks=message_parts(text)
    assert max(map(len,chunks))<=950
    assert '\n'.join(chunks)==text

def test_river_units_freshness_and_stage_category(monkeypatch):
    meta={'flood':{'categories':{'minor':{'stage':8}}}}
    series={'forecast':{'issuedTime':NOW.isoformat(),'secondaryUnits':'kcfs','primaryUnits':'ft','data':[
        {'validTime':(NOW+timedelta(days=1)).isoformat(),'secondary':1.5,'primary':8.1},
        {'validTime':(NOW+timedelta(days=2)).isoformat(),'secondary':1.,'primary':7.}]}}
    monkeypatch.setattr('operational_monitor.get',lambda u:series if u.endswith('stageflow') else meta)
    r=river('drgc2',NOW)
    assert r['cfs']==1500 and r['category']=='minor'
    series['forecast']['issuedTime']=(NOW-timedelta(days=2)).isoformat()
    with pytest.raises(ValueError,match='stale'):river('drgc2',NOW)

def test_failed_send_preserves_delivery_baseline(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'data').mkdir()
    (tmp_path/'data/latest.json').write_text(json.dumps(report()))
    baseline={'status':'wet','last_alert_totals':{'county':2.0}}
    state_path=tmp_path/'data/notification_state.json'
    state_path.write_text(json.dumps(baseline))
    operational={**forecast(),'errors':[]}
    monkeypatch.setattr(groupme_notify,'collect',lambda now:operational)
    monkeypatch.setattr(groupme_notify,'decide',lambda *a,**k:('Manual status',dict(baseline)))
    monkeypatch.setenv('GROUPME_BOT_ID','test-only')
    monkeypatch.setattr('sys.argv',['groupme_notify.py'])
    def fail(*a,**k):raise RuntimeError('simulated delivery failure')
    monkeypatch.setattr(groupme_notify.requests,'post',fail)
    with pytest.raises(RuntimeError,match='simulated'):groupme_notify.main()
    assert json.loads(state_path.read_text())==baseline

def test_preview_never_sends_or_saves_baseline(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'data').mkdir()
    (tmp_path/'data/latest.json').write_text(json.dumps(report()))
    monkeypatch.setattr(groupme_notify,'collect',lambda now:{**forecast(),'errors':[]})
    monkeypatch.setattr('sys.argv',['groupme_notify.py','--preview'])
    monkeypatch.setattr(groupme_notify.requests,'post',lambda *a,**k:pytest.fail('preview sent a message'))
    groupme_notify.main()
    assert not (tmp_path/'data/notification_state.json').exists()

def test_wet_brief_sends_weather_then_rivers_as_separate_posts(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path/'data').mkdir()
    (tmp_path/'data/latest.json').write_text(json.dumps(report()))
    monkeypatch.setattr(groupme_notify,'collect',lambda now:{**forecast(),'errors':[]})
    monkeypatch.setattr(groupme_notify,'decide',lambda *a,**k:('No material changes',{'status':'wet'}))
    monkeypatch.setattr(groupme_notify,'compare',lambda *a:[])
    monkeypatch.setattr('sys.argv',['groupme_notify.py'])
    monkeypatch.setenv('GROUPME_BOT_ID','test-only')
    posts=[]
    class Response:
        status_code=202
        def raise_for_status(self):pass
    def send(*a,**k):posts.append(k['json']['text']);return Response()
    monkeypatch.setattr(groupme_notify.requests,'post',send)
    groupme_notify.main()
    assert len(posts)==2
    assert posts[0].startswith('LPC Weather |')
    assert posts[1].startswith('LPC River Forecasts |')
    assert 'Animas at Durango' not in posts[0]
    assert 'La Plata at Hesperus' in posts[1] and 'state line' not in posts[1]
    assert all(len(t)<=1000 for t in posts)
    assert json.loads((tmp_path/'data/notification_state.json').read_text())['operational']['rivers']

def test_stage_labels_are_in_every_river_brief_even_if_unchanged():
    for category,label in [('action','ACTION STAGE'),('minor','MINOR FLOOD STAGE'),
                           ('moderate','MODERATE FLOOD STAGE'),('major','MAJOR FLOOD STAGE')]:
        current=forecast();current['rivers']['drgc2'].update(category=category,stage_ft=8.1)
        text=groupme_notify.river_brief(current,[],NOW)
        assert label in text and '8.10 ft' in text
