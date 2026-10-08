# La Plata County WPC QPF Monitor

Automated county and watershed precipitation forecasts, using [NOAA WPC QPF](https://www.wpc.ncep.noaa.gov/qpf/day1-7.shtml), the U.S. Census La Plata County boundary (FIPS 08067), and USGS HUC8/HUC10 watershed polygons. Reports are **forecast liquid-equivalent precipitation in inches**, not observed rainfall or flood-stage predictions.

## Daily schedule — La Plata County local time

GitHub Actions runs automatically at **13:20 UTC** and **01:20 UTC**, translating to:

| Season | Morning run | Evening run |
| --- | --- | --- |
| Mountain Daylight Time (MDT) | **7:20 AM** | **7:20 PM** |
| Mountain Standard Time (MST) | **6:20 AM** | **6:20 PM** |

These are **scheduled workflow start times, not guaranteed message delivery times**. GitHub may queue a run, and the forecast query/geometry analysis takes additional time. Dates and valid periods in GroupMe messages are converted from NOAA UTC timestamps to **America/Denver** local time, respecting daylight saving time.

## What is calculated

Each successful analysis updates [data/latest.md](data/latest.md) and [data/latest.json](data/latest.json) with:

- La Plata County area-weighted QPF mean, mapped minimum and maximum
- Full USGS HUC8 and finer HUC10 watershed averages and ranges, including upstream/out-of-county areas
- Day 1, Day 2, Day 3, Days 4–5, Days 6–7, and seven-day QPF
- WPC issue times and valid forecast periods

The message highlights Vallecito Creek, upper Los Pinos, headwaters Florida River, and Animas Canyon HUC10 units. HUC10 units **are not exact gauge-upstream catchments**.

## When GroupMe sends a message

After a **successful** scheduled forecast calculation:

| Condition | GroupMe behavior |
| --- | --- |
| New wet pattern | Send an initial briefing |
| During wet pattern, county or highlighted HUC10 seven-day average changes by **0.25 in or more** versus last sent values | Send an updated briefing (morning or evening) |
| Wet pattern continues without material changes | Send **one morning “No material changes”** message per local calendar day; skip evening unchanged messages |
| First dry forecast while previously wet | Continue checking; do not announce the end yet |
| **Two consecutive dry forecasts** | Send one final message: “Returned to dry pattern; routine alerts paused” |
| Dry pattern persists | **Send nothing** until a new wet pattern is detected |
| Manual **Run workflow** | Send a current status message even without a change, after analysis succeeds |
| GIS query, test or processing failure | **Send nothing**; do not distribute potentially stale/unverified rainfall |

**Threshold definitions** (seven-day area-weighted QPF):

- **Wet:** county average **>= 0.50 in**, OR any highlighted HUC10 average **>= 1.00 in**.
- **Dry:** county average **< 0.25 in** AND **all** highlighted HUC10 averages **< 0.50 in**, on **two consecutive scheduled or manual successful forecasts**.
- Between wet and dry thresholds the state is retained to avoid notification flapping.
- A **material change** is an absolute increase or decrease **>= 0.25 in** in county or a highlighted HUC10 seven-day average since the last sent wet briefing.

No emails or SMS are configured. GroupMe notifications go to the private group through a bot. The bot ID is held in the GitHub Actions secret `GROUPME_BOT_ID` and is not checked into source control. Notification state persists in `data/notification_state.json` after successful scheduled/manual checks.

### Example GroupMe notification

The following illustrates the format, using the **October 8, 2026** NOAA WPC values, **not a new live forecast**. Dates use La Plata County local time.

```text
LPC QPF | No material changes
Valid: Oct 8–15 (6 AM–6 AM local)
7-day county avg 2.95in (range 1.50-5.00in)
Oct 8–11 (6 AM–6 AM local): 0.25in
Oct 11–13 (6 AM–6 AM local): 0.97in
Oct 13–15 (6 AM–6 AM local): 1.71in
Basins: Vallecito 4.00in, Upper Pine 3.88in, Upper Florida 3.93in, Animas Canyon 3.72in
Forecast liquid equivalent, not flood guidance.
```

## Running manually / validating

Go to [Actions → La Plata WPC QPF](../../actions/workflows/qpf.yml), select **Run workflow**, and inspect its execution log. A successful manual run also sends a GroupMe status message even when amounts are unchanged. Check `data/latest.json` for the actual NOAA valid times and report freshness.

## Operational limitations

- WPC's mapped precipitation polygons approximate forecast ranges; an area outside mapped precipitation contours is treated as zero in the area-weighted estimate (a lower-bound assumption).
- Seven-day precipitation amounts alone do not determine flash flood risk, short-duration rainfall intensity, or river crests.
- NOAA issue periods can differ across products; interpret sums of independently rounded periods cautiously.
- GitHub scheduled starts are approximate; failures leave the previous valid result intact.
