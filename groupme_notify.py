"""GroupMe: morning/evening wet outlooks, followed by a separate river text."""
import argparse,json,os,re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from operational_monitor import collect, compare, delivered_baseline, OBSERVED_ONLY_RIVERS, RIVERS, LEVELS, stamp, dt

WATCHED={"Vallecito":"09352900","Los Pinos":"09352800","Lemon":"09362750","Animas":"09361500","La Plata":"09365500"}
EASTERN=("Vallecito","Los Pinos","Lemon","Animas")
SOURCE="https://github.com/Matthew-Richardson/la-plata-qpf-monitor/blob/main/data/latest.md"

def normalize_snow_signature(rows):
    """Collapse nested WPC categories to the strongest one per day/threshold."""
    strongest={}
    for day,threshold,pct in rows:
        strongest[(day,threshold)]=max(strongest.get((day,threshold),0),pct)
    return sorted((day,threshold,pct) for (day,threshold),pct in strongest.items())

def snow_summary(snow):
    """Return the strongest mapped probability for each day/threshold."""
    return normalize_snow_signature(
        (day,row["threshold_in"],row["minimum_probability_pct"])
        for day,rows in snow.get("periods",{}).items() for row in rows)

def snow_direction(previous,current):
    """Describe whether mapped snow probabilities moved up, down, or both."""
    old={tuple(row[:-1]):row[-1] for row in previous}
    new={tuple(row[:-1]):row[-1] for row in current}
    moves=[]
    for key in old.keys() | new.keys():
        before=old.get(key,0); after=new.get(key,0)
        if after!=before: moves.append(1 if after>before else -1)
    if moves and all(move>0 for move in moves): return "increased"
    if moves and all(move<0 for move in moves): return "decreased"
    return "mixed"

def snow_elevation_summary(snow):
    rows=[]
    for day,bands in snow.get("elevation_bands",{}).items():
        for label,values in bands.items():
            strongest={}
            for row in values:
                threshold=row["threshold_in"]
                strongest[threshold]=max(strongest.get(threshold,0),row["minimum_probability_pct"])
            rows.extend((day,label,threshold,pct) for threshold,pct in strongest.items())
    return sorted(rows)

def totals(r):
    return {"county":r["periods"]["Days 1-7"]["average_in"],
            **{name:r["gauge_catchments"][site]["periods"]["Days 1-7"]["average_in"] for name,site in WATCHED.items()}}

def essentially_no_moisture(report):
    """True only when the seven-day liquid/snow signal is negligible countywide."""
    values=totals(report)
    return (values["county"]<0.10
            and all(values[name]<0.25 for name in WATCHED)
            and not snow_summary(report.get("snow",{})))

def wet_slot(now):
    return now.date().isoformat()+(" AM" if now.hour<12 else " PM")
