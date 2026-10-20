# La Plata County WPC QPF Monitor

Automated county and watershed precipitation forecasts, using [NOAA WPC QPF](https://www.wpc.ncep.noaa.gov/qpf/day1-7.shtml), the U.S. Census La Plata County boundary (FIPS 08067), and USGS HUC8/HUC10 watershed polygons. Reports are **forecast liquid-equivalent precipitation in inches**, not observed rainfall or flood-stage predictions.

## Daily schedule — La Plata County local time

The existing **La Plata Rain Watch** task in ChatGPT Scheduled triggers forecast checks at **6:30 AM** and **6:30 PM Mountain Time year-round**. At the start of each Rain Watch check, the task updates `.chatgpt-trigger` with a unique local timestamp and the exact commit message `Trigger scheduled QPF check`; that push immediately starts the GitHub Actions calculation. This avoids GitHub's delayed cron dispatcher while keeping calculation, validation, state, and GroupMe delivery inside GitHub. There is no separate active QPF schedule.

Runs are serialized to protect forecast and notification state. GroupMe delivery normally follows several minutes after the trigger because the forecast query, geometry analysis, and tests must finish first.

**GroupMe quiet hours: 9:00 PM through 5:59 AM Mountain Time.** The notifier checks the actual local time before sending, including delayed runs and manual `--force` runs. During quiet hours, the verified forecast can still be saved, but no GroupMe message is sent and the last delivered notification baseline is retained. The next successful daytime check evaluates the newest forecast against that baseline; it does not send a backlog of overnight messages. Quiet hours apply to both rain and snow updates.

Dates and valid periods in messages use **America/Denver** local time, respecting daylight saving time.

## What is calculated

Each successful analysis updates [data/latest.md](data/latest.md) and [data/latest.json](data/latest.json) with:

- La Plata County area-weighted QPF mean, mapped minimum and maximum
- Full USGS HUC8 and finer HUC10 watershed averages and ranges, including upstream/out-of-county areas
- Day 1, Day 2, Day 3, Days 4–5, Days 6–7, and seven-day QPF
- WPC issue times and valid forecast periods
- **Snow:** NOAA WPC Days 1–3 probabilities for ≥4, ≥8, and ≥12 inches, summarized within La Plata County as below 7,000 feet, 7,000–9,000 feet, and above 9,000 feet using the USGS 3DEP elevation service

The rain message highlights Vallecito Creek, upper Los Pinos, headwaters Florida River, Animas Canyon, and headwaters La Plata River HUC10 units. HUC10 units **are not exact gauge-upstream catchments**. Snow is presented by elevation rather than watershed.

## When GroupMe sends a message

GroupMe also checks official operational guidance on the **same Rain Watch runs**:

- **LPC river forecasts only:** Animas at Durango (DRGC2), Vallecito Creek (VNBC2), Los Pinos above Vallecito Reservoir (LPAC2), and La Plata River at Hesperus (LPHC2). Alert when the forecast maximum changes by at least **20% and 100 cfs**, crosses an action/flood stage category, or its peak shifts by **6 hours** (for flows of at least 500 cfs). Forecasts are converted from kcfs to cfs. This does not monitor out-of-county river forecast points.
- **WPC excessive-rainfall outlooks, Days 1–5:** check polygons against the actual county boundary. Alert on new risk coverage or an increase/decrease in risk category. The same valid period is tracked across daily layer rollovers. A risk somewhere in the county does not imply the whole county has that category.
- **NWS hourly timing:** check Durango, Bayfield and Ignacio. Track the next sustained window with hourly precipitation probability at least 50% (at least six wet hours, bridging gaps up to six hours). Alert on a new/removed window or a start/end shift of **6 hours or more**. This is a likely-precipitation window, not an exact onset, ending time, peak-intensity forecast, or interpretation of AFD prose.

Operational messages show the four river forecast peaks in **cfs and local time**. Initial activation sends the current active guidance as its baseline. Subsequent comparisons use the last successfully delivered values, retaining gradual changes between checks. A river forecast at the end of its available horizon is labeled a forecast maximum rather than a confirmed crest.

Unavailable/stale sources are logged and excluded from change detection; their prior delivered values remain intact. River guidance older than 36 hours and NWS hourly forecasts older than 24 hours are not used. Other available sources can still trigger a message. `data/operational_latest.json` records source errors and the latest checked guidance. Quiet hours still apply. Long messages are split at line boundaries to meet GroupMe's length limit. Preview without sending: `python groupme_notify.py --preview`.

After a **successful** scheduled forecast calculation **outside quiet hours**:

| Condition | GroupMe behavior |
| --- | --- |
| New wet pattern (liquid QPF or mapped ≥10% probability of ≥4 inches of snow somewhere in county in Days 1–3) | Send an initial briefing |
| Snow probability area appears or the category/threshold changes | Send updated snow guidance, labeled **increased**, **decreased**, or **mixed**, even if liquid QPF is unchanged |
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
LPC precipitation | No material changes
Rain | Thu 10/8 6 AM–Thu 10/15 6 AM
County 7-day avg: 2.95" (range 1.50–5.00")
Days 1–3 (Thu 10/8–Sun 10/11): 0.25" | Days 4–5: 0.97" | Days 6–7: 1.71"
Areas, 7-day avg: Vallecito 4.00" | Upper Pine 3.88" | Upper Florida 3.93" | Animas Canyon 3.72" | La Plata 1.85"
Snow | No mapped 10%+ chance of 4"; lighter snow remains possible
```

**Example snowfall addendum for a hypothetical winter storm (not a current NOAA forecast):**

```text
LPC precipitation | ↑ Snow increased
Snow | Fri 10/9–Sat 10/10
Below 7,000 ft: no mapped 10%+ chance of 4"+
7,000–9,000 ft: 40–69% for 4"+
Above 9,000 ft: 70%+ for 4"+; 40–69% for 8"+
```

The snow block is quick-look guidance based on meaningful overlap between WPC probability polygons and each USGS 3DEP elevation band. It does not replace NWS point forecasts or pass/road observations. Day 4–7 snowfall depth is not estimated from QPF.

## Running manually / validating

Go to [Actions → La Plata WPC QPF](../../actions/workflows/qpf.yml), select **Run workflow**, and inspect its execution log. A successful manual run also sends a GroupMe status message even when amounts are unchanged, except during 9 PM–6 AM Mountain Time quiet hours. Manual runs do not override quiet hours. Check `data/latest.json` for the actual NOAA valid times and report freshness.

## Operational limitations

- WPC's mapped precipitation polygons approximate forecast ranges; an area outside mapped precipitation contours is treated as zero in the area-weighted estimate (a lower-bound assumption).
- Seven-day precipitation amounts alone do not determine flash flood risk, short-duration rainfall intensity, or river crests.
- NOAA issue periods can differ across products; interpret sums of independently rounded periods cautiously.
- GitHub scheduled starts are approximate; failures leave the previous valid result intact.
- The ChatGPT **La Plata Rain Watch** owns the twice-daily timing; GitHub cron and the separate QPF trigger task are intentionally disabled to avoid delayed or duplicate runs.


