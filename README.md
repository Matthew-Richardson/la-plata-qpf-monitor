# La Plata County WPC QPF Monitor

Scheduled precipitation estimates from NOAA's Weather Prediction Center, intersected with the U.S. Census boundary for La Plata County, CO (FIPS 08067).

## Current scope
- Countywide Day 1, Day 2, Day 3, Days 4–5, Days 6–7, and Days 1–7 rainfall (liquid-equivalent QPF).
- Area-weighted county average, polygon minimum and maximum in inches.
- NOAA issuance times and forecast valid periods.
- Strict coverage checks: incomplete or conflicting GIS data causes workflow failure, not manufactured totals.

## Run and results
Visit **Actions → La Plata WPC QPF → Run workflow** to run it immediately. It is also scheduled twice daily at 01:20 and 13:20 UTC (7:20 AM/PM MDT; 6:20 AM/PM MST). GitHub's schedule can be delayed.

Following a successful run, open [data/latest.md](data/latest.md) or [data/latest.json](data/latest.json). Check the generated UTC timestamp; a failed job leaves previously validated files intact.

## Limits
This initial version computes La Plata County only. Upstream Animas, Pine/Vallecito, and Florida watershed statistics require validated watershed boundaries in the next release. QPF is forecast liquid-equivalent precipitation, not a flood prediction.
