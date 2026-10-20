"""GroupMe QPF: notify changes during wet patterns, daily unchanged, quiet when dry."""
import argparse,json,os,re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from operational_monitor import collect, compare, delivered_baseline, message_lines

WATCHED={"Vallecito":"1408010112","Upper Pine":"1408010111","Upper Florida":"1408010407","Animas Canyon":"1408010403","La Plata":"1408010501"}
SOURCE="https://github.com/Matthew-Richardson/la-plata-qpf-monitor/blob/main/data/latest.md"
# Keep the new operational addendum in preview until its wording is approved.
OPERATIONAL_PREVIEW=True

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
        elif 6<=now.hour<12 and state.get("unchanged_day")!=now.date().isoformat():
            reason="No material changes"
    elif previous=="wet": reason="Returned to dry pattern; routine alerts paused"
    if force and not reason:
        reason="Manual status: "+("No material changes" if status=="wet" else "Dry pattern; monitoring continues")
    next_state={**state,"status":status,"dry_streak":streak,
                "snow_signature":[list(x) for x in snow_signature],
                "snow_elevation_signature":[list(x) for x in elevation_signature]}
    if reason:
        next_state["last_sent_at"]=now.isoformat()
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
    if OPERATIONAL_PREVIEW and not args.preview:
        print("Operational delivery held for message review.")
        changes=[]
    if not 6<=now.hour<21:
        changes=[]
    if changes:
        reason=reason or "River / flood / timing change"
        new_state["last_sent_at"]=now.isoformat()
    text=None
    if reason:
        blocks=[]
        if reason!="River / flood / timing change": blocks.append(message(report,reason))
        if changes: blocks.append("\n".join(message_lines(operational,changes)))
        text="\n\n".join(blocks)
        if not OPERATIONAL_PREVIEW:
            new_state["operational"]=delivered_baseline(operational,state.get("operational",{}))
    if args.preview:
        print(text or "Quiet: no update required.")
        return
    if reason:
        bot=os.environ.get("GROUPME_BOT_ID")
        if not bot: raise RuntimeError("Missing GROUPME_BOT_ID Actions secret")
        # GroupMe bot messages have a 1,000-character limit. Preserve whole lines.
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


