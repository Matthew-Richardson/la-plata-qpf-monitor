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
WATERSHEDS="https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/4"
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

def watershed_geometries(county_geom):
    """Full USGS HUC8 subbasins intersecting La Plata County, including upstream/out-of-county area."""
    xmin,ymin,xmax,ymax=county_geom.bounds
    fs=features(WATERSHEDS,{"where":"1=1","outFields":"huc8,name,states",
        "geometry":f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType":"esriGeometryEnvelope","inSR":4326,
        "spatialRel":"esriSpatialRelIntersects","outSR":4326,"returnGeometry":"true"})
    result={}
    for feature in fs:
        props=feature["properties"]
        boundary=shape(feature["geometry"])
        if not boundary.is_valid: boundary=boundary.buffer(0)
        if boundary.intersection(county_geom).area<=0: continue
        code=str(props.get("huc8") or "")
        if len(code)!=8 or not code.isdigit(): raise RuntimeError("Invalid USGS HUC8 identifier")
        if code in result: raise RuntimeError("Duplicate HUC8 identifier "+code)
        result[code]={"name":props.get("name") or code,"geometry":boundary}
    if not result: raise RuntimeError("No USGS watersheds intersect La Plata County")
    return result

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
    basins=watershed_geometries(geom)
    from shapely.ops import unary_union as union
    extent=union([geom]+[b["geometry"] for b in basins.values()])
    xmin,ymin,xmax,ymax=extent.bounds
    bbox=f"{xmin-0.05},{ymin-0.05},{xmax+0.05},{ymax+0.05}"
    output={}
    watershed_output={code:{"name":v["name"],"huc8":code,"periods":{}} for code,v in basins.items()}
    for label,layer in LAYERS.items():
        f=features(f"{WPC}/{layer}",{"where":"1=1","outFields":"qpf,units,issue_time,start_time,end_time",
               "geometry":bbox,"geometryType":"esriGeometryEnvelope","inSR":4326,
               "spatialRel":"esriSpatialRelIntersects","outSR":4326,"returnGeometry":"true"})
        output[label]=summarize(geom,f)
        for code,basin in basins.items():
            watershed_output[code]["periods"][label]=summarize(basin["geometry"],f)
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
    report={"watersheds":watershed_output,"watershed_source":WATERSHEDS,"watershed_level":"USGS HUC8 full subbasins intersecting La Plata County",
            "generated_utc":datetime.now(timezone.utc).isoformat(),"location":"La Plata County CO",
            "source":WPC,"periods":output,"caveats":"QPF polygon averages are forecast liquid-equivalent amounts, not observed rainfall or flood probabilities."}
    Path("data").mkdir(exist_ok=True)
    Path("data/latest.json").write_text(json.dumps(report,indent=2)+"\n")
    lines=["# La Plata County WPC QPF","",f"Generated UTC: {report['generated_utc']}","","| Period | Avg (in) | Min (in) | Max (in) | Coverage | WPC issue |","|---|---:|---:|---:|---:|---|"]
    for label,p in output.items():
        lines.append(f"| {label} | {p['average_in']:.2f} | {p['min_in']:.2f} | {p['max_in']:.2f} | {p['coverage_pct']:.1f}% | {p['issue_time']} |")
    lines.extend(["", "## Watershed QPF — full HUC8 subbasins", "",
          "These USGS subbasins intersect La Plata County but include their full mapped drainage area outside county lines.",
          "HUC8s are broad subbasins, not individual upstream gauge catchments.", "",
          "| Watershed | HUC8 | Days 1–7 avg (in) | Min (in) | Max (in) | Coverage |",
          "|---|---|---:|---:|---:|---:|"])
    for code, basin in sorted(watershed_output.items(),key=lambda x:x[1]["name"]):
        q=basin["periods"]["Days 1-7"]
        lines.append(f"| {basin['name']} | {code} | {q['average_in']:.2f} | {q['min_in']:.2f} | {q['max_in']:.2f} | {q['coverage_pct']:.1f}% |")
    lines.extend(["", "### Forecast-period breakdown by watershed", "",
                  "| Watershed | Day 1 | Day 2 | Day 3 | Days 4–5 | Days 6–7 | Days 1–7 |",
                  "|---|---:|---:|---:|---:|---:|---:|"])
    for code, basin in sorted(watershed_output.items(),key=lambda x:x[1]["name"]):
        p=basin["periods"]
        lines.append("| "+basin["name"]+" ("+code+") | "+" | ".join(f"{p[label]['average_in']:.2f}" for label in LAYERS)+" |")
    lines.extend(["",report["caveats"],"",f"Source: {WPC}",f"Watershed boundaries: {WATERSHEDS}"])
    Path("data/latest.md").write_text("\n".join(lines)+"\n")
    print("\n".join(lines))
if __name__=="__main__": run()
