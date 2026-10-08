"""NOAA WPC Day 1-3 snowfall probability zones intersecting La Plata County.

The polygons are threshold *probability categories*, not deterministic
snowfall amounts.  A small overlap does not represent the whole county.
"""
import json
from pathlib import Path
from shapely.geometry import shape
from qpf_monitor import county, features

SNOW="https://mapservices.weather.noaa.gov/vector/rest/services/precip/wpc_prob_winter_precip/MapServer"
LAYERS={
    "Day 1":{4:1,8:2,12:3},
    "Day 2":{4:6,8:7,12:8},
    "Day 3":{4:11,8:12,12:13},
}
PROBS={"Slight (10-39%)":10,"Moderate (40-69%)":40,"High (70-100%)":70}
def analyze(geom, query=features):
    bounds=geom.bounds
    bbox=",".join(str(n) for n in bounds)
    periods={}
    for day,layers in LAYERS.items():
        observations=[]
        for inches,layer in layers.items():
            fs=query(f"{SNOW}/{layer}",{
                "where":"1=1","outFields":"outlook,issue_time,start_time,end_time",
                "geometry":bbox,"geometryType":"esriGeometryEnvelope","inSR":4326,
                "spatialRel":"esriSpatialRelIntersects","outSR":4326,"returnGeometry":"true"})
            for ft in fs:
                g=shape(ft["geometry"])
                if not g.is_valid:g=g.buffer(0)
                if g.is_empty or not g.intersects(geom):continue
                category=ft["properties"].get("outlook")
                if category not in PROBS:
                    raise ValueError(f"Unexpected NOAA snow probability class: {category!r}")
                observations.append({"threshold_in":inches,"category":category,
                                     "minimum_probability_pct":PROBS[category]})
        periods[day]=sorted(observations,key=lambda r:(r["threshold_in"],r["minimum_probability_pct"]),reverse=True)
    return {"source":SNOW,"scope":"Any intersecting part of La Plata County; not area-wide probabilities",
            "periods":periods,"active":any(periods.values())}

def main():
    file=Path("data/latest.json")
    report=json.loads(file.read_text())
    snow=analyze(county())
    report["snow"]=snow
    file.write_text(json.dumps(report,indent=2)+"\n")
    md=Path("data/latest.md")
    lines=["","## WPC Day 1–3 snowfall probability (La Plata County)","",
           "Listed categories indicate that **some portion** of the county intersects a WPC probability polygon. These are not countywide snowfall forecasts."]
    if snow["active"]:
        for day,rows in snow["periods"].items():
            if not rows:continue
            observed=sorted({(r["threshold_in"],r["minimum_probability_pct"]) for r in rows})
            lines.append(f"- {day}: "+", ".join(f"≥{inch} in snow at ≥{pct}% probability (somewhere in county)" for inch,pct in observed))
    else:
        lines.append("No WPC ≥10% probability polygon for ≥4 in snowfall intersects the county in Days 1–3. This does not rule out lighter snow.")
    md.write_text(md.read_text().rstrip()+"\n"+"\n".join(lines)+"\n")

if __name__=="__main__":main()