def decide(report,state,force=False,now=None):
    now=(now or datetime.now(ZoneInfo("America/Denver"))).astimezone(ZoneInfo("America/Denver"))
    # Enforce quiet hours even for delayed scheduled runs and manual --force.
    # Retain the last delivered baseline so the next daytime forecast can alert.
    if not 6<=now.hour<21:
        return None,dict(state)
    t=totals(report)
    snow=report.get("snow",{})
    snow_signature=snow_summary(snow)
    snow_active=bool(snow_signature)
    wet=t["county"]>=0.50 or any(t[n]>=1.00 for n in WATCHED) or snow_active
    dry=t["county"]<0.25 and all(t[n]<0.50 for n in WATCHED) and not snow_active
    streak=(state.get("dry_streak",0)+1) if dry else 0
    previous=state.get("status","dry")
    status="wet" if wet else ("dry" if previous=="wet" and streak>=2 else previous)
    schema=report.get("area_schema","huc10-v1")
    baseline=state.get("last_alert_totals") or {}
    if state.get("qpf_area_schema")!=schema:
        # Preserve a valid county-to-county comparison while suppressing false
        # watershed changes during the one-time HUC10-to-gauge migration.
        baseline={"county":baseline["county"]} if "county" in baseline else {}
    changes=[(n,t[n]-baseline[n]) for n in t if n in baseline and abs(t[n]-baseline[n])>=0.25]
    reason=None
    previous_snow=normalize_snow_signature(state.get("snow_signature",[]))
    elevation_signature=snow_elevation_summary(snow)
    previous_elevation=sorted(tuple(row) for row in state.get("snow_elevation_signature",[]))
    elevation_changed="snow_elevation_signature" in state and previous_elevation!=elevation_signature
    snow_changed=previous_snow!=snow_signature or elevation_changed
    if status=="wet":
        if previous!="wet": reason="Wet pattern detected"
        elif dry: reason=None  # wait for second dry forecast before declaring pattern over
        elif snow_changed:
            direction=(snow_direction(previous_snow,snow_signature) if previous_snow!=snow_signature
                       else snow_direction(previous_elevation,elevation_signature))
            reason=f"Snow guidance {direction}"
        elif changes:
            directions={"increased" if delta>0 else "decreased" for _,delta in changes}
            overall=next(iter(directions)) if len(directions)==1 else "mixed"
            details=", ".join(
                f"{'County' if name=='county' else name} "
                f"{'increased' if delta>0 else 'decreased'} {abs(delta):.2f}in"
                for name,delta in changes)
            reason=f"Material change — forecast {overall}: {details}"
        elif state.get("wet_update_slot")!=wet_slot(now):
            reason="No material changes"
    elif previous=="wet": reason="Returned to dry pattern; routine alerts paused"
    if force and not reason:
        reason="Manual status: "+("No material changes" if status=="wet" else "Dry pattern; monitoring continues")
    next_state={**state,"status":status,"dry_streak":streak,
                "qpf_area_schema":schema,
                "snow_signature":[list(x) for x in snow_signature],
                "snow_elevation_signature":[list(x) for x in elevation_signature]}
    if state.get("qpf_area_schema")!=schema and status=="wet":
        next_state["last_alert_totals"]=t
    if reason:
        next_state["last_sent_at"]=now.isoformat()
        if status=="wet": next_state["wet_update_slot"]=wet_slot(now)
        if "No material changes" in reason: next_state["unchanged_day"]=now.date().isoformat()
        next_state["last_alert_totals"]=t if status=="wet" else None
    return reason,next_state

def local_range(period):
    """Convert WPC UTC validity to La Plata County (America/Denver) time."""
    def convert(value):
        if isinstance(value, (int, float)):
            dt=datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), timezone.utc)
        else:
            dt=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Denver"))
    start,end=(convert(x) for x in period["valid_period"])
    a=start.strftime("%a %b %-d")
    b=end.strftime("%a %b %-d") if start.month!=end.month else end.strftime("%a %-d")
    return f"{a}–{b} ({start.strftime('%-I %p')}–{end.strftime('%-I %p')} local)"

def short_range(period,include_time=False):
    """Compact local date range for a phone notification."""
    def convert(value):
        if isinstance(value,(int,float)):
            dt=datetime.fromtimestamp(value/(1000 if value>1e11 else 1),timezone.utc)
        else:
            dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo("America/Denver"))
    start,end=(convert(x) for x in period["valid_period"])
    if include_time:
        return f"{start.strftime('%a %-m/%-d %-I %p')}–{end.strftime('%a %-m/%-d %-I %p')}"
    return f"{start.strftime('%a %-m/%-d')}–{end.strftime('%a %-m/%-d')}"

def alert_heading(reason):
    if reason.startswith("Material change — forecast increased:"): return "LPC precipitation | Rain increased"
    if reason.startswith("Material change — forecast decreased:"): return "LPC precipitation | Rain decreased"
    if reason.startswith("Material change — forecast mixed:"): return "LPC precipitation | Rain mixed"
    if reason=="Snow guidance increased": return "LPC precipitation | Snow increased"
    if reason=="Snow guidance decreased": return "LPC precipitation | Snow decreased"
    if reason=="Snow guidance mixed": return "LPC precipitation | Snow mixed"
    return "LPC precipitation | "+reason

def change_line(reason):
    if not reason.startswith("Material change —") or ":" not in reason: return None
    detail=reason.split(":",1)[1].strip()
    return "Change: "+re.sub(r"(?<=\d)in\b",'"',detail)

def chance_label(pct,threshold):
    chance=f"{pct}%+" if pct>=70 else f"{pct}–{39 if pct==10 else 69}%"
    return f"{chance} for {threshold}+\""

