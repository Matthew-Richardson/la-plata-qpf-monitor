"""GroupMe: morning/evening wet outlooks, followed by a separate river text."""
import argparse,json,os,re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from operational_monitor import collect, compare, delivered_baseline, RIVERS, LEVELS, stamp, dt

WATCHED={"Vallecito":"1408010112","Upper Pine":"1408010111","Upper Florida":"1408010407","Animas Canyon":"1408010403","La Plata":"1408010501"}
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
            **{name:r["huc10_watersheds"][huc]["periods"]["Days 1-7"]["average_in"] for name,huc in WATCHED.items()}}

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
    changes=[(n,t[n]-state["last_alert_totals"].get(n,t[n]))
             for n in t if state.get("last_alert_totals") and
             abs(t[n]-state["last_alert_totals"].get(n,t[n]))>=0.25]
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
                "snow_signature":[list(x) for x in snow_signature],
                "snow_elevation_signature":[list(x) for x in elevation_signature]}
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
        f"Days 1–3 ({short_range(first)}): {day123:.2f}\" | Days 4–5: {p['Days 4-5']['average_in']:.2f}\" | Days 6–7: {p['Days 6-7']['average_in']:.2f}\"",
        "Areas, 7-day avg: "+" | ".join(f"{n} {t[n]:.2f}\"" for n in WATCHED)]
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

def weather_brief(report,operational,reason,changes,now):
    p=report['periods']; overall=p['Days 1-7']
    lines=[now.strftime('LPC Weather | %a %-m/%-d %-I:%M %p %Z')]
    weather_changes=[c for c in changes if ('NWS timing:' in c or 'window added' in c or 'window removed' in c
                     or ('WPC risk' in c and 'new monitored period' not in c))]
    if reason.startswith('Material change'):
        lines.append(change_line(reason))
    elif reason.startswith('Snow guidance'):
        lines.append(reason)
    elif reason=='Wet pattern detected':
        lines.append('Wet period detected.')
    elif not changes:
        lines.append('No material changes.')
    elif not weather_changes:
        lines.append('Weather outlook unchanged; river update follows.')
    if any('NWS timing:' in c for c in weather_changes):lines.append('NWS timing changed by 6+ hours; revised windows below.')
    lines.extend(c.split(' | ',1)[0] for c in weather_changes if 'WPC risk' in c)
    windows={}
    for town,entry in operational.get('timing',{}).items():
        w=entry.get('window')
        if w: windows.setdefault((w['start'],w['end'],w['open_end']),[]).append(town)
    for (start,end,open_end),towns in windows.items():
        lines.append('/'.join(towns)+': likely precipitation '+stamp(start)+' to '+stamp(end)
                     +(' or later' if open_end else ''))
    lines.append(f"County 7-day liquid total: avg {overall['average_in']:.2f}\"; range {overall['min_in']:g}–{overall['max_in']:g}\".")
    # This is the wettest forecast accumulation period, not a peak rainfall rate.
    names=['Day 1','Day 2','Day 3','Days 4-5','Days 6-7']
    def rate(name):
        v=p[name];hours=(dt(v['valid_period'][1].replace(' ','T')+'Z')-dt(v['valid_period'][0].replace(' ','T')+'Z')).total_seconds()/3600
        return v['average_in']/max(1,hours)
    peak=max(names,key=rate)
    if p[peak]['average_in']>=.25:
        v=p[peak]
        lines.append('Wettest QPF period: '+short_range(v,include_time=True)+f"; avg {v['average_in']:.2f}\".")
    risks={}
    for v in operational.get('outlooks',{}).values():
        if v['level'] and dt(v['end'])>now:
            risks.setdefault(v['level'],[]).append(v)
    for level,values in sorted(risks.items(),reverse=True):
        lines.append(f"WPC {LEVELS[level]} Risk in part of LPC: "+stamp(min(v['start'] for v in values))+' to '+stamp(max(v['end'] for v in values)))
    if report.get('snow',{}).get('active'):
        snow_lines=message(report,reason).splitlines()
        index=next((i for i,line in enumerate(snow_lines) if line.startswith('Snow |')),None)
        if index is not None:lines.extend(snow_lines[index:])
    if not windows:lines.append('NWS likely-precipitation timing unavailable or no sustained likely window.')
    lines.append('Liquid totals include rain and melted snow. Times Mountain.')
    if operational.get('errors'):lines.append('Some guidance unavailable; no reduction in risk inferred.')
    return '\n'.join(lines)

def river_brief(operational,changes,now):
    lines=[now.strftime('LPC River Forecasts | %a %-m/%-d %-I:%M %p %Z')]
    river_changes=[c for c in changes if c.startswith(tuple(RIVERS.values()))]
    initial='River forecasts added to monitoring' in changes
    lines.extend(river_changes or [('Current NOAA forecast peaks.' if initial else 'No material river forecast changes.') if operational.get('rivers') else 'River forecasts unavailable.'])
    for gauge,name in RIVERS.items():
        v=operational.get('rivers',{}).get(gauge)
        if not v:
            lines.append(name+': current forecast unavailable.')
            continue
        label='maximum through' if v['at_horizon'] else 'peak'
        labels={'action':'ACTION STAGE','minor':'MINOR FLOOD STAGE','moderate':'MODERATE FLOOD STAGE',
                'major':'MAJOR FLOOD STAGE','below action':'below action stage','undefined':'stage thresholds not defined'}
        category='; '+labels[v['category']]
        if v.get('stage_ft') is not None:category=f"; {v['stage_ft']:.2f} ft"+category
        lines.append(f"{name}: {v['cfs']:,} cfs {label} {stamp(v['time'])}{category}")
    values=operational.get('rivers',{}).values()
    if values:
        issued=max(dt(v['issued']) for v in values).astimezone(ZoneInfo('America/Denver'))
        lines.append(issued.strftime('Latest NOAA issue: %a %-m/%-d %-I:%M %p %Z.'))
    lines.append('Forecasts, not observations. All times Mountain; peaks may change.')
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
        texts=[weather_brief(report,operational,reason,changes,now)]
        if new_state.get('status')=='wet' or changes:
            texts.append(river_brief(operational,changes,now))
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


