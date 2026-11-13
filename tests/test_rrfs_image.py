from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
import pytest
import rrfs_image as rrfs

NOW = datetime(2026,11,13,14,tzinfo=timezone.utc)

@pytest.fixture
def prepared(tmp_path):
    reference=json.loads((rrfs.ROOT/'assets/rrfs/watershed-weights.json').read_text())['grid']
    start=NOW.replace(hour=6)
    manifest={'initialized':start.isoformat(),'directory':'2026111306','completeThroughHour':84,
              'frames':[{'hour':84,'valid':(start+timedelta(hours=84)).isoformat()}],'grid':reference}
    grid={field:[.5]*(reference['width']*reference['height']) for field in ('qpf','rain')}
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    (tmp_path/'grid.json').write_text(json.dumps(grid))
    Image.new('RGB',(10,10)).save(tmp_path/rrfs.IMAGE)
    return tmp_path,manifest,grid


def test_forecast_freshness_completeness_and_grid_guards(prepared):
    _,m,g=prepared
    rrfs.validate_manifest(m,NOW)
    rrfs.validate_grid(g,m)
    with pytest.raises(ValueError,match='stale'):rrfs.validate_manifest(m,NOW+timedelta(days=1))
    m['completeThroughHour']=81
    with pytest.raises(ValueError,match='84-hour'):rrfs.validate_manifest(m,NOW)
    m['completeThroughHour']=84;m['grid']['width']+=1
    with pytest.raises(ValueError,match='grid changed'):rrfs.validate_manifest(m,NOW)


@pytest.mark.parametrize('value',[None,-1,float('nan'),float('inf')])
def test_invalid_grid_withheld(prepared,value):
    _,m,g=prepared;g['qpf'][0]=value
    with pytest.raises(ValueError,match='invalid'):rrfs.validate_grid(g,m)


def test_rain_cannot_exceed_precip(prepared):
    _,m,g=prepared;g['rain'][0]=1
    with pytest.raises(ValueError,match='exceeds'):rrfs.validate_grid(g,m)


def test_caption_has_local_start_and_forecast_through(prepared):
    _,m,_=prepared;text=rrfs.caption(m)
    assert 'Thu 11/12, 11:00 PM MST' in text
    assert 'Mon 11/16, 11:00 AM MST' in text
    assert '(84 hours)' in text


def test_delivery_slots_use_mountain_time_and_quiet_hours():
    assert rrfs.delivery_slot(NOW)=='2026-11-13 AM'
    assert rrfs.delivery_slot(NOW.replace(hour=13,minute=29)) is None
    assert rrfs.delivery_slot(NOW.replace(hour=13,minute=30))=='2026-11-13 AM'
    assert rrfs.delivery_slot(NOW+timedelta(hours=12))=='2026-11-13 PM'
    assert rrfs.delivery_slot(NOW+timedelta(hours=14)) is None


def test_upload_attach_and_deduplicate_only_after_success(prepared,monkeypatch):
    p,_,_=prepared;state=p/'state.json';posts=[]
    monkeypatch.setenv('GROUPME_BOT_ID','test-bot');monkeypatch.setenv('GROUPME_ACCESS_TOKEN','test-token')
    def post(url,**kwargs):
        posts.append((url,kwargs))
        return SimpleNamespace(ok=True,json=lambda:{'payload':{'url':'https://i.groupme.com/test.png'}})
    monkeypatch.setattr(rrfs.requests,'post',post)
    assert rrfs.send(p,state,NOW)
    assert posts[0][1]['headers']['X-Access-Token']=='test-token'
    assert posts[1][1]['json']['attachments']==[{'type':'image','url':'https://i.groupme.com/test.png'}]
    assert not rrfs.send(p,state,NOW)
    assert len(posts)==2
    manifest=json.loads((p/'manifest.json').read_text())
    start=NOW.replace(hour=18)
    manifest.update(initialized=start.isoformat(),directory='2026111318')
    manifest['frames'][-1]['valid']=(start+timedelta(hours=84)).isoformat()
    (p/'manifest.json').write_text(json.dumps(manifest))
    assert rrfs.send(p,state,NOW+timedelta(hours=12))


@pytest.mark.parametrize('failure_step',[1,2])
def test_failed_upload_or_post_never_advances_delivery(prepared,monkeypatch,failure_step):
    p,_,_=prepared;state=p/'state.json';calls=[]
    monkeypatch.setenv('GROUPME_BOT_ID','test-bot');monkeypatch.setenv('GROUPME_ACCESS_TOKEN','test-token')
    def post(url,**kwargs):
        calls.append(url)
        return SimpleNamespace(ok=len(calls)!=failure_step,status_code=503,
                               json=lambda:{'payload':{'url':'https://i.groupme.com/test.png'}})
    monkeypatch.setattr(rrfs.requests,'post',post)
    with pytest.raises(RuntimeError,match='HTTP 503'):rrfs.send(p,state,NOW)
    assert not state.exists()


def test_missing_token_no_upload(prepared,monkeypatch):
    p,_,_=prepared
    monkeypatch.setenv('GROUPME_BOT_ID','test-bot');monkeypatch.delenv('GROUPME_ACCESS_TOKEN',raising=False)
    monkeypatch.setattr(rrfs.requests,'post',lambda *a,**k:pytest.fail('unauthorized upload'))
    with pytest.raises(RuntimeError,match='GROUPME_ACCESS_TOKEN'):rrfs.send(p,p/'state.json',NOW)


def test_fetch_uses_manifest_cycle_and_retries_publish_rollover(prepared,monkeypatch,tmp_path):
    _,m,g=prepared;urls=[]
    def get(url,**kwargs):
        urls.append(url)
        fail=len(urls)==2
        def check():
            if fail:raise rrfs.requests.HTTPError('publishing rollover')
        return SimpleNamespace(raise_for_status=check,json=lambda:m if 'manifest' in url else g)
    monkeypatch.setattr(rrfs.requests,'get',get)
    result=rrfs.fetch_forecast(tmp_path/'download',NOW)
    assert result['directory']=='2026111306'
    assert len(urls)==4 and urls[-1].endswith('/2026111306/grid-084.json')
