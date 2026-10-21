"""Seven-day deterministic NBM QPF averages for La Plata County and watched HUC10s."""
from datetime import datetime, timedelta, timezone
import time

import numpy as np
import requests
from shapely import contains_xy


SOURCE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
SYNOPTIC_HOURS = (0, 6, 12, 18)
FORECAST_HOURS = tuple(range(1, 49)) + tuple(range(54, 169, 6))


def candidate_cycles(now=None, lookback_days=2):
    """Newest-first 00/06/12/18Z cycles, which tile the full 168 hours exactly."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    floor = now.replace(minute=0, second=0, microsecond=0)
    return [
        floor - timedelta(hours=offset)
        for offset in range(0, lookback_days * 24 + 1)
        if (floor - timedelta(hours=offset)).hour in SYNOPTIC_HOURS
    ]


def accumulation_window(forecast_hour):
    if forecast_hour <= 48:
        return forecast_hour - 1, forecast_hour
    return forecast_hour - 6, forecast_hour


def object_url(cycle, forecast_hour):
    stamp = cycle.strftime("%Y%m%d")
    hour = cycle.strftime("%H")
    name = f"blend.t{hour}z.core.f{forecast_hour:03d}.co.grib2"
    return f"{SOURCE}/blend.{stamp}/{hour}/core/{name}"


def byte_range(index_text, forecast_hour):
    """Return the deterministic APCP message byte range from a wgrib2 index."""
    start_hour, end_hour = accumulation_window(forecast_hour)
    target = f":APCP:surface:{start_hour}-{end_hour} hour acc fcst:"
    lines = index_text.splitlines()
    for position, line in enumerate(lines):
        if target not in line or ":prob " in line:
            continue
        start = int(line.split(":", 2)[1])
        end = int(lines[position + 1].split(":", 2)[1]) - 1 if position + 1 < len(lines) else None
        return start, end
    raise RuntimeError(f"NBM f{forecast_hour:03d} lacks deterministic {start_hour}-{end_hour}h APCP")


def _get(url, *, headers=None, timeout=90, attempts=4):
    last = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "LaPlata-QPF-Monitor/1.0", **(headers or {})},
                timeout=timeout,
            )
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"NBM request failed for {url}: {last}") from last


def fetch_apcp_message(cycle, forecast_hour):
    url = object_url(cycle, forecast_hour)
    index = _get(url + ".idx").text
    start, end = byte_range(index, forecast_hour)
    if end is None:
        raise RuntimeError(f"NBM APCP message unexpectedly last in index: f{forecast_hour:03d}")
    response = _get(url, headers={"Range": f"bytes={start}-{end}"})
    if response.status_code != 206:
        raise RuntimeError(f"NBM byte-range request returned HTTP {response.status_code}")
    return response.content


def _latest_complete_cycle(now=None):
    """Require the final 162–168h field before selecting a cycle."""
    errors = []
    for cycle in candidate_cycles(now):
        try:
            url = object_url(cycle, 168)
            index = _get(url + ".idx", attempts=2).text
            byte_range(index, 168)
            return cycle
        except Exception as exc:
            errors.append(f"{cycle:%Y-%m-%d %HZ}: {exc}")
    raise RuntimeError("No complete NBM synoptic cycle found; " + " | ".join(errors[-4:]))


def calculate(regions, now=None):
    """Average a complete rolling 168-hour NBM deterministic QPF over each polygon."""
    from eccodes import codes_get_array, codes_new_from_message, codes_release

    cycle = _latest_complete_cycle(now)
    masks = None
    sums = {name: 0.0 for name in regions}
    counts = {}
    for forecast_hour in FORECAST_HOURS:
        message = fetch_apcp_message(cycle, forecast_hour)
        handle = codes_new_from_message(message)
        try:
            values = np.asarray(codes_get_array(handle, "values"), dtype=float)
            if masks is None:
                latitudes = np.asarray(codes_get_array(handle, "latitudes"), dtype=float)
                longitudes = np.asarray(codes_get_array(handle, "longitudes"), dtype=float)
                longitudes = np.where(longitudes > 180, longitudes - 360, longitudes)
                masks = {name: contains_xy(geometry, longitudes, latitudes) for name, geometry in regions.items()}
                counts = {name: int(mask.sum()) for name, mask in masks.items()}
                empty = [name for name, count in counts.items() if count == 0]
                if empty:
                    raise RuntimeError("NBM grid has no cell centers in: " + ", ".join(empty))
            for name, mask in masks.items():
                selected = values[mask]
                valid = selected[np.isfinite(selected) & (selected < 1e20)]
                if valid.size != counts[name]:
                    raise RuntimeError(f"NBM missing values in {name} at f{forecast_hour:03d}")
                sums[name] += float(valid.mean())
        finally:
            codes_release(handle)

    totals = {
        name: {"average_in": round(total_mm / 25.4, 3), "grid_points": counts[name]}
        for name, total_mm in sums.items()
    }
    valid_end = cycle + timedelta(hours=168)
    return {
        "status": "complete",
        "source": SOURCE,
        "cycle_utc": cycle.isoformat(),
        "valid_period": [cycle.isoformat(), valid_end.isoformat()],
        "forecast_hours": list(FORECAST_HOURS),
        "grid": "NBM CONUS core deterministic, approximately 2.5 km",
        "totals": totals,
        "note": "Point mean of a complete 168-hour deterministic NBM cycle; comparison guidance, not a WPC replacement.",
    }
