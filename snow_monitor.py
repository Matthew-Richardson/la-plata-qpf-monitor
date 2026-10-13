"""NOAA WPC Day 1-3 snowfall probabilities by La Plata County elevation band."""
import io,json,math
from pathlib import Path
import numpy as np
from PIL import Image
import requests
from shapely import contains_xy
from shapely.geometry import shape
from qpf_monitor import county, features

SNOW="https://mapservices.weather.noaa.gov/vector/rest/services/precip/wpc_prob_winter_precip/MapServer"
ELEVATION="https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer"
LAYERS={"Day 1":{4:1,8:2,12:3},"Day 2":{4:6,8:7,12:8},"Day 3":{4:11,8:12,12:13}}
PROBS={"Slight (10-39%)":10,"Moderate (40-69%)":40,"High (70-100%)":70}
BANDS=(("Below 7,000 ft",None,7000),("7,000–9,000 ft",7000,9000),("Above 9,000 ft",9000,None))

def elevation_grid(geom,session=requests):
    """Download a compact USGS 3DEP elevation raster covering the county."""
    bbox=",".join(str(n) for n in geom.bounds)
    response=session.get(ELEVATION+"/exportImage",params={
        "bbox":bbox,"bboxSR":4326,"imageSR":4326,"size":"384,384",
        "format":"tiff","pixelType":"F32","noData":-9999,"f":"json"},timeout=90)
    response.raise_for_status(); doc=response.json()
    if "error" in doc: raise RuntimeError(str(doc["error"]))
    raster=session.get(doc["href"],timeout=90); raster.raise_for_status()
    values=np.asarray(Image.open(io.BytesIO(raster.content)),dtype=float)
    extent=doc["extent"]; height,width=values.shape
    xs=np.linspace(extent["xmin"],extent["xmax"],width,endpoint=False)+(extent["xmax"]-extent["xmin"])/(2*width)
    ys=np.linspace(extent["ymax"],extent["ymin"],height,endpoint=False)-(extent["ymax"]-extent["ymin"])/(2*height)
    xx,yy=np.meshgrid(xs,ys)
    valid=np.isfinite(values) & (values>-1000) & contains_xy(geom,xx,yy)
    if not valid.any(): raise RuntimeError("USGS 3DEP returned no elevation pixels inside La Plata County")
    return xx,yy,values*3.28084,valid

def elevation_rows(polygons,grid):
    """Return strongest WPC category with meaningful coverage in each band."""
    xx,yy,elev_ft,county_mask=grid
    result={label:[] for label,_,_ in BANDS}
    for label,low,high in BANDS:
        band=county_mask
        if low is not None: band=band & (elev_ft>=low)
        if high is not None: band=band & (elev_ft<high)
        band_count=int(band.sum())
        minimum=max(4,math.ceil(band_count*0.005))
        strongest={}
        for polygon,row in polygons:
            overlap=band & contains_xy(polygon,xx,yy)
            if int(overlap.sum())>=minimum:
                threshold=row["threshold_in"]
                if row["minimum_probability_pct"]>strongest.get(threshold,0): strongest[threshold]=row["minimum_probability_pct"]
        result[label]=[{"threshold_in":threshold,"minimum_probability_pct":pct}
                       for threshold,pct in sorted(strongest.items())]
    return result

def analyze(geom,query=features,elevation=elevation_grid):
    bbox=",".join(str(n) for n in geom.bounds)
    periods={}; polygons_by_day={}
    for day,layers in LAYERS.items():
        observations=[]; polygons=[]
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
                if category not in PROBS: raise ValueError(f"Unexpected NOAA snow probability class: {category!r}")
                row={"threshold_in":inches,"category":category,"minimum_probability_pct":PROBS[category]}
                observations.append(row); polygons.append((g,row))
        periods[day]=sorted(observations,key=lambda r:(r["threshold_in"],r["minimum_probability_pct"]),reverse=True)
        polygons_by_day[day]=polygons
    active=any(periods.values())
    bands={}
    if active:
        grid=elevation(geom)
        bands={day:elevation_rows(polygons,grid) for day,polygons in polygons_by_day.items()}
    return {"source":SNOW,"elevation_source":ELEVATION,
            "scope":"WPC probability polygons summarized by USGS 3DEP elevation band within La Plata County",
            "periods":periods,"elevation_bands":bands,"active":active}

def main():
    file=Path("data/latest.json"); report=json.loads(file.read_text())
    snow=analyze(county()); report["snow"]=snow
    file.write_text(json.dumps(report,indent=2)+"\n")
    md=Path("data/latest.md")
    lines=["","## WPC Day 1–3 snowfall probability by elevation",""]
    if snow["active"]:
        for day,bands in snow["elevation_bands"].items():
            for label,rows in bands.items():
                if rows: lines.append(f"- {day}, {label}: "+", ".join(f"≥{r['threshold_in']} in at ≥{r['minimum_probability_pct']}%" for r in rows))
    else: lines.append("No WPC ≥10% probability polygon for ≥4 in snowfall intersects the county in Days 1–3. Lighter snow remains possible.")
    md.write_text(md.read_text().rstrip()+"\n"+"\n".join(lines)+"\n")

if __name__=="__main__":main()
