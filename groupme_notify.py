"""GroupMe QPF: notify changes during wet patterns, daily unchanged, quiet when dry."""
import argparse,json,os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

WATCHED={"Vallecito":"1408010112","Upper Pine":"1408010111","Upper Florida":"1408010407","Animas Canyon":"1408010403"}
SOURCE="https://github.com/Matthew-Richardson/la-plata-qpf-monitor/blob/main/data/latest.md"
def totals(r):
    return {"county":r["periods"]["Days 1-7"]["average_in"],
            **{name:r["huc10_watersheds"][huc]["periods"]["Days 1-7"]["average_in"] for name,huc in WATCHED.items()}}
def decide(report,state,force=False,now=None):
    now=now or datetime.now(ZoneInfo("America/Denver"))
    t=totals(report)
    snow=report.get("snow",{})
    snow_signature=sorted({(day,row["threshold_in"],row["minimum_probability_pct"]) for day,rows in snow.get("periods",{}).items() for row in rows})
    snow_active=bool(snow_signature)
    wet=t["county"]>=0.50 or any(t[n]>=1.00 for n in WATCHED) or snow_active
    dry=t["county"]<0.25 and all(t[n]<0.50 for n in WATCHED) and not snow_active
    streak=(state.get("dry_streak",0)+1) if dry else 0
    previous=state.get("status","dry")
    status="wet" if wet else ("dry" if previous=="wet" and streak>=2 else previous)
    change=[f"{n} {t[n]-state['last_alert_totals'].get(n,t[n]):+.2f}in"
            for n in t if state.get("last_alert_totals") and
            abs(t[n]-state["last_alert_totals"].get(n,t[n]))>=0.25]
    reason=None
    snow_changed=state.get("snow_signature",[])!=[list(x) for x in snow_signature]
    if status=="wet":
        if previous!="wet": reason="Wet pattern detected"
        elif dry: reason=None  # wait for second dry forecast before declaring pattern over
        elif snow_changed: reason="Snowfall probability guidance changed"
        elif change: reason="Material change: "+", ".join(change)
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
        "Basins: "+", ".join(f"{n} {t[n]:.2f}in" for n in WATCHED),
        "Forecast liquid equivalent, not flood guidance."]
    snow=report.get("snow",{})
    if "periods" in snow:
        lines.append("Snow (WPC Days 1–3, any part of county):")
        if snow.get("active"):
            for day,rows in snow["periods"].items():
                if rows:
                    unique=sorted({(r["threshold_in"],r["minimum_probability_pct"]) for r in rows})
                    lines.append(day+": "+", ".join(f"≥{inch}in snow at ≥{pct}% chance" for inch,pct in unique))
        else:
            lines.append("No ≥10% area for ≥4in snow mapped; lighter snow possible.")
    return "\\n".join(lines)

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
