import json, math, io, pathlib, urllib.request, concurrent.futures, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
from matplotlib.patches import Rectangle
import matplotlib.patheffects as pe

ROOT=pathlib.Path(sys.argv[1]).resolve()
ASSETS=pathlib.Path(__file__).resolve().parents[1]/'assets'/'rrfs'
m=json.loads((ROOT/'manifest.json').read_text()); grid=json.loads((ROOT/'grid.json').read_text())
county=json.loads((ASSETS/'county.geojson').read_text()); basins=json.loads((ASSETS/'watersheds.geojson').read_text()); weights=json.loads((ASSETS/'watershed-weights.json').read_text())
# Identical geographic bounds and scale on both panels; include the upstream inflow labels.
W,S,E,N=-108.43,36.96,-107.25,38.00
R=6378137.0
def xy(lon,lat):return R*math.radians(lon),R*math.log(math.tan(math.pi/4+math.radians(lat)/2))
x0,y0=xy(W,S);x1,y1=xy(E,N)
z=10;n=2**z;tilem=2*math.pi*R/n
left=math.floor((x0+math.pi*R)/tilem);right=math.floor((x1+math.pi*R)/tilem)
top=math.floor((math.pi*R-y1)/tilem);bottom=math.floor((math.pi*R-y0)/tilem)
cache=ROOT/'tiles';cache.mkdir(exist_ok=True)
def tile(pair):
 x,y=pair;p=cache/f'{z}-{x}-{y}.jpg'
 if not p.exists():
  url=f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}'
  req=urllib.request.Request(url,headers={'User-Agent':'LPC forecast comparison preview'})
  p.write_bytes(urllib.request.urlopen(req,timeout=30).read())
 return x,y,Image.open(p).convert('RGB')
canvas=Image.new('RGB',((right-left+1)*256,(bottom-top+1)*256),'white')
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 for x,y,im in pool.map(tile,[(x,y) for y in range(top,bottom+1) for x in range(left,right+1)]):canvas.paste(im,((x-left)*256,(y-top)*256))
tile_extent=[left*tilem-math.pi*R,(right+1)*tilem-math.pi*R,math.pi*R-(bottom+1)*tilem,math.pi*R-top*tilem]

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11})
fig=plt.figure(figsize=(16,11.0),dpi=125,facecolor='white')
fig.text(.5,.97,'La Plata County — RRFS forecast',ha='center',va='top',fontsize=17)
local=ZoneInfo('America/Denver');start=datetime.fromisoformat(m['initialized']).astimezone(local);end=datetime.fromisoformat(m['frames'][-1]['valid']).astimezone(local)
def stamp(dt):return dt.strftime('%a, %b %d, %I:%M %p %Z').replace(' 0',' ')
fig.text(.5,.925,f'Model start: {stamp(start)} ({m["initialized"][11:13]}Z)',ha='center',fontsize=12)
fig.text(.5,.891,f'Forecast through: {stamp(end)}   |   {m["completeThroughHour"]} hours from model start',ha='center',fontsize=12)
fig.text(.5,.858,'Accumulated totals for this entire period, in inches',ha='center',fontsize=11)
levels=m['products']['qpf']['levels'];colors=m['products']['qpf']['colors'];rgba=[]
for h in colors:rgba.append(tuple(int(h[i:i+2],16)/255 for i in [1,3,5]) + (.68,))
cmap=ListedColormap(rgba);cmap.set_under((0,0,0,0));norm=BoundaryNorm(levels+[100],cmap.N)
rows,cols=m['grid']['height'],m['grid']['width'];extent=m['grid']['extent3857'];model_extent=[extent[0],extent[2],extent[1],extent[3]]

def rings(geometry):
 if geometry['type']=='Polygon':return geometry['coordinates']
 if geometry['type']=='MultiPolygon':return [ring for polygon in geometry['coordinates'] for ring in polygon]
 return []

