from shapely.geometry import box
from qpf_monitor import summarize

def test_uniform():
    region=box(-108,37,-107,38)
    fs=[{"properties":{"qpf":1.5,"units":"inches","issue_time":"2026","start_time":"a","end_time":"b"},"geometry":region.__geo_interface__}]
    out=summarize(region,fs)
    assert out["average_in"]==1.5
    assert out["coverage_pct"]==100

def test_incomplete_fails():
    region=box(-108,37,-107,38)
    partial=box(-108,37,-107.9,38)
    fs=[{"properties":{"qpf":1,"units":"inches"},"geometry":partial.__geo_interface__}]
    try:
        summarize(region,fs)
        raise AssertionError("Expected coverage failure")
    except RuntimeError as exc:
        assert "coverage" in str(exc).lower()
