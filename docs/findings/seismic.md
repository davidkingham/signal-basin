# Seismic data: what the public networks can and cannot do for prediction

Investigated 2026-08-09, prompted by the observation that seismometers sit
near several geyser basins. Everything below was measured against live
services and real waveforms, not read from papers.

## What exists and is publicly available

The University of Utah runs the Yellowstone seismic network (network code
`WY`); everything streams through EarthScope's open FDSN services with **no
authentication and near-real-time latency** (we pulled the trailing 15
minutes from five stations on demand). Stations relevant to geysers,
operating as of August 2026:

| Station | Site | Relevance |
|---|---|---|
| WY.YFT | Old Faithful (since 1993) | ~1.2 km from OF; the station in published OF studies |
| WY.YBB | Biscuit Basin (new, June 2025) | at Sapphire/Jewel |
| WY.YNM | Norris Museum (since 2012) | ~1–2 km from Steamboat; USGS reads it for eruptions |
| WY.YNB | Norris Geyser Basin (2023) | second Norris station |
| PB.B207 | Madison borehole (seismic + strain) | ~10 km north of Great Fountain |

**There is no operating station near Great Fountain, Fountain, Lone Star, or
Till** — the closest instruments to the Lower Basin are ~10 km away, and
published hydrothermal-tremor detections work at the ~1–2 km scale. The
frontcountry stations do not cover the geysers where our observation gaps
actually hurt.

Transport: `fdsnws/dataselect` miniSEED (~200 KB per 10 minutes at 100 Hz)
decoded with `simplemseed` (pure Python, light deps). The `irisws/timeseries`
ASCII service works for spot checks but its server-side
envelope/decimate chain proved unreliable, and decimation to ≤5 sps destroys
the signal — **Steamboat's tremor lives above 2 Hz**, exactly what the
anti-alias filter removes.

## The Steamboat signature, calibrated against real eruptions

Ground truth: GeyserTimes' recent majors (all flagged `major`, several
`electronic`). Minute-RMS of raw counts at WY.YNM.01.HHZ:

| window | minute-RMS |
|---|---|
| 2025-04-14 pre-eruption hour | ~960–1010 |
| 2025-04-14 onset +1 → +5 min | 2,775 → **8,574** (8.5×) |
| 2025-04-14 +20 → +30 min | 5,100–6,800 (sustained) |
| 2026-02-28 onset +0 → +15 min | **8,000–15,400** sustained |
| quiet control days | ~750–1,900 |

The eruption is unmistakable *in these windows*: an abrupt 8× rise sustained
for tens of minutes. Steamboat **minors** reach similar peak amplitudes but
collapse within ~4 minutes (measured on 2026-02-24: 10.8k → 1.5k inside five
minutes), so duration is the discriminator against minors.

## Why the single-station detector is not shippable — measured, not assumed

A candidate detector (rolling 15-minute *minimum* of minute-RMS ≥ threshold,
so spiky noise that dips cannot fire) was swept over four eruption days and
six control days across seasons. It fails three ways:

1. **Wind produces exactly the eruption signature.** 2026-02-28 and
   2026-03-10 show sustained RMS in the tens of thousands for hours (day
   median 16k–41k, p95 up to 156k) — dozens of false detections per day.
   Wind is sustained broadband shaking; the duration criterion does not
   reject it.
2. **One of four eruptions is seismically invisible.** The 2025-02-04 major
   (23:56 MST 02-03) produced *no* elevated minutes at YNM — that day's
   p99.7 is 1,486 against a 367 median. Instrument metadata rules out a gain
   change (sensitivity flat since 2013 within 5%). Eruption-to-eruption
   amplitude at YNM varies by ~10×.
3. **Winter telemetry gaps are routine.** Two of ten sampled days
   (2026-01-01, 2025-12-25) have no data at all — the 2026-01-01 eruption
   day among them.

Summer daytime is additionally hostile: visitor/wind/minor noise runs a
5k–13k baseline (vs ~1k at night), thinning the margin to a real eruption's
sustained ~8k to ~1.3×.

**The design that would work** — standard volcano-seismology practice — is a
**two-station amplitude ratio**: wind and regional noise move YNM and YNB
together, while Steamboat tremor is local and moves them apart, plus
response-corrected amplitudes and a false-positive rate validated across a
full year of data. That is a real research project. Shipping anything less
puts a false "STEAMBOAT ERUPTING" banner on the most-watched geyser claim in
the park, and one false banner would cost more trust than a year of good
predictions buys.

## What shipped instead

A Steamboat **context card**: days since the last major, the recent interval
range from GeyserTimes, and an explicit statement that nobody can honestly
predict it — which is the true state of knowledge, and saying it plainly is
worth more than a decoration of false precision. The card computes from data
already synced; no seismic dependency.

## Where seismic integration would actually pay, ranked

1. **Steamboat detection** (two-station design above) — the only geyser
   where seismic sees something nothing else does, hours before a human
   report reaches GeyserTimes.
2. **OF preplay research** via YFT — published precedent for minutes-scale
   precursors; an Indicator-style nowcast someday. The 24/7 webcam already
   covers detection, so only the precursor angle adds value.
3. **Biscuit Basin** via the new YBB — modest audience.
4. Nothing for the Lower Basin, Lone Star, or Till until instruments exist
   there.
