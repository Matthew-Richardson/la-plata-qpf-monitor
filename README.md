# La Plata County WPC QPF Monitor

Automated county and watershed precipitation forecasts, using [NOAA WPC QPF](https://www.wpc.ncep.noaa.gov/qpf/day1-7.shtml), the U.S. Census La Plata County boundary (FIPS 08067), and USGS HUC8/HUC10 watershed polygons. Reports are **forecast liquid-equivalent precipitation in inches**, not observed rainfall or flood-stage predictions.

## Daily schedule — La Plata County local time

GitHub Actions checks forecasts at **7:20 AM** and **7:20 PM Mountain Time year-round**, using a daylight-saving-aware gate for `America/Denver`:

| Season | Morning UTC | Evening UTC | Local starts |
| --- | --- | --- | --- |
| Mountain Daylight Time (MDT) | 13:20 UTC | 01:20 UTC | **7:20 AM / 7:20 PM** |
| Mountain Standard Time (MST) | 14:20 UTC | 02:20 UTC | **7:20 AM / 7:20 PM** |

Only the matching seasonal UTC pair proceeds with analysis. Runs are serialized to protect forecast and notification state. These are **scheduled start times, not guaranteed delivery times**; GitHub may queue runs and processing takes additional time.

**GroupMe quiet hours: 9:00 PM through 5:59 AM Mountain Time.** The notifier checks the actual local time before sending, including delayed runs and manual `--force` runs. During quiet hours, the verified forecast can still be saved, but no GroupMe message is sent and the last delivered notification baseline is retained. The next successful daytime check evaluates the newest forecast against that baseline; it does not send a backlog of overnight messages. Quiet hours apply to both rain and snow updates.

Dates and valid periods in messages use **America/Denver** local time, respecting daylight saving time.

## What is calculated

Each successful analysis updates [data/latest.md](data/latest.md) and [data/latest.json](data/latest.json) with:

- La Plata County area-weighted QPF mean, mapped minimum and maximum
- Full USGS HUC8 and finer HUC10 watershed averages and ranges, including upstream/out-of-county areas
- Day 1, Day 2, Day 3, Days 4–5, Days 6–7, and seven-day QPF
- WPC issue times and valid forecast periods
- **Snow:** separate NOAA WPC Days 1–3 probabilities for ≥4, ≥8, and ≥12 inches of snowfall at ≥10%, ≥40%, and ≥70% categories, intersected with any portion of La Plata County

The message highlights Vallecito Creek, upper Los Pinos, headwaters Florida River, and Animas Canyon HUC10 units. HUC10 units **are not exact gauge-upstream catchments**.

## When GroupMe sends a message

After a **successful** scheduled forecast calculation **outside quiet hours**:

| Condition | GroupMe behavior |
| --- | --- |
| New wet pattern (liquid QPF or mapped ≥10% probability of ≥4 inches of snow somewhere in county in Days 1–3) | Send an initial briefing |
| Snow probability area appears or the category/threshold changes | Send updated snow guidance, even if liquid QPF is unchanged |
| During wet pattern, county or highlighted HUC10 seven-day average changes by **0.25 in or more** versus last sent values | Send an updated briefing (morning or evening) that explicitly says **increased**, **decreased**, or **mixed**, with the direction for every changed area |
| Wet pattern continues without material changes | Send **one morning “No material changes”** message per local calendar day; skip evening unchanged messages |
| First dry forecast while previously wet | Continue checking; do not announce the end yet |
| **Two consecutive dry forecasts** | Send one final message: “Returned to dry pattern; routine alerts paused” |
| Dry pattern persists | **Send nothing** until a new wet pattern is detected |
| Manual **Run workflow** | Send a current status message even without a change, after analysis succeeds and outside quiet hours |
| Any run completing during 9 PM–6 AM quiet hours | Save verified forecast; suppress GroupMe and retain the last delivered baseline for the next daytime check |
| GIS query, test or processing failure | **Send nothing**; do not distribute potentially stale/unverified rainfall |

**Threshold definitions** (seven-day area-weighted QPF):

- **Wet:** county average **>= 0.50 in**, OR any highlighted HUC10 average **>= 1.00 in**, OR a WPC Day 1–3 snowfall probability polygon (≥10% chance of ≥4 in snow) intersects any portion of the county.
- **Dry:** county average **< 0.25 in** AND **all** highlighted HUC10 averages **< 0.50 in**, AND **no** Day 1–3 WPC ≥4 in snow probability polygon intersects the county, on **two consecutive successful forecasts**.
- Between wet and dry thresholds the state is retained to avoid notification flapping.
- A **material change** is an absolute increase or decrease **>= 0.25 in** in county or a highlighted HUC10 seven-day average since the last sent wet briefing.
- Material-change headlines state whether the forecast **increased**, **decreased**, or contains **mixed** changes. Each changed county/basin value also states its own direction and amount.

No emails or SMS are configured. GroupMe notifications go to the private group through a bot. The bot ID is held in the GitHub Actions secret `GROUPME_BOT_ID` and is not checked into source control. Notification state persists in `data/notification_state.json` after successful scheduled/manual checks.

### Example GroupMe notification

The following illustrates the format, using the **October 8, 2026** NOAA WPC values, **not a new live forecast**. Dates use La Plata County local time.

```text
LPC QPF | No material changes
Valid: Thu Oct 8–Thu 15 (6 AM–6 AM local)
7-day county avg 2.95in (range 1.50-5.00in)
Days 1–3 | Thu Oct 8–Sun 11 (6 AM–6 AM local): 0.25in
Days 4–5 | Sun Oct 11–Tue 13 (6 AM–6 AM local): 0.97in
Days 6–7 | Tue Oct 13–Thu 15 (6 AM–6 AM local): 1.71in
Basins: Vallecito 4.00in, Upper Pine 3.88in, Upper Florida 3.93in, Animas Canyon 3.72in
Forecast liquid equivalent, not flood guidance.
Snow (WPC Days 1–3, any part of county):
No ≥10% area for ≥4in snow mapped; lighter snow possible.
```

**Example snowfall addendum for a hypothetical winter storm (not a current NOAA forecast):**

```text
Snow (WPC Days 1–3, any part of county):
Day 1: ≥4in snow at ≥40% chance, ≥8in snow at ≥10% chance
Day 2: ≥4in snow at ≥70% chance
```

These values describe probability categories **somewhere within La Plata County**, not the likelihood across the entire county, and not predicted snowfall depth at Durango or any specific mountain pass. Day 4–7 snowfall depth is not estimated from QPF.

## Running manually / validating

Go to [Actions → La Plata WPC QPF](../../actions/workflows/qpf.yml), select **Run workflow**, and inspect its execution log. A successful manual run also sends a GroupMe status message even when amounts are unchanged, except during 9 PM–6 AM Mountain Time quiet hours. Manual runs do not override quiet hours. Check `data/latest.json` for the actual NOAA valid times and report freshness.

## Operational limitations

- WPC's mapped precipitation polygons approximate forecast ranges; an area outside mapped precipitation contours is treated as zero in the area-weighted estimate (a lower-bound assumption).
- Seven-day precipitation amounts alone do not determine flash flood risk, short-duration rainfall intensity, or river crests.
- NOAA issue periods can differ across products; interpret sums of independently rounded periods cautiously.
- GitHub scheduled starts are approximate; failures leave the previous valid result intact.


