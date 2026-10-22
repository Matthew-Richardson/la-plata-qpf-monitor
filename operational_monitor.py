"""Official LPC river forecasts, county WPC ERO, and NWS hourly timing.

Source failures never become zero risk. Baselines advance only after delivery.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from shapely.geometry import shape
from qpf_monitor import county, features, PROJECT
from shapely.ops import transform

LOCAL = ZoneInfo("America/Denver")
RIVERS = {"drgc2": "Animas at Durango", "vnbc2": "Vallecito Creek",
          "lpac2": "Los Pinos above Vallecito", "lphc2": "La Plata at Hesperus",
          "fric2": "Florida above Lemon"}
OBSERVED_ONLY_RIVERS = {"fric2"}
TOWNS = {"Durango": (37.2753, -107.8801), "Bayfield": (37.2256, -107.5981),
         "Ignacio": (37.115, -107.633)}
ERO = "https://mapservices.weather.noaa.gov/vector/rest/services/hazards/wpc_precip_hazards/MapServer"
LEVELS = {0: "None", 1: "Marginal", 2: "Slight", 3: "Moderate", 4: "High"}

def dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)

def stamp(value):
    return dt(value).astimezone(LOCAL).strftime("%a %-m/%-d %-I %p")

def get(url):
    session = requests.Session()
    session.headers["User-Agent"] = "LaPlata-Operational-Monitor/1.0"
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))))
    with session:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

def stage_category(metadata, stage):
    categories = metadata.get("flood", {}).get("categories", {})
    category = "undefined" if all((v.get("stage") or -9999) < 0 for v in categories.values()) else "below action"
    for label in ("action", "minor", "moderate", "major"):
        threshold = categories.get(label, {}).get("stage")
        if stage is not None and threshold is not None and threshold >= 0 and stage >= threshold:
            category = label
    return category

def observed_river(metadata, series, now):
    observed = series.get("observed") or {}
    unit = observed.get("secondaryUnits")
    if unit not in ("kcfs", "cfs"):
        raise ValueError("unknown observed river flow units")
    rows = [r for r in observed.get("data", []) if r.get("secondary") is not None and r["secondary"] >= 0
            and dt(r["validTime"]) <= now]
    if not rows:
        raise ValueError("no river observation")
    latest = max(rows, key=lambda r: dt(r["validTime"]))
    if now - dt(latest["validTime"]) > timedelta(hours=6):
        raise ValueError("stale river observation")
    flow = latest["secondary"] * (1000 if unit == "kcfs" else 1)
    stage = latest.get("primary") if observed.get("primaryUnits") == "ft" else None
    return {"cfs": round(flow), "time": latest["validTime"],
            "category": stage_category(metadata, stage), "stage_ft": stage,
            "issued": observed.get("issuedTime") or latest["validTime"],
            "at_horizon": False, "observed": True}

def river(gauge, now):
    url = "https://api.water.noaa.gov/nwps/v1/gauges/" + gauge
    metadata, series = get(url), get(url + "/stageflow")
    forecast = series.get("forecast") or {}
    if gauge in OBSERVED_ONLY_RIVERS and (not forecast.get("issuedTime") or forecast["issuedTime"].startswith("0001-")):
        return observed_river(metadata, series, now)
    if now - dt(forecast["issuedTime"]) > timedelta(hours=36):
        raise ValueError("stale river forecast")
    unit = forecast.get("secondaryUnits")
    if unit not in ("kcfs", "cfs"):
        raise ValueError("unknown river flow units")
    rows = [r for r in forecast.get("data", []) if now <= dt(r["validTime"]) <= now + timedelta(days=7)
            and r.get("secondary") is not None and r["secondary"] >= 0]
    if not rows:
        raise ValueError("no future river forecast")
    peak = max(rows, key=lambda r: r["secondary"])
    flow = peak["secondary"] * (1000 if unit == "kcfs" else 1)
    stage = peak.get("primary") if forecast.get("primaryUnits") == "ft" else None
    category = stage_category(metadata, stage)
    return {"cfs": round(flow), "time": peak["validTime"], "category": category, "stage_ft": stage,
            "issued": forecast["issuedTime"], "at_horizon": peak is rows[-1], "observed": False}

def timing_window(periods, now):
    """Next sustained >=50% hourly precipitation window; bridge gaps <=6h."""
    periods = sorted([p for p in periods if dt(p["endTime"]) > now], key=lambda p: p["startTime"])
    if not periods:
        raise ValueError("no future NWS hourly periods")
    groups = []
    for p in periods:
        probability = p.get("probabilityOfPrecipitation", {}).get("value")
        if probability is None:
            raise ValueError("missing hourly precipitation probability")
        if probability < 50:
            continue
        if not groups or dt(p["startTime"]) - dt(groups[-1][-1]["endTime"]) > timedelta(hours=6):
            groups.append([])
        groups[-1].append(p)
    for group in groups:
        if len(group) >= 6:
            return {"start": group[0]["startTime"], "end": group[-1]["endTime"],
                    "open_end": dt(group[-1]["endTime"]) >= dt(periods[-1]["endTime"])}
    return None

def timing(town, now):
    lat, lon = TOWNS[town]
    point = get(f"https://api.weather.gov/points/{lat},{lon}")
    forecast = get(point["properties"]["forecastHourly"])["properties"]
    issued = forecast.get("updateTime") or forecast.get("generatedAt")
    if not issued or now - dt(issued) > timedelta(hours=24):
        raise ValueError("stale NWS hourly forecast")
    return {"window": timing_window(forecast["periods"], now), "issued": issued}

def outlook(day, boundary, now):
    xmin, ymin, xmax, ymax = boundary.bounds
    rows = features(f"{ERO}/{day}", {"where": "1=1", "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*", "returnGeometry": "true"})
    base = now.replace(hour=12, minute=0, second=0, microsecond=0)
    if now.hour < 12:
        base -= timedelta(days=1)
    start = base + timedelta(days=day)
    result = {"level": 0, "start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat(), "issued": None}
    for row in rows:
        p = row["properties"]
        # Validate issue even when polygon only intersects the query envelope.
        issue = dt(p["issue_time"].replace(" ", "T") + "Z")
        if now - issue > timedelta(hours=36):
            raise ValueError("stale WPC excessive rainfall outlook")
        geom = shape(row["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        if transform(PROJECT, geom.intersection(boundary)).area < 1_000_000:
            continue
        level = int(p["dn"])
        if level > result["level"]:
            result = {"level": level, "start": p["start_time"].replace(" ", "T") + "Z",
                      "end": p["end_time"].replace(" ", "T") + "Z", "issued": issue.isoformat()}
    return result

def collect(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = {"rivers": {}, "outlooks": {}, "timing": {}, "errors": [], "checked": now.isoformat()}
    jobs = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for gauge in RIVERS:
            jobs[pool.submit(river, gauge, now)] = ("rivers", gauge)
        for town in TOWNS:
            jobs[pool.submit(timing, town, now)] = ("timing", town)
        try:
            boundary = county()
            for day in range(5):
                jobs[pool.submit(outlook, day, boundary, now)] = ("outlooks", str(day))
        except Exception as exc:
            result["errors"].append("WPC county outlook: " + str(exc))
        for future, (source, key) in jobs.items():
            try:
                value = future.result()
                if source == "outlooks":
                    key = dt(value["end"]).isoformat()
                result[source][key] = value
            except Exception as exc:
                result["errors"].append(f"{source}/{key}: {exc}")
    return result

def compare(current, previous, now):
    """Material changes relative to last delivered values, never last poll."""
    changes = []
    for gauge, value in current.get("rivers", {}).items():
        if value.get("observed"):
            continue
        old = previous.get("rivers", {}).get(gauge)
        name = RIVERS[gauge]
        if old is None:
            if value["cfs"] >= 500 or value["category"] in ("action", "minor", "moderate", "major"):
                if "River forecasts added to monitoring" not in changes:
                    changes.append("River forecasts added to monitoring")
            continue
        delta = value["cfs"] - old["cfs"]
        if abs(delta) >= max(100, old["cfs"] * .20):
            changes.append(f"{name} {'increased' if delta > 0 else 'decreased'} {old['cfs']:,} to {value['cfs']:,} cfs")
        elif value["category"] != old["category"] and value["category"] != "undefined":
            changes.append(f"{name}: {old['category']}→{value['category']}")
        elif (value["cfs"] >= 500 and not value["at_horizon"] and not old["at_horizon"]
              and abs((dt(value["time"]) - dt(old["time"])).total_seconds()) >= 21600):
            changes.append(f"{name} peak timing: {stamp(old['time'])}→{stamp(value['time'])}")
    for key, value in current.get("outlooks", {}).items():
        if dt(value["end"]) <= now:
            continue
        old = previous.get("outlooks", {}).get(key)
        before = old["level"] if old else 0
        if before != value["level"]:
            label = (f"{'increased' if value['level'] > before else 'decreased'} {LEVELS[before]} to {LEVELS[value['level']]}"
                     if old else f"{LEVELS[value['level']]} (new monitored period)")
            changes.append(f"WPC risk in part of LPC: {label} | {stamp(value['start'])}–{stamp(value['end'])}")
    for town, value in current.get("timing", {}).items():
        if town not in previous.get("timing", {}):
            if value["window"]:
                w = value["window"]
                changes.append(f"{town}: NWS likely precipitation {stamp(w['start'])}–{stamp(w['end'])}")
            continue
        old = previous["timing"][town]["window"]
        new = value["window"]
        if old and dt(old["end"]) <= now:
            old = None
        if old and new:
            # Ignore onset moving forward merely because the event has begun.
            onset_shift = (dt(old["start"]) > now and dt(new["start"]) > now
                           and abs((dt(new["start"]) - dt(old["start"])).total_seconds()) >= 21600)
            end_shift = (not old["open_end"] and not new["open_end"]
                         and abs((dt(new["end"]) - dt(old["end"])).total_seconds()) >= 21600)
            if onset_shift or end_shift:
                changes.append(f"{town} NWS timing: {stamp(old['start'])}–{stamp(old['end'])} → "
                               f"{stamp(new['start'])}–{stamp(new['end'])}")
        elif bool(old) != bool(new):
            changes.append(f"{town}: NWS likely precipitation window {'added' if new else 'removed'}")
    return changes

def delivered_baseline(current, previous):
    return {source: {**previous.get(source, {}), **current.get(source, {})}
            for source in ("rivers", "outlooks", "timing")}

def message_lines(current, changes):
    lines = ["LPC weather | River / flood / timing update", *changes]
    for gauge, value in current.get("rivers", {}).items():
        if value.get("observed"):
            category = "" if value["category"] in ("undefined", "below action") else f" | {value['category']} stage"
            lines.append(f"{RIVERS[gauge]}: {value['cfs']:,} cfs observed {stamp(value['time'])}{category}; no current NWS forecast")
            continue
        suffix = "forecast maximum" if value["at_horizon"] else "forecast peak"
        category = "" if value["category"] in ("undefined", "below action") else f" | {value['category']} stage"
        lines.append(f"{RIVERS[gauge]}: {value['cfs']:,} cfs {stamp(value['time'])}{category} ({suffix})")
    lines.append("All times Mountain. River values are forecast peaks, not observations.")
    if current.get("rivers"):
        forecasts = [v for v in current["rivers"].values() if not v.get("observed")]
        if forecasts:
            issue = max(v["issued"] for v in forecasts)
            lines.append("Latest NOAA river issue: " + dt(issue).astimezone(LOCAL).strftime("%a %-m/%-d %-I:%M %p"))
    if current.get("timing"):
        lines.append("NWS timing = sustained ≥50% precipitation chance; not exact onset/end.")
    if current.get("errors"):
        lines.append("Some official data unavailable; prior risk retained. See run log.")
    return lines
