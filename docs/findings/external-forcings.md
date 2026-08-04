# External forcings: weather, tides, rain, earthquakes, hydrology

**Does anything outside the geyser itself move its interval enough to help a
walk-forward prediction?**

Short answer: **almost nothing.** One forcing is real and large enough to matter
(wind on Daisy). Everything else is null, too slow, or too rare. This document
exists so nobody spends another week rediscovering that.

Two sections, kept separate because they have very different evidentiary status:

1. **Literature synthesis** — published work, dated **2026-08-03**. Not our
   measurements except where explicitly noted as "our data".
2. **In-database interactions** — measured on the GeyserTimes archive by this
   project.

---

# Part 1 — Literature synthesis (2026-08-03)

Verdicts are framed specifically as *"is this useful for walk-forward prediction
of our seven geysers?"*, which is a much higher bar than *"is this physically
real?"*. Several forcings below are real and still useless to us.

## Wind and air temperature → Daisy: REAL AND LARGE

**The one forcing worth implementing.**

- Hurwitz et al. (2014): **8 of 11 wind storms** pushed Daisy's interval past
  +1σ. Modelled effect **135 min at 2 m/s → 180 min at 8 m/s** — a 45-minute
  swing, driven by evaporative and convective heat loss from the exposed pool.
- **Independently replicated at Strokkur, Iceland** over ~650,000 eruptions, so
  this is not a Yellowstone-specific curiosity.
- **Our own data agrees**: Daisy shows a **19.4-minute seasonal swing**, longest
  in February and shortest in August. That is **4.5× Daisy's MAE** (4.3 min) —
  comfortably large enough to matter.

Daisy is the **only** one of our seven where weather clears the bar.

## Wind and air temperature → Old Faithful: NULL

- **0 of 11** storms produced a response.
- Literature amplitude ~2.7 min; ours ~1.6 min. **Both are below our 6-minute
  error floor on Old Faithful**, so even if real it is unmeasurable against our
  own noise.

The contrast with Daisy is the physically expected one: Old Faithful's conduit is
deep and thermally insulated; Daisy's pool is exposed.

## Barometric pressure: NULL TO MARGINAL

- No coherence at any frequency.
- Daisy's apparent pressure signal has the **wrong sign** for decompression
  boiling — i.e. the mechanism people reach for does not fit the data. It is
  almost certainly wind and temperature co-varying with pressure, not pressure.

## Rainfall: DEMONSTRATED NULL

Zero measurable response within 3 hours of rainfall events ≥7 mm/h. This is a
clean negative, not an absence of evidence.

## Earth tides: THE CLEANEST NULL IN THE FIELD

- The Rinehart (1972) claim of tidal modulation was **rebutted in print** and is
  superseded. Do not resurrect it.
- **Suggested use as a deliberate negative control**: feed a tidal series
  (e.g. `pygtide`) into any model-selection procedure. If a model assigns it
  non-trivial importance, **that is evidence of leakage or overfitting in the
  procedure**, not a discovery. This is a genuinely useful diagnostic and costs
  almost nothing to set up.

## Snowpack, drought, hydrology: REAL BUT FAR TOO SLOW

- Real effects exist at **2–11 month lags**.
- Converted to a per-interval quantity that is ≈ **0.03 min drift per interval**.
- **Retraction, recorded deliberately:** an earlier version of this research
  proposed the Firehole River gauge as a short-term covariate. That was wrong.
  It is a **slow drift term**, on the order of months, and the local-baseline
  validity filter already absorbs drift of that kind. It should not be wired in
  as a live covariate.

## Earthquakes: RARE REGIME CHANGES ONLY

- Requires **>0.1 MPa dynamic stress** to alter geyser behaviour.
- **M9.1 Tōhoku did nothing** to Yellowstone geysers — a useful calibration of
  how large "large" has to be.
- The Denali 2002 attribution for Riverside and Depression is
  **press-release-only** and should be treated as **contested**, not established.
- Practical consequence: earthquakes are a *regime-change* event to be detected
  after the fact (the adaptive changepoint model already handles regime shifts),
  not a predictive covariate.

## Weather data source decision: HRRR over NAM

Chosen **not on forecast skill but on archive depth**:

- **NAM's** open archive starts **2021-09**, which cannot support a 3-year
  walk-forward backtest that begins earlier.
- **ERA5** reaches back to **1940** — use for training and backtesting.
- **HRRR** at 15-minute resolution — use for live inference.

This is the right decision for anyone implementing the Daisy wind model.

## Also relevant

- **Jin et al. (2022)** — covariate-adjusted recurrent-event model applied to
  West Triplet and Grotto. Closest published methodology to ours.
- **Rhee & Yeung (2023)** — Beehive Indicator lead time, mean **13.3 min**.
  Independently consistent with our own 13.0 min (see Part 2).