def message(report,reason):
    p=report["periods"];t=totals(report)
    day123=sum(p[k]["average_in"] for k in ("Day 1","Day 2","Day 3"))
    first={"valid_period": [p["Day 1"]["valid_period"][0],p["Day 3"]["valid_period"][1]]}
    lines=[alert_heading(reason)]
    detail=change_line(reason)
    if detail: lines.append(detail)
    lines += [
        "Rain | "+short_range(p["Days 1-7"],include_time=True),
        f"County 7-day avg: {t['county']:.2f}\" (range {p['Days 1-7']['min_in']:.2f}–{p['Days 1-7']['max_in']:.2f}\")",
        f"Days 1–3 ({short_range(first)}): {day123:.2f}\" | Days 4–5 ({short_range(p['Days 4-5'])}): {p['Days 4-5']['average_in']:.2f}\" | Days 6–7 ({short_range(p['Days 6-7'])}): {p['Days 6-7']['average_in']:.2f}\"",
        "Gauge basins, 7-day avg: "+" | ".join(f"{n} {t[n]:.2f}\"" for n in WATCHED)]
    snow=report.get("snow",{})
    if "periods" in snow:
        if snow.get("active"):
            bands=snow.get("elevation_bands",{})
            if bands:
                for day in snow["periods"]:
                    active=list(bands.get(day,{}).items())
                    if not any(rows for _,rows in active): continue
                    lines.append(f"Snow | {short_range(p[day])}")
                    for label,rows in active:
                        if not rows:
                            lines.append(label+': no mapped 10%+ chance of 4"+')
                            continue
                        strongest={}
                        for row in rows:
                            threshold=row["threshold_in"]
                            strongest[threshold]=max(strongest.get(threshold,0),row["minimum_probability_pct"])
                        lines.append(label+": "+"; ".join(chance_label(pct,threshold) for threshold,pct in sorted(strongest.items())))
            else:
                lines.append("Snow | strongest WPC chance in part of La Plata County")
                summary=snow_summary(snow)
                for day in snow["periods"]:
                    rows=[(threshold,pct) for d,threshold,pct in summary if d==day]
                    if rows: lines.append(f"{short_range(p[day])}: "+"; ".join(chance_label(pct,threshold) for threshold,pct in rows))
        else:
            lines.append("Snow | No mapped 10%+ chance of 4\"; lighter snow remains possible")
    return "\n".join(lines)

def active_outlook(operational,now):
    rows=[row for row in operational.get('outlooks',{}).values()
          if dt(row['end'])>now and row.get('level',0)>0]
    return max(rows,key=lambda row:(row['level'],-dt(row['start']).timestamp())) if rows else None

def timing_line(operational):
    windows=[value['window'] for value in operational.get('timing',{}).values() if value.get('window')]
    if not windows: return None
    start=min(dt(row['start']) for row in windows)
    end=max(dt(row['end']) for row in windows)
    return f"NWS likely precipitation: {stamp(start.isoformat())}–{stamp(end.isoformat())}"

def compact_snow_lines(report,reason):
    if not report.get('snow',{}).get('active'): return []
    text=message(report,reason).splitlines()
    start=next((i for i,line in enumerate(text) if line.startswith('Snow |')),None)
    return text[start:] if start is not None else []

def brief_mode(report,operational,reason,changes,now):
    """Choose dry, storm-tracking, or monitor detail."""
    if essentially_no_moisture(report):
        return 'dry'
    outlook=active_outlook(operational,now)
    river_hazard=any(value.get('category') in ('action','minor','moderate','major')
                     or value.get('forecast_hazard')
                     for value in operational.get('rivers',{}).values())
    significant_change=any(
        ('increased' in change and (' cfs' in change or 'WPC risk' in change))
        or '→action' in change or '→minor' in change or '→moderate' in change or '→major' in change
        for change in changes)
    reservoir_concern=any(value.get('percent_full',0)>=90 and value.get('inflow_cfs',0)>value.get('release_cfs',0)
                          for value in operational.get('reservoirs',{}).values())
    day123=sum(report['periods'][name]['average_in'] for name in ('Day 1','Day 2','Day 3'))
    material_increase=reason.startswith('Material change — forecast increased:')
    if ((outlook and outlook['level']>=3) or river_hazard or significant_change
            or reservoir_concern or material_increase or day123>=1.50):
        return 'monitor'
    return 'storm tracking'

