"""NWS/NDFD rain-only averages for small La Plata County drainages."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOCAL = ZoneInfo("America/Denver")
MM_PER_INCH = 25.4
SNOW_LIQUID_RATIO = 10.0
SAMPLE_STEP_DEG = 0.025

DRAINAGES = {
    "Junction": "09361400",
    "Hermosa": "09361000",
    "Stevens": "372431107481901",
}


def session():
    retry = Retry(
        total=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    value = requests.Session()
    value.headers.update({"User-Agent": "la-plata-qpf-monitor/1.0"})
    value.mount("https://", HTTPAdapter(max_retries=retry))
    return value


def _json(http, url):
    response = http.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def _rings(feature_collection):
    geometry = feature_collection["features"][0]["geometry"]
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    raise ValueError(f"Unsupported basin geometry: {geometry['type']}")


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        crosses = (yi > y) != (yj > y)
        if crosses and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _inside(point, feature_collection):
    for polygon in _rings(feature_collection):
        if _point_in_ring(point, polygon[0]) and not any(
            _point_in_ring(point, hole) for hole in polygon[1:]
        ):
            return True
    return False


def sample_points(feature_collection, step=SAMPLE_STEP_DEG):
    coordinates = [
        point
        for polygon in _rings(feature_collection)
        for ring in polygon
        for point in ring
    ]
    min_x = min(point[0] for point in coordinates)
    max_x = max(point[0] for point in coordinates)
    min_y = min(point[1] for point in coordinates)
    max_y = max(point[1] for point in coordinates)
    points = []
    y = min_y + step / 2
    while y < max_y:
        x = min_x + step / 2
        while x < max_x:
            if _inside((x, y), feature_collection):
                points.append((round(y, 5), round(x, 5)))
            x += step
        y += step
    if not points:
        feature = feature_collection["features"][0]
        ring = _rings({"features": [feature]})[0][0]
        points = [(sum(p[1] for p in ring) / len(ring), sum(p[0] for p in ring) / len(ring))]
    return points


_DURATION = re.compile(r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?)?")


def interval(value):
    start_text, duration_text = value.split("/", 1)
    start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
    match = _DURATION.fullmatch(duration_text)
    if not match:
        raise ValueError(f"Unsupported ISO duration: {duration_text}")
    length = timedelta(
        days=int(match.group("days") or 0),
        hours=int(match.group("hours") or 0),
        minutes=int(match.group("minutes") or 0),
    )
    return start, start + length


def _allocated(values, start, end):
    """Allocate interval totals by overlap to local calendar dates."""
    daily = defaultdict(float)
    for row in values:
        value = row.get("value")
        if value is None:
            continue
        row_start, row_end = interval(row["validTime"])
        overlap_start = max(row_start, start)
        overlap_end = min(row_end, end)
        if overlap_end <= overlap_start:
            continue
        duration = (row_end - row_start).total_seconds()
        cursor = overlap_start
        while cursor < overlap_end:
            local_cursor = cursor.astimezone(LOCAL)
            next_midnight = datetime.combine(
                local_cursor.date() + timedelta(days=1), datetime.min.time(), LOCAL
            ).astimezone(timezone.utc)
            segment_end = min(overlap_end, next_midnight)
            daily[local_cursor.date().isoformat()] += value * (
                (segment_end - cursor).total_seconds() / duration
            )
            cursor = segment_end
    return daily


def _cell_summary(grid, start, end):
    properties = grid["properties"]
    qpf = _allocated(properties.get("quantitativePrecipitation", {}).get("values", []), start, end)
    snow = _allocated(properties.get("snowfallAmount", {}).get("values", []), start, end)
    days = sorted(set(qpf) | set(snow))
    rain = {
        day: max(0.0, qpf.get(day, 0.0) - snow.get(day, 0.0) / SNOW_LIQUID_RATIO)
        for day in days
    }
    return {"qpf": qpf, "snow": snow, "rain": rain}


def _basin(http, site):
    return _json(
        http,
        f"https://api.water.usgs.gov/nldi/linked-data/nwissite/USGS-{site}/basin?f=json",
    )


def _grid_weights(http, basin):
    weights = Counter()
    for latitude, longitude in sample_points(basin):
        point = _json(http, f"https://api.weather.gov/points/{latitude},{longitude}")
        weights[point["properties"]["forecastGridData"]] += 1
    return weights


def _average_cells(cells, weights):
    total_weight = sum(weights.values())
    daily = {key: defaultdict(float) for key in ("qpf", "snow", "rain")}
    for url, weight in weights.items():
        for key in daily:
            for day, value in cells[url][key].items():
                daily[key][day] += value * weight / total_weight
    return {key: dict(values) for key, values in daily.items()}


def _rain_on_snow(daily):
    dates = sorted(set(daily["snow"]) | set(daily["rain"]))
    concerns = []
    for day in dates:
        date_value = datetime.fromisoformat(day).date()
        next_day = (date_value + timedelta(days=1)).isoformat()
        snow_inches = daily["snow"].get(day, 0.0) / MM_PER_INCH
        rain_inches = daily["rain"].get(next_day, 0.0) / MM_PER_INCH
        if snow_inches >= 1.0 and rain_inches >= 0.25:
            concerns.append((day, next_day, rain_inches))
    return concerns


def collect_local_rain(now=None, http=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    http = http or session()
    start = now
    end_3 = start + timedelta(days=3)
    end_7 = start + timedelta(days=7)
    basins = {name: _basin(http, site) for name, site in DRAINAGES.items()}
    weights = {name: _grid_weights(http, basin) for name, basin in basins.items()}
    urls = sorted({url for value in weights.values() for url in value})
    raw_grids = {url: _json(http, url) for url in urls}
    grids_3 = {url: _cell_summary(grid, start, end_3) for url, grid in raw_grids.items()}
    grids_7 = {url: _cell_summary(grid, start, end_7) for url, grid in raw_grids.items()}
    drainage = {}
    for name in DRAINAGES:
        daily_3 = _average_cells(grids_3, weights[name])
        daily_7 = _average_cells(grids_7, weights[name])
        rain_3 = sum(daily_3["rain"].values())
        rain_7 = sum(daily_7["rain"].values())
        drainage[name] = {
            "rain_3day_in": round(rain_3 / MM_PER_INCH, 3),
            "rain_7day_in": round(rain_7 / MM_PER_INCH, 3),
            "daily": {
                key: {day: round(value / MM_PER_INCH, 3) for day, value in values.items()}
                for key, values in daily_7.items()
            },
            "rain_on_snow": _rain_on_snow(daily_7),
            "sample_points": sum(weights[name].values()),
            "grid_cells": len(weights[name]),
        }
    return {
        "issued": min(
            raw_grids[url]["properties"]["updateTime"] for url in urls
        ) if urls else None,
        "start": start.isoformat(),
        "end_3day": end_3.isoformat(),
        "end_7day": end_7.isoformat(),
        "drainages": drainage,
        "method": "NWS/NDFD QPF minus modeled snowfall water-equivalent at 10:1",
    }


def _date_range(start, end):
    start = datetime.fromisoformat(start).astimezone(LOCAL)
    end = datetime.fromisoformat(end).astimezone(LOCAL)
    return f"{start.strftime('%a %-m/%-d')}–{end.strftime('%a %-m/%-d')}"


def brief(result):
    lines = ["Local drainage rain-only averages | NWS grid"]
    lines.append(
        f"3-day ({_date_range(result['start'], result['end_3day'])}): "
        + " | ".join(
            f"{name} {values['rain_3day_in']:.2f}\""
            for name, values in result["drainages"].items()
        )
    )
    lines.append(
        f"7-day ({_date_range(result['start'], result['end_7day'])}): "
        + " | ".join(
            f"{name} {values['rain_7day_in']:.2f}\""
            for name, values in result["drainages"].items()
        )
    )
    concern_parts = []
    for name, values in result["drainages"].items():
        for snow_day, rain_day, rain_inches in values["rain_on_snow"]:
            snow_label = datetime.fromisoformat(snow_day).strftime("%a %-m/%-d")
            rain_label = datetime.fromisoformat(rain_day).strftime("%a %-m/%-d")
            concern_parts.append(f"{name} {snow_label}→{rain_label} ({rain_inches:.2f}\" rain)")
    if concern_parts:
        lines.append("Rain-on-snow concern: " + " | ".join(concern_parts))
    else:
        lines.append("Rain-on-snow: no consecutive-day modeled snow→rain signal")
    lines.append("Rain only; modeled snow excluded. Full upstream basins.")
    issued = result.get("issued")
    if issued:
        value = datetime.fromisoformat(issued.replace("Z", "+00:00")).astimezone(LOCAL)
        lines.append(value.strftime("NWS grid issued %a %-m/%-d, %-I:%M %p %Z"))
    return "\n".join(lines)
