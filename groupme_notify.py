"""Send only operationally meaningful WPC QPF changes to a GroupMe bot.

Reads the verified current report and an optional prior successful report.
Never prints the bot token, and does not send on missing credentials.
"""
import argparse
import json
import os
import sys
from pathlib import Path
import requests

WATCHED = {
    "Vallecito Creek": "1408010112",
    "Upper Los Pinos": "1408010111",
    "Headwaters Florida": "1408010407",
    "Animas River Canyon": "1408010403",
}
SOURCE = "https://github.com/Matthew-Richardson/la-plata-qpf-monitor/blob/main/data/latest.md"
THRESHOLD_IN = 0.25

def totals(report):
    return {
        "county": report["periods"]["Days 1-7"]["average_in"],
        **{
            name: report["huc10_watersheds"][code]["periods"]["Days 1-7"]["average_in"]
            for name, code in WATCHED.items()
        },
    }

def material_changes(old, new):
    if old is None:
        return ["Initial verified forecast"]
    now = totals(new)
    before = totals(old)
    changes = []
    for name, value in now.items():
        delta = value - before[name]
        if abs(delta) >= THRESHOLD_IN:
            changes.append(f"{name}: {delta:+.2f} in")
    # Compare the valid periods for each sub-forecast; new issue timestamp alone is not a change.
    for label, p in new["periods"].items():
        previous = old["periods"].get(label)
        if previous is None:
            changes.append(f"{label} added")
        elif (p.get("valid_period") != previous.get("valid_period") and
              abs(p["average_in"] - previous["average_in"]) >= THRESHOLD_IN):
            changes.append(f"{label} timing/amount changed")
    return list(dict.fromkeys(changes))

def format_message(report, changes):
    p = report["periods"]
    t = totals(report)
    part1_3 = sum(p[x]["average_in"] for x in ("Day 1", "Day 2", "Day 3"))
    # Times below are UTC-based WPC periods. Read NOAA metadata for exact timestamps.
    lines = [
        "LPC WPC QPF | Updated",
        f"7-day county avg: {t['county']:.2f} in (range {p['Days 1-7']['min_in']:.2f}-{p['Days 1-7']['max_in']:.2f})",
        f"Days 1-3: {part1_3:.2f} in | Days 4-5: {p['Days 4-5']['average_in']:.2f} in | Days 6-7: {p['Days 6-7']['average_in']:.2f} in",
        "HUC10 basins: " + "; ".join(f"{name} {t[name]:.2f} in" for name in WATCHED),
        "Change: " + "; ".join(changes[:5]),
        "Forecast liquid-equivalent, not flood-stage guidance.",
        SOURCE
    ]
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Send current forecast even without material change")
    parser.add_argument("--current", default="data/latest.json")
    parser.add_argument("--previous", default="data/previous.json")
    args = parser.parse_args()
    current = json.loads(Path(args.current).read_text())
    oldfile = Path(args.previous)
    old = json.loads(oldfile.read_text()) if oldfile.exists() and oldfile.stat().st_size else None
    change = material_changes(old, current)
    if args.force and not change:
        change = ["Manual forecast delivery test"]
    if not change:
        print("No material change (>=0.25 inch); GroupMe message skipped.")
        return
    bot_id = os.environ.get("GROUPME_BOT_ID")
    if not bot_id:
        raise RuntimeError("Missing GROUPME_BOT_ID Actions secret; message not sent")
    message = format_message(current, change)
    response = requests.post(
        "https://api.groupme.com/v3/bots/post",
        json={"bot_id": bot_id, "text": message},
        timeout=30,
    )
    response.raise_for_status()
    print("GroupMe API accepted notification; HTTP", response.status_code)

if __name__ == "__main__":
    main()