def weather_brief(report,operational,reason,changes,now,mode=None):
    mode=mode or brief_mode(report,operational,reason,changes,now)
    periods=report['periods']
    period=periods['Days 1-7']
    day123=sum(periods[name]['average_in'] for name in ('Day 1','Day 2','Day 3'))
    first={'valid_period':[periods['Day 1']['valid_period'][0],periods['Day 3']['valid_period'][1]]}
    header=now.strftime('LPC WEATHER | %a %-m/%-d, %-I:%M %p')
    qpf=(f"Days 1–3 ({short_range(first)}): {day123:.2f}\" | "
         f"Days 4–5 ({short_range(periods['Days 4-5'])}): {periods['Days 4-5']['average_in']:.2f}\" | "
         f"Days 6–7 ({short_range(periods['Days 6-7'])}): {periods['Days 6-7']['average_in']:.2f}\"")
    outlook=active_outlook(operational,now)
    risk=(f"{LEVELS[outlook['level']]} excessive-rain risk in part of LPC: "
          f"{stamp(outlook['start'])}–{stamp(outlook['end'])}" if outlook else None)
    timing=timing_line(operational)
    weather_changes=[change for change in changes if change.startswith(('NWS timing','WPC risk'))]
    if mode=='dry':
        lines=[header,
               f"7-day outlook: Essentially dry | WPC county avg {period['average_in']:.2f}\" "
               f"(max {period['max_in']:.2f}\")"]
        if risk: lines.append(risk)
        lines.extend(weather_changes)
        return '\n'.join(lines)

    if mode=='storm tracking':
        lines=[header,qpf]
        if timing: lines.append(timing)
        if risk: lines.append(risk)
        snow=compact_snow_lines(report,reason)
        if snow: lines.extend(snow)
        lines.extend(weather_changes)
        return '\n'.join(lines)

    lines=[header,'MONITOR MODE']
    detail=change_line(reason)
    if detail: lines.extend(['',detail])
    if timing: lines.extend(['','TIMING',timing])
    lines.extend(['','RAINFALL',qpf,f"WPC county 7-day: {period['average_in']:.2f}\""])
    basin_values=totals(report)
    lines.extend(['','UPSTREAM BASINS — 7 DAY',
                  ' | '.join(f"{name} {basin_values[name]:.2f}\"" for name in WATCHED)])
    if risk or weather_changes:
        lines.extend(['','RISK'])
        if risk: lines.append(risk)
        lines.extend(weather_changes)
    snow=compact_snow_lines(report,reason)
    if snow: lines.extend(['','SNOW',*snow])
    return '\n'.join(lines)

def no_significant_rises(operational, now):
    """Require complete, fresh guidance; significant = >=20% and >=100 cfs."""
    rivers=operational.get('rivers',{})
    for gauge in RIVERS.keys() - OBSERVED_ONLY_RIVERS:
        v=rivers.get(gauge)
        if not v or v.get('observed') or v.get('baseline_cfs') is None:
            return False
        if not -21600 <= (dt(v['baseline_time'])-now).total_seconds() <= 21600:
            return False
        if not 0 <= (now-dt(v['issued'])).total_seconds() <= 36*3600:
            return False
        if v['cfs']-v['baseline_cfs'] >= max(100,v['baseline_cfs']*.20):
            return False
    # A flat hydrograph at action/flood stage still needs explicit detail.
    return not any(v.get('category') in ('action','minor','moderate','major')
                   or v.get('baseline_category') in ('action','minor','moderate','major')
                   or v.get('forecast_hazard',False) for v in rivers.values())

def river_line(name,v):
    labels={'action':'ACTION STAGE','minor':'MINOR FLOOD STAGE','moderate':'MODERATE FLOOD STAGE',
            'major':'MAJOR FLOOD STAGE','below action':'below action stage'}
    category=v['category']
    stage=f" / {v['stage_ft']:.2f} ft" if v.get('stage_ft') is not None else ''
    suffix=' — '+labels[category] if category in labels else ''
    time=dt(v['time']).astimezone(ZoneInfo('America/Denver')).strftime('%a %-m/%-d, %-I:%M %p')
    if v.get('observed'):
        return f"{name}: {v['cfs']:,} cfs observed {time}{stage}{suffix}; no current NWS forecast"
    label='max through ' if v['at_horizon'] else ''
    return f"{name}: {v['cfs']:,} cfs{stage} {label}{time}{suffix}"

def reservoir_line(value):
    return (f"{value['name']}: {value['storage_af']:,} AF ({value['percent_full']}%); "
            f"{value['headroom_af']:,} AF headroom | in {value['inflow_cfs']:,} / out {value['release_cfs']:,} cfs")

