from shapely.geometry import box
import qpf_monitor
from qpf_monitor import GAUGE_CATCHMENTS, gauge_catchment_geometries, summarize

def test_uniform():
    region=box(-108,37,-107,38)
    fs=[{"properties":{"qpf":1.5,"units":"inches","issue_time":"2026","start_time":"a","end_time":"b"},"geometry":region.__geo_interface__}]
    out=summarize(region,fs)
    assert out["average_in"]==1.5
    assert out["coverage_pct"]==100

def test_unmapped_area_counted_as_zero():
    region=box(-108,37,-107,38)
    partial=box(-108,37,-107.9,38)
    fs=[{"properties":{"qpf":1,"units":"inches"},"geometry":partial.__geo_interface__}]
    result=summarize(region,fs)
    assert result["coverage_pct"] < 15
    assert 0 < result["average_in"] < 0.15
    assert result["min_in"] == 0.0

def test_gauge_catchments_use_usgs_nldi(monkeypatch):
    calls=[]
    def fake_fetch(url,params):
        calls.append((url,params))
        return {"type":"FeatureCollection","features":[{"geometry":box(-108,37,-107,38).__geo_interface__}]}
    monkeypatch.setattr(qpf_monitor,"fetch",fake_fetch)
    result=gauge_catchment_geometries()
    assert set(result)==set(GAUGE_CATCHMENTS)
    assert all(row["source"].endswith("/basin") for row in result.values())
    assert all(params=={"f":"json"} for _,params in calls)
