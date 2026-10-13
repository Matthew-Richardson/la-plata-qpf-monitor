"""GroupMe QPF: notify changes during wet patterns, daily unchanged, quiet when dry."""
import argparse,json,os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

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
    snow_changed=previous_snow!=snow_signature
    if status=="wet":
        if previous!="wet": reason="Wet pattern detected"
        elif dry: reason=None  # wait for second dry forecast before declaring pattern over
        elif snow_changed: reason="Snowfall probability guidance changed"
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
    next_state={**state,"status":status,"dry_streak":streak,"snow_signature":[list(x) for x in snow_signature]}
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

def message(report,reason):
    p=report["periods"];t=totals(report)
    day123=sum(p[k]["average_in"] for k in ("Day 1","Day 2","Day 3"))
    first={"valid_period": [p["Day 1"]["valid_period"][0],p["Day 3"]["valid_period"][1]]}
    lines=[
        "LPC QPF | "+reason,
        "Valid: "+local_range(p["Days 1-7"]),
        f"7-day county avg {t['county']:.2f}in (range {p['Days 1-7']['min_in']:.2f}-{p['Days 1-7']['max_in']:.2f}in)",
        f"Days 1–3 | {local_range(first)}: {day123:.2f}in",
        f"Days 4–5 | {local_range(p['Days 4-5'])}: {p['Days 4-5']['average_in']:.2f}in",
        f"Days 6–7 | {local_range(p['Days 6-7'])}: {p['Days 6-7']['average_in']:.2f}in",
        "Basins: "+", ".join(f"{n} {t[n]:.2f}in" for n in WATCHED)]
    snow=report.get("snow",{})
    if "periods" in snow:
        lines.append("Snow — strongest chance anywhere in La Plata County (not countywide):")
        if snow.get("active"):
            summary=snow_summary(snow)
            for day in snow["periods"]:
                rows=[(threshold,pct) for d,threshold,pct in summary if d==day]
                if rows:
                    labels=[]
                    for threshold,pct in rows:
                        chance=f"{pct}%+" if pct>=70 else f"{pct}–{39 if pct==10 else 69}%"
                        labels.append(f"{chance} chance of {threshold}+in")
                    lines.append(f"{day} | {local_range(p[day])}: "+"; ".join(labels))
        else:
            lines.append("No part of the county has at least a 10% chance of 4+in; lighter snow is still possible.")
    return "\n".join(lines)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--force",action="store_true")
    parser.add_argument("--current",default="data/latest.json")
    parser.add_argument("--state",default="data/notification_state.json")
    args=parser.parse_args()
    report=json.loads(Path(args.current).read_text())
    state_path=Path(args.state)
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    reason,new_state=decide(report,state,force=args.force)
    if reason:
        bot=os.environ.get("GROUPME_BOT_ID")
        if not bot: raise RuntimeError("Missing GROUPME_BOT_ID Actions secret")
        response=requests.post("https://api.groupme.com/v3/bots/post",
                               json={"bot_id":bot,"text":message(report,reason)},timeout=30)
        response.raise_for_status()
        print("GroupMe HTTP",response.status_code,reason)
    else: print("Quiet: no update required.")
    state_path.parent.mkdir(parents=True,exist_ok=True)
    state_path.write_text(json.dumps(new_state,indent=2)+"\n")
if __name__=="__main__": main()


