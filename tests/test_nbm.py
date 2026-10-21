from datetime import datetime, timezone

from nbm_monitor import FORECAST_HOURS, accumulation_window, byte_range, candidate_cycles


def test_forecast_hours_tile_complete_week():
    windows=[accumulation_window(hour) for hour in FORECAST_HOURS]
    assert windows[0]==(0,1)
    assert windows[47]==(47,48)
    assert windows[48]==(48,54)
    assert windows[-1]==(162,168)
    assert all(left[1]==right[0] for left,right in zip(windows,windows[1:]))


def test_only_synoptic_cycles_are_candidates():
    now=datetime(2026,10,21,5,30,tzinfo=timezone.utc)
    cycles=candidate_cycles(now,lookback_days=1)
    assert cycles[0].hour==0
    assert all(cycle.hour in (0,6,12,18) for cycle in cycles)


def test_byte_range_selects_six_hour_deterministic_apcp():
    index='\n'.join([
        '1:100:d=2026102100:APCP:surface:53-54 hour acc fcst:',
        '2:200:d=2026102100:APCP:surface:48-54 hour acc fcst:',
        '3:300:d=2026102100:TMP:surface:54 hour fcst:',
    ])
    assert byte_range(index,54)==(200,299)