## Citations

The prior engineer's synthesis carried **18 citations with DOIs**. The saved PDFs
lived in a temporary session directory. Seventeen were recovered and the
following DOIs were extracted directly from the files; the remainder could not be
resolved from the saved copies and are listed by identifier only.

**Verified from the recovered PDFs:**

| Work | DOI |
|---|---|
| Hurwitz et al. 2014, *JGR Solid Earth* — wind/temperature forcing of Daisy | `10.1002/2013JB010803` |
| Husen et al. 2004, *Geology* 32(6) 537–540 — "Changes in geyser eruption behavior and remotely triggered seismicity" (Denali) | `10.1130/G20381.1` |
| Old Faithful 2020, *Geophysical Research Letters* 47(20) | `10.1029/2020GL089871` |
| Steamboat 2023, *Geochemistry, Geophysics, Geosystems* 24(7) | `10.1029/2023GC010988` |
| Hurwitz et al. 2008, *Geology*, June 2008, p. 451 | not embedded; identify by journal/page |
| Marler & White 1975 — "Evolution of Seismic Geyser, Yellowstone National Park" | pre-DOI |

**Recovered but DOI not extractable from the saved copy** — verify before
citing: Reed 2021; Rojstaczer 2003; Karlstrom/Kieffer-lineage 2013 (carries
`10.1002/jgrb.50251` and `10.1002/grl.50422` in its reference list, one of which
is its own); Hurwitz & Manga 2017; Hurwitz 2012; USGS Professional Paper 435;
two further Old Faithful and Steamboat papers.

**Cited above but not among the recovered files:** Rinehart 1972 (tidal claim,
superseded); Jin et al. 2022; Rhee & Yeung 2023; the Strokkur replication.

**If you are going to build on this, re-verify the citations.** They are recorded
here so the *findings* survive, not as a substitute for reading the papers.

---

# Part 2 — In-database interactions (measured on our own data)

Neighbour-geyser relationships, measured on the GeyserTimes archive. Full model
detail and the backtest harness are in [model-results.md](model-results.md);
this section records the *findings*.

## Beehive's Indicator → Beehive: REAL, LARGE, IMPLEMENTED

- **8,763** Indicator entries logged in total.
- Lead time: **n=5,441 since 2000, median 13.0 min, p5–p95 3.0–20.6**.
  **n=3,486 since 2015: mean 13.0, sd 10.3.** A normal fits far better than
  lognormal or gamma.
- Implemented as a **Bayesian mixture**, not a switch.
- In-Indicator regime: **CRPS −35.4%**, nominal 90% coverage **29% → 87%**.
- No-Indicator regime: **provably unchanged** (±0.0%), which is the property that
  makes it safe to ship.

Independently consistent with Rhee & Yeung (2023)'s 13.3 min.

## Turban → Grand: REAL LATTICE, NO PREDICTIVE VALUE

Only **0.1%** of Grand starts land 5–13 min after a Turban vs **24%** in the
first 2 min (n=3,710). Gating on it gives **+0.2% CRPS** — no help. Turban's
phase decoheres within ~1 cycle (sd 4.2 min on a 19-min period), and Grand's
~100-min base uncertainty never consults the lattice at a discriminating range.
Kept behind `use_turban`, default off.

## Rift → Grand: REAL, STILL WORSE

+32 min in a fixed early window (t=6.4), and it **worsens** the distribution
(+1.6%). The naive between-eruptions measurement gives +45 min — **40% of that is
length bias**. Kept behind `use_precursors`, default off.

## Castle minor/major

Post-minor median **375 min** vs post-major **~1081 min**, after per-regime
validity filtering. Before that filter fix, the pooled filter had deleted
**103 of 103** short post-minor intervals and the two branches were
indistinguishable. See [data-quality.md](data-quality.md).

## Observer-attention confound

When gazers cluster at one geyser, others' apparent intervals inflate because
eruptions go unlogged. **Any cross-geyser correlation study must control for
this**, or it will discover that geysers are correlated with where people are
standing.

## Electronic vs human entries

GT `E` entries are **volunteer batch-transcriptions from data loggers**, not an
institutional real-time feed. At Great Fountain the loggers are genuinely more
complete (no missed eruptions) with **no timestamp offset** — but entry-type
conditioning **does not improve predictions**. See
[model-results.md](model-results.md) for the transition table.

---

## What to do next, in priority order

1. **Implement the Daisy wind model.** It is the only forcing that clears the
   bar, the effect is 4.5× the current MAE, and the data-source decision (ERA5
   for training, HRRR for live) is already made.
2. **Add earth tides as a negative control** to the model-selection procedure.
   Cheap, and it will catch leakage.
3. **Do not** wire in river gauges, rainfall, or barometric pressure.
4. **Do not** revisit earth tides as a signal.