def river_brief(operational,changes,now,mode='routine'):
    quiet=no_significant_rises(operational,now)
    lines=[now.strftime('LPC RIVERS | %a %-m/%-d, %-I:%M %p')]
    if mode=='monitor': lines.extend(['','EXPECTED PEAKS'])
    elif quiet: lines.append('No significant rises expected in available NWS forecasts.')
    for gauge,name in RIVERS.items():
        v=operational.get('rivers',{}).get(gauge)
        if not v:
            lines.append(name+': unavailable')
            continue
        if mode=='monitor' and v.get('observed') and lines[-1]!='CURRENT':
            lines.extend(['','CURRENT'])
        lines.append(river_line(name,v))
    river_changes=[change for change in changes if not change.startswith(('NWS timing','WPC risk'))]
    reservoirs=operational.get('reservoirs',{})
    reservoir_concern=any(value.get('percent_full',0)>=90 and value.get('inflow_cfs',0)>value.get('release_cfs',0)
                          for value in reservoirs.values())
    if mode=='monitor' and river_changes:
        lines.extend(['','CHANGES',*river_changes])
    if reservoirs and (mode=='monitor' or reservoir_concern):
        latest=max(dt(value['time']) for value in reservoirs.values()).astimezone(ZoneInfo('America/Denver'))
        lines.extend(['',latest.strftime('RESERVOIRS | USBR %a %-m/%-d')])
        lines.extend(reservoir_line(reservoirs[key]) for key in ('vallecito','lemon') if key in reservoirs)
    values=[v for v in operational.get('rivers',{}).values() if not v.get('observed')]
    if values:
        issued=max(dt(v['issued']) for v in values).astimezone(ZoneInfo('America/Denver'))
        lines.append(issued.strftime('NWS issued %a %-m/%-d, %-I:%M %p %Z.'))
    return '\n'.join(lines)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--force",action="store_true")
    parser.add_argument("--current",default="data/latest.json")
    parser.add_argument("--state",default="data/notification_state.json")
    parser.add_argument("--preview",action="store_true",help="Fetch and evaluate without sending or saving delivery state")
    args=parser.parse_args()
    report=json.loads(Path(args.current).read_text())
    state_path=Path(args.state)
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    now=datetime.now(ZoneInfo("America/Denver"))
    operational=collect(now)
    for error in operational["errors"]: print("Official source unavailable:",error)
    Path("data/operational_latest.json").write_text(json.dumps(operational,indent=2)+"\n")
    reason,new_state=decide(report,state,force=args.force,now=now)
    changes=compare(operational,state.get("operational",{}),now)
    if not 6<=now.hour<21:
        changes=[]
    if changes:
        reason=reason or "River / flood / timing change"
        new_state["last_sent_at"]=now.isoformat()
    texts=[]
    if reason:
        mode=brief_mode(report,operational,reason,changes,now)
        texts=[weather_brief(report,operational,reason,changes,now,mode)]
        if mode!='dry' or changes:
            texts.append(river_brief(operational,changes,now,mode))
        new_state["operational"]=delivered_baseline(operational,state.get("operational",{}))
        if new_state.get('status')=='wet':
            new_state['wet_update_slot']=wet_slot(now)
            new_state['last_alert_totals']=totals(report)
    if args.preview:
        print('\n\n--- Separate GroupMe text ---\n\n'.join(texts) or "Quiet: no update required.")
        return
    if reason:
        bot=os.environ.get("GROUPME_BOT_ID")
        if not bot: raise RuntimeError("Missing GROUPME_BOT_ID Actions secret")
        # GroupMe bot messages have a 1,000-character limit. Preserve whole lines.
        for text in texts:
            for part in message_parts(text):
                response=requests.post("https://api.groupme.com/v3/bots/post",
                                       json={"bot_id":bot,"text":part},timeout=30)
                response.raise_for_status()
                print("GroupMe HTTP",response.status_code,reason)
    else: print("Quiet: no update required.")
    state_path.parent.mkdir(parents=True,exist_ok=True)
    state_path.write_text(json.dumps(new_state,indent=2)+"\n")

def message_parts(text,limit=950):
    parts=[]; current=""
    for line in text.splitlines():
        while len(line)>limit:
            if current: parts.append(current); current=""
            parts.append(line[:limit]); line=line[limit:]
        candidate=current+"\n"+line if current else line
        if len(candidate)>limit:
            parts.append(current); current=line
        else: current=candidate
    if current: parts.append(current)
    return parts
if __name__=="__main__": main()
