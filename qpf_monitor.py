"""Area-weighted La Plata County WPC quantitative precipitation forecasts."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests
from shapely.geometry import shape
from shapely.ops import transform, unary_union
from pyproj import Transformer

WPC="https://mapservices.weather.noaa.gov/vector/rest/services/precip/wpc_qpf/MapServer"
COUNTY="https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/27"
LAYERS={"Day 1":1,"Day 2":2,"Day 3":3,"Days 4-5":4,"Days 6-7":5,"Days 1-7":11}
PROJECT=Transformer.from_crs("EPSG:4326","EPSG:5070",always_xy=True).transform
SESSION=requests.Session()
SESSION.headers["User-Agent"]="LaPlata-QPF-Monitor/1.0"

def fetch(url,params):
    r=SESSION.get(url,params=params,timeout=90)
    r.raise_for_status()
    obj=r.json()
    if "error" in obj: raise RuntimeError(str(obj["error"]))
    return obj

def features(url,params):
    result=[]
    for offset in range(0,20000,500):
        q={**params,"f":"geojson","resultOffset":offset,"resultRecordCount":500}
        doc=fetch(url+"/query",q)
        if doc.get("type")!="FeatureCollection": raise RuntimeError("Expected GeoJSON from "+url)
        chunk=doc.get("features",[])
        result.extend(chunk)
        if len(chunk)<500 and not doc.get("exceededTransferLimit"): return result
        if not chunk: return result
    raise RuntimeError("Pagination limit reached "+url)

def county():
    fs=features(COUNTY,{"where":"GEOID='08067'","outFields":"GEOID,NAME","outSR":4326,"returnGeometry":"true"})
    if len(fs)!=1: raise RuntimeError("Expected one county, got "+str(len(fs)))
    return shape(fs[0]["geometry"])

def summarize(region,fs):
    region=transform(PROJECT,region)
    parts=[]; amounts=[]; issue=set(); times=set()
    for f in fs:
        p=f["properties"]
        if p.get("qpf") is None: continue
        if str(p.get("units","inches")).lower() not in ("in","inch","inches",""): raise RuntimeError("Unknown QPF units "+str(p.get("units")))
        g=shape(f["geometry"])
        if not g.is_valid: g=g.buffer(0)
        if g.is_empty: continue
        clip=transform(PROJECT,g).intersection(region)
        if clip.area>0:
            parts.append((clip,float(p["qpf"])))
            if p.get("issue_time"): issue.add(str(p["issue_time"]))
            times.add((str(p.get("start_time")),str(p.get("end_time"))))
    if not parts:
        return {"average_in":0.0,"min_in":0.0,"max_in":0.0,"coverage_pct":0.0,
                "issue_time":None,"valid_period":None,
                "note":"No WPC mapped precipitation polygons intersect the county; below display threshold, not necessarily exactly zero."}
    if len(issue)>1 or len(times)>1: raise RuntimeError("Mixed forecast issue/valid periods: "+str((issue,times)))
    # Disallow overlapping polygon classes; no double counting.
    covered=unary_union([p[0] for p in parts])
    overlap=sum(p[0].area for p in parts)-covered.area
    if overlap>region.area*0.002: raise RuntimeError("Overlapping forecast polygons exceed tolerance")
    coverage=covered.area/region.area
    # WPC contours describe precipitation >= the first mapped amount; uncovered areas are below that contour.
    # Do not require polygons to tile the entire county. Treat uncovered area as 0 for a lower-bound mean.
    weighted=sum(g.area*v for g,v in parts)/region.area
    return {"average_in":round(weighted,3),"min_in":0.0 if coverage<0.999 else min(v for _,v in parts),"max_in":max(v for _,v in parts),
            "coverage_pct":round(coverage*100,2),"issue_time":next(iter(issue),None),
            "valid_period":next(iter(times),None),
            "note":"Areas outside WPC QPF contours are treated as zero; area average is a lower-bound approximation."}

def run():
    geom=county()
    xmin,ymin,xmax,ymax=geom.bounds
    bbox=f"{xmin-0.1},{ymin-0.1},{xmax+0.1},{ymax+0.1}"
    output={}
    for label,layer in LAYERS.items():
        f=features(f"{WPC}/{layer}",{"where":"1=1","outFields":"qpf,units,issue_time,start_time,end_time",
               "geometry":bbox,"geometryType":"esriGeometryEnvelope","inSR":4326,
               "spatialRel":"esriSpatialRelIntersects","outSR":4326,"returnGeometry":"true"})
        output[label]=summarize(geom,f)
        if not output[label]["issue_time"]:
            # Metadata query establishes product issuance even if no mapped polygon reaches the county.
            doc=fetch(f"{WPC}/{layer}/query",{"f":"json","where":"1=1",
                     "outFields":"issue_time,start_time,end_time","returnGeometry":"false",
                     "resultRecordCount":1})
            meta=doc.get("features",[])
            if not meta: raise RuntimeError(f"WPC layer {layer} returned no issuance metadata")
            attr=meta[0]["attributes"]
            output[label]["issue_time"]=attr.get("issue_time")
            output[label]["valid_period"]=[attr.get("start_time"),attr.get("end_time")]
    report={"generated_utc":datetime.now(timezone.utc).isoformat(),"location":"La Plata County CO",
            "source":WPC,"periods":output,"caveats":"QPF polygon averages are forecast liquid-equivalent amounts, not observed rainfall or flood probabilities."}
    Path("data").mkdir(exist_ok=True)
    Path("data/latest.json").write_text(json.dumps(report,indent=2)+"\n")
    lines=["# La Plata County WPC QPF","",f"Generated UTC: {report['generated_utc']}","","| Period | Avg (in) | Min (in) | Max (in) | Coverage | WPC issue |","|---|---:|---:|---:|---:|---|"]
    for label,p in output.items():
        lines.append(f"| {label} | {p['average_in']:.2f} | {p['min_in']:.2f} | {p['max_in']:.2f} | {p['coverage_pct']:.1f}% | {p['issue_time']} |")
    lines.extend(["",report["caveats"],"",f"Source: {WPC}"])
    Path("data/latest.md").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))
if __name__=="__main__": run()
