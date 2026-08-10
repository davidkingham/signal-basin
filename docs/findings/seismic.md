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

## Round two: the two-station design also fails, measured

The YNM/YNB amplitude-ratio design was validated the same day and does not
survive contact with the data either. Three measurements killed it:

1. **YNB barely records Steamboat.** During confirmed majors, YNB rises only
   1.1–2.1× (against YNM's 6–14×) — which would have made it an ideal quiet
   reference, except:
2. **YNB is unreliable as a reference.** Its 15-minute medians drop to
   near-zero in ordinary windows (dead-channel episodes), so the ratio-shift
   test produced **8–25× scores on false positives** — indistinguishable
   from real eruptions (5–36×).
3. **YNM's false onsets are local too.** Sixteen eruption-free control days
   produced 7 sustained-onset candidates — every one between 9 and 11 am
   local, i.e. cultural noise at the Norris Museum itself, which is a local
   source just like Steamboat and therefore passes any two-station test.
   And no amplitude floor separates them: real onsets sustain 5.9k–16k
   counts, false ones 3.2k–5.7k — the weak 2025-04-14 eruption sits inside
   the false-positive range.

Detection coverage is also worse than it first appeared: YNM has no usable
data at 4 of 13 eruption times since late 2023 (telemetry gaps and one
flatlined channel), and one eruption is seismically weak everywhere.

**Round three, prompted by "are you sure?": YNR fails the same way, and
for the structural reason.** YNR (Norris Junction, ~2 km, running since
1993) was tested as the reference: it discriminates a real eruption at
12.7 — and a known false positive at **6.3**, because the false positives
are cultural noise local to the YNM vault. Two co-located sources cannot
be separated by any remote station, however reliable. The
reference-station family is closed with three stations measured, not
assumed. (YNR also has 2025+ data gaps of its own.)

**What the challenge surfaced instead — the night gate, quantified.**
Every false positive in the 16-day sweep lands between 06:00 and 10:46
local: museum noise is diurnal, eruption tremor is not. Against the
existing data, a detector active 17:00–08:00 local, with a strict
quiet-baseline gate (trailing median < 2,500) and the 15-minute sustained
minimum ≥ 6,000, scores **4 clean detections of 13 eruptions (~31%) with
zero false positives** — and the four are evening/overnight eruptions,
exactly when human reports lag most. The honest framing would be
"seismic watch, active overnight; detects roughly the louder half of
in-window eruptions; silence means nothing."

**The long baseline (run 2026-08-09): 118 eruption-free days, 1,606
valid gate-hours, exactly one fire — and the fire was the M7.6 Aomori,
Japan earthquake.** The sweep covered 2025-10-01 → 2025-12-27 (fall into
winter, wind season, park closed) and 2026-06-15 → 2026-07-14 (peak
summer evenings). The single fire, 2025-12-08 07:49 MST, is the surface
-wave arrival of the M7.6 that struck Japan 34 minutes earlier (USGS
us6000rtdt) — a real seismic event, not noise, and it defines the one
in-gate false-positive class: teleseisms. That class has a measured
veto: the Japan quake lifted YNR 145× and YFT 70× simultaneously, while
real Steamboat eruptions leave YNR at 0.9–2.3× — so "suppress when YNR
is simultaneously ≥3× elevated" kills every teleseism and no measured
eruption. Composed detector: night gate (kills diurnal cultural noise) +
quiet baseline + 15-minute sustained minimum + regional veto (kills
earthquakes) + 60-minute refractory. **Zero false positives in 1,606
gate-hours; 4 of 13 eruptions detected, all evening/overnight.** The
remaining honest caveats: detection n=4; YNM itself was dark at 4 of 13
eruption times, so the watch must present silence as no-information;
and the veto depends on YNR being up (when both are dark, the watch is
simply off).

**Round four — "why exclude all daytime?" — the all-hours sweep and the
seasonal truth (212 days, 4,797 hours).** Dropping the clock gate and
re-sweeping everything, including the deep winter the first baseline never
covered, produced 47 fires merging to 23 events. The regional veto killed
only 3 (two teleseisms, one regional event). The 20 survivors rewrite the
seasonal map:

| season | all-hours verdict |
|---|---|
| Oct 1 – Dec 5 (closed, pre-oversnow) | 66 days × 24 h, **zero** surviving fires — full-day watch validated |
| mid-Dec – mid-Mar (oversnow season) | ~18 surviving events, ~1 per 4 days, 6k–648k sustained — **unusable at any hour** |
| Mar 22 – Apr 30 (closed, post-plowing) | clean |
| summer | nights clean; two ~10:10–10:30 crossers → keep the night gate |

The oversnow failures are *local* (YNR flat at 0.6–1.4× under all of
them) and the calendar names the source: snowcoach stops at the Norris
warming hut — which is at the museum, beside the vault — during midday
hours, groomers at 01:00–02:40, and 300k–650k events matching grooming
and spring plowing in late February and mid-March. Winter visitation
parks vehicles on top of the seismometer; amplitude and duration overlap
eruptions completely, and co-location defeats every station-geometry
veto. The one season where gazers are scarcest is the one season this
station cannot cover.

**Where this leaves the detector**: a season-aware watch is validated —
full-day in the shoulder seasons (October–early December,
late March–April), night-gated in summer, and **honestly suspended
during the oversnow season** with the reason stated on the card. Winter
coverage requires spectral discrimination; nothing less survives the
data. Every one of these boundaries was set by measurement, most of them
by someone asking "are you sure?" at the right moment.

## What shipped

Two things, in order of arrival:

1. The Steamboat **context card**: days since the last major, the recent
   interval range from GeyserTimes, and an explicit statement that nobody
   can honestly predict it.

2. **The season-aware seismic watch** (2026-08-09, after the four
   validation rounds above): `seismic.py` polls WY.YNM on the service
   cadence, maintains a rolling minute-RMS state through the worker's
   writable prefix, and applies exactly the validated detector — quiet
   baseline < 2,500, 15-minute sustained minimum ≥ max(6,000, 3× baseline),
   YNR/YFT regional veto at 3×, 60-minute refractory — under the round-four
   season policy: all hours in the shoulder seasons, night-gated in summer,
   **suspended during the oversnow season with the reason printed on the
   card**. A detection renders as "seismic signature consistent with a
   major eruption — awaiting observer confirmation", never as certainty;
   every other mode states its reason; and a dark station or lagging feed
   says "watch offline" rather than pretending to watch. Detection latency
   is ~15–20 minutes by construction — still hours ahead of a human report
   overnight in the shoulder seasons.

## Where seismic integration would actually pay, ranked

1. **Steamboat detection** — still the only geyser where seismic sees
   something nothing else does, hours before a human report reaches
   GeyserTimes. But both amplitude designs are now measured failures; the
   viable path starts from spectral features with a labeled dataset.
2. **OF preplay research** via YFT — published precedent for minutes-scale
   precursors; an Indicator-style nowcast someday. The 24/7 webcam already
   covers detection, so only the precursor angle adds value.
3. **Biscuit Basin** via the new YBB — modest audience.
4. Nothing for the Lower Basin, Lone Star, or Till until instruments exist
   there.
