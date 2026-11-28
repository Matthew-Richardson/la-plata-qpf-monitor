# La Plata County WPC QPF Monitor

Automated county and watershed precipitation forecasts, using [NOAA WPC QPF](https://www.wpc.ncep.noaa.gov/qpf/day1-7.shtml), the U.S. Census La Plata County boundary (FIPS 08067), USGS gauge-upstream basin delineations, and USGS HUC8/HUC10 watershed polygons. Reports are **forecast liquid-equivalent precipitation in inches**, not observed rainfall or flood-stage predictions.

## Daily schedule — La Plata County local time

The existing **La Plata Rain Watch** task in ChatGPT Scheduled triggers forecast checks at **6:30 AM** and **6:30 PM Mountain Time year-round**. At the start of each Rain Watch check, the task updates `.chatgpt-trigger` with a unique local timestamp and the exact commit message `Trigger scheduled QPF check`; that push immediately starts the GitHub Actions calculation. This avoids GitHub's delayed cron dispatcher while keeping calculation, validation, state, and GroupMe delivery inside GitHub. There is no separate active QPF schedule.

Runs are serialized to protect forecast and notification state. GroupMe delivery normally follows several minutes after the trigger because the forecast query, geometry analysis, and tests must finish first.

**GroupMe quiet hours: 9:00 PM through 5:59 AM Mountain Time.** The notifier checks the actual local time before sending, including delayed runs and manual `--force` runs. During quiet hours, the verified forecast can still be saved, but no GroupMe message is sent and the last delivered notification baseline is retained. The next successful daytime check evaluates the newest forecast against that baseline; it does not send a backlog of overnight messages. Quiet hours apply to both rain and snow updates.

Dates and valid periods in messages use **America/Denver** local time, respecting daylight saving time.


## Forecast sources and message timestamps

- **WPC QPF** is the primary numerical source for county, period and highlighted-area precipitation totals, including Days 1–7. Trend language such as **increased** or **decreased** compares WPC with WPC.
- **NWS Grand Junction / NDFD** supplies local precipitation timing and operational context. It may be used as a clearly labeled short-range fallback when a newer WPC graphical forecast is available but WPC numerical GIS files are still pending. NWS/NDFD totals are never silently substituted for WPC totals or compared against earlier WPC totals as a forecast change.
- **NWS river forecasts** are a separate product and remain in a separate GroupMe text.
- **Bureau of Reclamation reservoir operations** supply provisional daily storage, inflow and total-release values for [Vallecito](https://www.usbr.gov/rsvrWater/rsv40Day.html?reservoirtype=Reservoir&siteid=933) and [Lemon](https://www.usbr.gov/rsvrWater/rsv40Day.html?reservoirtype=Reservoir&siteid=934) reservoirs. Percent full and headroom are calculated against Reclamation's published total capacities: 129,700 acre-feet for Vallecito and 40,146 acre-feet for Lemon.

Product times mean the source's **issuance time**, not the time the monitor checked the source. The weather text uses one compact line:

`Issued | WPC Tue 10/20, 4:31 AM MDT | NWS GJT Tue 10/20, 6:16 PM MDT`

When a newer WPC numerical file is pending, the same line identifies both the pending status and the latest available WPC cycle:

`Issued | WPC numerical update pending; latest Tue 10/20, 4:31 AM MDT | NWS GJT: Tue 10/20, 6:16 PM MDT`

The separate river text identifies its own forecast cycle:

`NWS river forecast issued Tue 10/20, 8:35 AM MDT`

These timestamps are examples of formatting only; each run uses the actual latest verified issuance times.

## What is calculated

Each successful analysis updates [data/latest.md](data/latest.md) and [data/latest.json](data/latest.json) with:

- La Plata County area-weighted QPF mean, mapped minimum and maximum
- Full USGS HUC8 and finer HUC10 watershed averages and ranges, including upstream/out-of-county areas
- WPC averages for the USGS NLDI basin delineations upstream of Vallecito Creek near Bayfield, Los Pinos River above Vallecito Reservoir, Florida River above Lemon Reservoir, Animas River at Durango and La Plata River at Hesperus
- Day 1, Day 2, Day 3, Days 4–5, Days 6–7, and seven-day QPF
- WPC issue times and valid forecast periods
- **Snow:** NOAA WPC Days 1–3 probabilities for ≥4, ≥8, and ≥12 inches, summarized within La Plata County as below 7,000 feet, 7,000–9,000 feet, and above 9,000 feet using the USGS 3DEP elevation service

The rain message uses five gauge-upstream catchments rather than general HUC10 units: Vallecito Creek near Bayfield, Los Pinos River above Vallecito Reservoir, Florida River above Lemon Reservoir, Animas River at Durango and La Plata River at Hesperus. The full HUC tables remain in `data/latest.md` for reference. Snow is presented by elevation rather than watershed.

## When GroupMe sends a message

GroupMe also checks official operational guidance on the **same Rain Watch runs**:

- **LPC river forecasts:** Animas at Durango (DRGC2), Vallecito Creek (VNBC2), Los Pinos above Vallecito Reservoir (LPAC2), and La Plata River at Hesperus (LPHC2). Alert when the forecast maximum changes by at least **20% and 100 cfs**, crosses an action/flood stage category, or its peak shifts by **6 hours** (for flows of at least 500 cfs). Forecasts are converted from kcfs to cfs. Florida River above Lemon Reservoir (FRIC2) is included as a clearly labeled current observation because NOAA does not currently publish a forecast series for that point; unavailable forecast data are never interpreted as reduced risk. This does not monitor out-of-county river forecast points.
- **WPC excessive-rainfall outlooks, Days 1–5:** check polygons against the actual county boundary. Alert on new risk coverage or an increase/decrease in risk category. The same valid period is tracked across daily layer rollovers. A risk somewhere in the county does not imply the whole county has that category.
- **NWS hourly timing:** check Durango, Bayfield and Ignacio. Track the next sustained window with hourly precipitation probability at least 50% (at least six wet hours, bridging gaps up to six hours). Alert on a new/removed window or a start/end shift of **6 hours or more**. This is a likely-precipitation window, not an exact onset, ending time, peak-intensity forecast, or interpretation of AFD prose.

Storm-tracking and monitor modes send a WPC weather text, a separate river/hydrology text, and the RRFS image at the existing 6:30 AM and 6:30 PM Mountain Time checks. The image remains its own post; there is no third local-drainage text. NBM comparison text remains removed.

### Three GroupMe operating modes

The workflow selects one mode for each verified run so message volume matches the operational signal.

**Dry mode** requires all of the following: WPC county seven-day average below **0.10 inch**, county maximum below **0.25 inch**, every monitored gauge-catchment average below **0.25 inch**, and no WPC Day 1–3 mapped ≥10% probability of ≥4 inches of snow in the county.

- Persistent dry runs remain quiet.
- When a dry-transition, manual-status or other required weather update is sent, it is one compact text containing the dated header and county seven-day average/maximum.
- The routine river text is suppressed unless an actual river, flood-product or operational timing change must be reported.
- The RRFS image is neither rendered nor posted.

**Storm-tracking mode** is the default whenever dry-mode criteria are not met and monitor-mode triggers are absent. It preserves the existing concise twice-daily format:

- **Weather:** dated WPC **Days 1–3**, **Days 4–5**, and **Days 6–7** county averages; active NWS likely-precipitation window; current WPC excessive-rainfall category; and active WPC snow guidance.
- **Rivers:** available NWS peak/max flow, time and stage for Animas at Durango, Vallecito Creek, Los Pinos above Vallecito and La Plata at Hesperus; the Florida-above-Lemon observation; and the NWS river-forecast issue time.
- The current RRFS precipitation/rain/snow image is posted.

**Monitor mode** replaces the former event-escalation label and activates when any of the following is present: a WPC Moderate or High excessive-rainfall risk in the county; a forecast action/flood-stage condition; a materially increasing river or WPC risk signal; an increasing material QPF change; a county Days 1–3 WPC average of at least 1.50 inches; or a reservoir at least 90% full with inflow exceeding release.

- **Weather:** timing, WPC period totals, five upstream gauge-catchment totals, current WPC risk and active snow guidance.
- **River/hydrology:** all official peaks and stages, current Florida observation, material changes, and provisional Vallecito and Lemon storage, percent full, headroom, inflow and release.
- The current RRFS precipitation/rain/snow image is posted.

The monitor does not invent missing escalation fields. Short-duration peak rainfall rates and matched observed-plus-remaining storm rainfall require separate verified gridded collectors and are not inferred from seven-day WPC totals. Until those collectors are added, they are omitted rather than shown as zero or estimated from an incompatible forecast window.

When all four forecast gauges have fresh guidance and a usable observed or near-term baseline, the river message summarizes “No significant rises expected” if every forecast increase is less than 20% or 100 cfs. This is a monitor threshold, not an official flood threshold. The individual river forecasts remain listed beneath that summary. Action/flood stages, missing baselines, unavailable forecasts and significant rises prevent the quiet summary. Florida above Lemon remains explicitly labeled as an observation, not a forecast. A forecast maximum at the end of the available horizon is not presented as a confirmed crest. Delivery baselines advance only after both texts succeed; a partial send can repeat the first text on retry.

Vallecito and Lemon reservoir operations come from Reclamation's provisional 40-day tables. A storage change of at least 5 percentage points or a release change of at least 25% and 100 cfs is material for comparison. Reservoir values are shown in event-escalation mode, and in routine mode only when storage is at least 90% full while inflow exceeds release. Unavailable or stale reservoir data are logged and never interpreted as added headroom or reduced risk.

Every river text explicitly labels **ACTION STAGE**, **MINOR FLOOD STAGE**, **MODERATE FLOOD STAGE**, or **MAJOR FLOOD STAGE** whenever forecast, including unchanged forecasts, and includes the forecast height in feet. Below-action forecasts and gauges without defined stage thresholds are labeled separately. Classification uses official stage heights rather than comparing flow alone with potentially different published cfs thresholds.

Unavailable/stale sources are logged and excluded from change detection; their prior delivered values remain intact. River guidance older than 36 hours and NWS hourly forecasts older than 24 hours are not used. Other available sources can still trigger a message. `data/operational_latest.json` records source errors and the latest checked guidance. Quiet hours still apply. Long messages are split at line boundaries to meet GroupMe's length limit. Preview without sending: `python groupme_notify.py --preview`.

After a **successful** scheduled forecast calculation **outside quiet hours**:

| Condition | GroupMe behavior |
| --- | --- |
| New wet pattern (liquid QPF or mapped ≥10% probability of ≥4 inches of snow somewhere in county in Days 1–3) | Send an initial briefing |
| Snow probability area appears or the category/threshold changes | Send updated snow guidance, labeled **increased**, **decreased**, or **mixed**, even if liquid QPF is unchanged |
| During wet pattern, county or gauge-catchment seven-day average changes by **0.25 in or more** versus last sent values | Send an updated briefing (morning or evening) that explicitly says **increased**, **decreased**, or **mixed**, with the direction for every changed area |
| Wet pattern continues without material changes | Send a weather outlook **at both the morning and evening Rain Watch checks**, followed by a **separate LPC river forecast text**; explicitly label unchanged guidance |
| First dry forecast while previously wet | Continue checking; do not announce the end yet |
| **Two consecutive dry forecasts** | Send one final message: “Returned to dry pattern; routine alerts paused” |
| Dry pattern persists | **Send nothing** until a new wet pattern is detected |
| Manual **Run workflow** | Send a current status message even without a change, after analysis succeeds and outside quiet hours |
| Any run completing during 9 PM–6 AM quiet hours | Save verified forecast; suppress GroupMe and retain the last delivered baseline for the next daytime check |
| GIS query, test or processing failure | **Send nothing**; do not distribute potentially stale/unverified rainfall |

**Threshold definitions** (seven-day area-weighted QPF):

- **Wet:** county average **>= 0.50 in**, OR any monitored gauge-catchment average **>= 1.00 in**, OR a WPC Day 1–3 snowfall probability polygon (≥10% chance of ≥4 in snow) intersects any portion of the county.
- **Dry:** county average **< 0.25 in** AND **all** monitored gauge-catchment averages **< 0.50 in**, AND **no** Day 1–3 WPC ≥4 in snow probability polygon intersects the county, on **two consecutive successful forecasts**.
- Between wet and dry thresholds the state is retained to avoid notification flapping.
- A **material change** is an absolute increase or decrease **>= 0.25 in** in county or a monitored gauge-catchment seven-day average since the last sent wet briefing.
- Material-change headlines state whether the forecast **increased**, **decreased**, or contains **mixed** changes. Each changed county/basin value also states its own direction and amount.

No emails or SMS are configured. GroupMe notifications go to the private group through a bot. The bot ID is held in the GitHub Actions secret `GROUPME_BOT_ID` and is not checked into source control. Notification state persists in `data/notification_state.json` after successful scheduled/manual checks.

### Compact notification example

Illustrative format, not a live forecast:

```text
LPC precipitation | No material changes
WPC 7-day avg: 2.82" (Tue 10/20–Tue 10/27)
```

The river summary remains separate. The image caption is a single short change label. Active snow guidance and material official timing/risk changes are retained when relevant; map values and map timestamps are not repeated in its caption.

## Running manually / validating

Go to [Actions → La Plata WPC QPF](../../actions/workflows/qpf.yml), select **Run workflow**, and inspect its execution log. A successful manual run also sends a GroupMe status message even when amounts are unchanged, except during 9 PM–6 AM Mountain Time quiet hours. Manual runs do not override quiet hours. Check `data/latest.json` for the actual NOAA valid times and report freshness.

## Operational limitations

- WPC's mapped precipitation polygons approximate forecast ranges; an area outside mapped precipitation contours is treated as zero in the area-weighted estimate (a lower-bound assumption).
- Seven-day precipitation amounts alone do not determine flash flood risk, short-duration rainfall intensity, or river crests.
- NOAA issue periods can differ across products; interpret sums of independently rounded periods cautiously.
- Gauge catchments come from USGS National Linked Data Index basin delineations. They are more relevant to a specific gauge than HUC10 boundaries, but they do not reproduce NWS river-model routing, antecedent soil conditions, reservoir storage or controlled releases.
- Reservoir percent full is storage divided by published total capacity. Headroom to total capacity is not the same as an operator-defined flood-control allocation, target pool or assurance that forecast inflow can be stored. Reclamation values are provisional and may lag real-time operations.
- GitHub scheduled starts are approximate; failures leave the previous valid result intact.
- The ChatGPT **La Plata Rain Watch** owns the twice-daily timing; GitHub cron and the separate QPF trigger task are intentionally disabled to avoid delayed or duplicate runs.



## Twice-daily RRFS image

The existing 6:30 AM and 6:30 PM Mountain Time Rain Watch triggers send a three-panel RRFS image to the same GroupMe bot in storm-tracking and monitor modes. Dry mode suppresses both rendering and delivery. No extra schedule was created. The image uses the newest complete 84-hour run published by [LPC RRFS](https://matthew-richardson.github.io/LPC_rrfs/) and displays model start, forecast-through time and duration. Total precipitation and estimated-rain panels retain the eight upstream watershed averages and Durango/Bayfield/Ignacio town-center values. The snowfall panel shows accumulated model snowfall at Hesperus, Kennebec Pass, Purgatory, Bayfield, Durango, Ignacio, Bondad, Chicago Basin, Vallecito and Lemon. Point values sample the model grid cell at each named location, not municipal or drainage averages.

**One-time setup:** Add the GroupMe developer access token as the repository Actions secret `GROUPME_ACCESS_TOKEN` in [Settings → Secrets and variables → Actions](../../settings/secrets/actions). This is separate from the existing `GROUPME_BOT_ID`. Get the token from your signed-in [GroupMe developer account](https://dev.groupme.com/). Do not put it in source files. GroupMe requires this token to upload PNGs to its image service before the existing bot can attach them.

Each scheduled run renders the map and uploads it as an Actions artifact. Automatic image delivery is limited to one successful post per local morning/evening slot; a forced manual workflow may send another. Only successful uploads/posts advance `data/rrfs_image_state.json`. Runs outside 6:30 AM–9 PM do not post an image. Runs older than 18 hours, expired or incomplete forecasts, changed grids, and invalid data are withheld instead of reposting an old PNG. A Pages publishing rollover is retried once. RRFS render failures do not prevent the existing WPC text updates. Image-upload failures are reported in Actions while text delivery retains its existing behavior.

The rainfall panel subtracts modeled snow water equivalent; freezing rain or mixed precipitation can remain. The snowfall panel uses RRFS accumulated new snowfall (ASNOW), not snow depth or a fixed snow-to-liquid ratio. The Florida above Lemon boundary is approximate. Upstream Animas is delineated at Durango; La Plata is delineated at Hesperus. The watershed polygons, point locations and weights for this image are local assets and do not change the interactive RRFS site.

The image caption is limited to `RRFS 84h totals: Increased.`, `Decreased.`, or `Unchanged.` compared with the last successfully posted image. A change of at least 0.10 inch in any displayed basin's total precipitation or estimated rain counts; opposing material changes are labeled `Mixed changes` rather than forced into one direction. This compares rolling 84-hour totals, not a fixed storm window. Forecast dates, times, locations and values remain on the image. Missing comparison data say `RRFS 84h forecast.` and establish a baseline only after a successful post.