def draw(ax,product,title):
 ax.imshow(canvas,extent=tile_extent,origin='upper',zorder=0)
 values=np.array(grid[product]).reshape(rows,cols)
 ax.imshow(values,extent=model_extent,origin='upper',cmap=cmap,norm=norm,interpolation='nearest',zorder=1)
 for f in basins['features']:
  for ring in rings(f['geometry']):
   coords=np.array([xy(*p[:2]) for p in ring]);ax.plot(coords[:,0],coords[:,1],color='#555555',linewidth=.65,linestyle='--' if f['properties']['approximate'] else '-',alpha=.9,zorder=2)
 for f in county['features']:
  for ring in rings(f['geometry']):
   coords=np.array([xy(*p[:2]) for p in ring]);ax.plot(coords[:,0],coords[:,1],color='black',linewidth=1.2,zorder=3)
 # Plain, compact watershed names and area-average values; no boxes.
 for b in weights['basins']:
  f=next(f for f in basins['features'] if f['properties']['id']==b['id']);p=f['properties']
  average=sum(grid[product][i]*w for i,w in b['samples'])/sum(w for i,w in b['samples'])
  lat,lon=b['anchor'];xx,yy=xy(lon,lat)
  name=p['shortName'].replace(' · Lemon',' / Lemon').replace('La Plata / Hesperus','La Plata')+('*' if p['approximate'] else '')
  text=ax.text(xx,yy,f'{name}\n{average:.2f} in',ha='center',va='center',fontsize=8,color='#111111',zorder=5,linespacing=1.15)
  text.set_path_effects([pe.withStroke(linewidth=2,foreground='white',alpha=.95)])
 # Town totals sample the displayed model cell at each town center.
 for name,lat,lon in [('Durango',37.2753,-107.8801),('Bayfield',37.2256,-107.5981),('Ignacio',37.1150,-107.6330)]:
  xx,yy=xy(lon,lat)
  col=int((xx-extent[0])/(extent[2]-extent[0])*cols)
  row=int((extent[3]-yy)/(extent[3]-extent[1])*rows)
  assert 0<=row<rows and 0<=col<cols
  value=values[row,col]
  ax.plot(xx,yy,'o',markersize=3,color='black',markeredgecolor='white',markeredgewidth=.5,zorder=6)
  ax.annotate(f'{name}\n{value:.2f} in',(xx,yy),xytext=(5,-6),textcoords='offset points',
              ha='left',va='top',fontsize=8,fontweight='bold',color='black',zorder=6,
              path_effects=[pe.withStroke(linewidth=2,foreground='white',alpha=.95)])
  print(product,name,f'{value:.2f} in')
 ax.set_xlim(x0,x1);ax.set_ylim(y0,y1);ax.set_aspect('equal');ax.set_xticks([]);ax.set_yticks([])
 for spine in ax.spines.values():spine.set_linewidth(.6);spine.set_color('#777777')
 ax.set_title(title,fontsize=13,pad=9)
 # Same simple scale on both maps.
 bx=x0+.045*(x1-x0);by=y0+.04*(y1-y0);length=10*1609.344/math.cos(math.radians(37.35))
 ax.plot([bx,bx+length],[by,by],color='black',linewidth=2,zorder=7)
 ax.text(bx+length/2,by+.014*(y1-y0),'10 miles',ha='center',fontsize=8,zorder=7,path_effects=[pe.withStroke(linewidth=2,foreground='white')])

ax1=fig.add_axes([.025,.208,.466,.595]);ax2=fig.add_axes([.509,.208,.466,.595])
draw(ax1,'qpf','Total precipitation (rain + snow water equivalent)')
draw(ax2,'rain','Estimated rainfall (snow removed)')
# One shared discrete legend: identical colors and thresholds for both maps.
legend=fig.add_axes([.13,.129,.74,.025]);legend.set_xlim(0,len(colors));legend.set_ylim(0,1);legend.axis('off')
for i,c in enumerate(colors):
 legend.add_patch(Rectangle((i,0),1,1,facecolor=c,alpha=.68,edgecolor='none'))
 legend.text(i+.5,-.48,f'{levels[i]:g}'+('+' if i==len(colors)-1 else ''),ha='center',va='top',fontsize=9)
fig.text(.5,.174,'Shared color scale — inches',ha='center',fontsize=11)
fig.text(.5,.075,'Watersheds: area averages. Towns (dots): model value at town center. Unshaded: less than 0.01 inch.',ha='center',fontsize=10)
fig.text(.5,.047,'Rainfall estimate removes snow water equivalent; freezing rain or mixed precipitation may remain. *Florida boundary is approximate.',ha='center',fontsize=9)
fig.text(.5,.021,'Source: NOAA RRFS 3 km. Basemap: Esri, USGS and contributors. County: La Plata County GIS. Forecast guidance, not a warning.',ha='center',fontsize=8,color='#333333')
path=ROOT/'LPC_RRFS_precip_vs_rain_preview.png';fig.savefig(path,dpi=125,facecolor='white');print(path)
