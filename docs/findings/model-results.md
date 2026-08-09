# Model results: what won, what lost, and what production actually serves

Walk-forward backtest over the last 3 years. At each evaluated eruption a model
sees only intervals strictly earlier than the one it is predicting. All models
are scored on the same set of target eruptions, so none benefits from silently
skipping hard cases.

Regenerate with `uv run geyser-ai backtest`; the full table lives in
[`reports/calibration_report.md`](../../reports/calibration_report.md).

## The headline

| Geyser | Best model | CRPS | MAE | 50% cov | 90% cov |
|---|---|---:|---:|---:|---:|
| Old Faithful | `minor_conditional` | 4.7 | 6.4 | 58% | 93% |
| Grand | `adaptive_lognormal` | 38.9 | 54.4 | 47% | 91% |
| Daisy | `adaptive_lognormal` | 3.1 | 4.3 | 51% | 88% |
| Riverside | `adaptive_lognormal` | 12.8 | 17.5 | 53% | 91% |
| Castle | `minor_conditional` | 77.6 | 101.6 | 61% | 87% |
| Great Fountain | `lognormal` | 45.6 | 62.9 | 55% | 91% |
| Beehive | `rolling_normal` | 119.8 | 166.1 | 52% | 87% |
| Fountain | `adaptive_lognormal` | 35.2 | 48.4 | 52% | 87% |
| Lion | `series_conditional` | 119.4 | 187.6 | 61% | 89% |
| Artemisia | `best_parametric` | 174.9 | 240.3 | 53% | 86% |

## The single most important result

**Data cleaning beat modelling, every time, by a wide margin.** Four successive
fixes to the interval validity filter each produced larger gains than any model
change ever did — 20–87% CRPS reductions per geyser from the harmonics fix alone,
and a further 30% on Castle from the per-regime fix. See
[data-quality.md](data-quality.md).

If you have a day to spend improving predictions, spend it on the data.

## Fountain, the eighth geyser (added 2026-08-09)

Selected by a sweep of all 491 logged geysers for interval tightness and
logging density, calibrated against the existing seven (log-sd of the
single-interval mode: Daisy 0.057 best, Beehive 0.206 worst). Fountain came in
at **log-sd 0.207 on a 305-minute median** — statistically the same tier as
Castle (0.203) and Beehive (0.206) — with 916 entries over two years, a
unimodal interval distribution, and no drift (the most recent ~120 intervals
fit log-sd 0.201). Neither the NPS nor Geysers.net predicts it, so this is
coverage nobody else provides rather than a head-to-head.

Two caveats, recorded up front:

- **Honest coverage is the worst in the set**: 45.9% of raw gaps are rejected
  by the validity filter (overnight logging is sparse at Fountain Paint Pots)
  and the honest 90% band catches only 47.8%. A gazer scoring us against every
  raw interval will see misses at nearly Great Fountain rates (51.7%).
- **Morning is not modelled.** When Morning is active the two geysers interact
  and Fountain's intervals shift; the current models will simply see wider
  scatter. Nobody should be surprised if a future Morning active phase makes
  Fountain's live numbers sag until that conditioning is added.

The runner-up candidate from the same sweep, **Lion**, was added the next day
with the series model it needed — see the section below. Ruled out with
numbers: White Dome (0.297), Aurum (0.319), Sawmill (0.306), Grotto (0.298).

**Lone Star is ruled out for a different reason and deserves the precise
version.** The geyser itself is *more* regular than most of what we serve —
tight-mode log-sd **0.147 at a 164-minute median** (p5–p95 123–204, n=74
clean pairs over 3y), with minor/major structure on top. What fails is the
observation channel: it is backcountry with no cell coverage, so only 26% of
entries reach GeyserTimes within 30 minutes and the median entry latency is
**2.7 hours — one full interval**; by the time an anchor arrives, the next
eruption has usually already happened. Sampled at random moments, the newest
logged eruption is a median of **206 hours old**, and an anchor fresh enough
to predict from exists ~2% of the time. A live card would read "no report
for 9 days" essentially always. The useful product for Lone Star is a
*planning* answer (interval stats + "log it when you arrive"), not a
countdown — unbuilt as of this writing.

## Lion, the ninth geyser (added 2026-08-09)

The sweep's runner-up, and the reason it could not be a drop-in: Lion erupts
in **series**. An initial eruption (observer-flagged `ini`, ~19% of entries),
one to five more at ~83-minute spacing (in-series log-sd 0.133 — tighter than
anything served except Daisy and Riverside), then 7.8–14.7 h of quiet. Two
pieces of machinery had to exist first:

1. **The validity filter deleted the entire long mode** — all 7,410 series
   gaps since 2015 — because a p25-anchored baseline sits in the short mode
   and no `prev_*` regime split fixes it (the post-non-initial branch is
   itself a 40/60 mixture). Generation 5 of the filter accepts gaps near
   either local mode, ratio-guarded so harmonics can never qualify; see
   [data-quality.md](data-quality.md).

2. **`series_conditional`**: branch on the anchor's `initial` flag; each
   branch is a two-component lognormal mixture (in-series vs series-gap,
   split at the geometric midpoint of the window's p25/p75) weighted by the
   branch's empirical continue rate — post-initial ~82% continue, ~40%
   after later eruptions. On a window that is not clearly bimodal it
   degrades to the pooled fit, so it is safe in any roster.

Walk-forward, 3 years, 2,000 evaluations:

| model | CRPS | MAE | 50% cov | 90% cov |
|---|---:|---:|---:|---:|
| **series_conditional** | **119.4** | **187.6** | 61% | 89% |
| weibull_aft | 134.2 | 196.1 | 46% | 91% |
| best_parametric | 141.1 | 204.4 | 34% | 94% |

−15.4% CRPS against the default — decisive by the noise rule, so Lion is
pinned in `BEST_MODEL_BY_GEYSER`. Note the unconditional models' 50%
coverage of ~33%: a unimodal fit cannot cover a bimodal reality no matter
how wide it stretches. Also note `weibull_aft` placing *second* — its only
non-bottom-half finish, because Lion is the first geyser whose process has
real state for covariates to carry.

The renewal forecast's chained draws use the **pooled mixture** as the
step distribution (`fit_marginal`), not a plain lognormal — a lognormal
fitted to bimodal data puts its median in the empty valley between the
modes and describes nothing that actually happens.

Honest coverage (scored against **every** raw gap): only 11.1% of Lion's
gaps are rejected by the filter — least in the set after Beehive, a direct
consequence of the second-mode band recognising series gaps as real — and
the honest 90% band catches 85.5%. The honest 50% is a poor 30.5%: with a
bimodal spread the central band often straddles the empty valley, which is
a structural cost of the shape, not a fixable calibration error.

## Artemisia, the tenth geyser (added 2026-08-09)

The last candidate standing from the sweep, re-ranked through the production
intervals table rather than the crude two-year gap analysis — a re-ranking
that mattered: Till's promising crude log-sd of 0.197 dissolved into **2.16
at a 12-minute median** under the real filter (the crude mode-finder had
locked onto a harmonic), Lone Star came out at 1.51, Aurum at 0.68. Artemisia
held: **log-sd 0.208 on a ~22 h median** (2,069 valid intervals since 2015),
cleanly unimodal in log space, and **30% logger-recorded**, which keeps its
valid share at 0.8–0.95 around the clock — observationally better than
Fountain despite one eruption a day.

Walk-forward (386 evaluations): `best_parametric` wins outright at CRPS
174.9, with the next four models within 3.4%. It *is* the default, so
Artemisia takes no `BEST_MODEL_BY_GEYSER` entry — nothing to pin. Calibration
53%/86%. Honest coverage: 29.6% of raw gaps rejected, honest 90% band 60.8%
— mid-pack, far better than Fountain's 47.8%, thanks to the logger.

Neither the NPS nor Geysers.net predicts it. One eruption a day means the
scoreboard will accumulate slowly; patience before judging its live numbers.

## What production serves, and why

`models.BEST_MODEL_BY_GEYSER`:

```python
{"Old Faithful": "minor_conditional", "Castle": "minor_conditional",
 "Lion": "series_conditional"}
# everything else falls back to best_parametric
```

This map exists because production used to default **every** geyser to
`best_parametric` while the README published a per-geyser winners table that
nothing read at runtime. That silently discarded the largest available gain on
two geysers:

| | `best_parametric` | `minor_conditional` | change |
|---|---:|---:|---:|
| Old Faithful | 8.9 | **4.7** | −47% |
| Castle | 173.0 | **77.6** | −55% |

The other six keep the default deliberately. Their walk-forward winners beat
`best_parametric` by **0.2–5.5%** (Riverside 12.8 vs 12.8; Great Fountain 45.6
vs 45.7; Daisy 3.1 vs 3.2; Grand 38.9 vs 40.0; Beehive 119.8 vs 126.3; Fountain
35.2 vs 37.3). That is inside the noise, and pinning a production choice on it
would be overfitting the leaderboard rather than improving the forecast.

**If you regenerate the backtest, update this map — but only where the margin is
decisive.**

## The model roster

| Model | What it is |
|---|---|
| `rolling_normal` | Dashboard-style rolling mean ± window, read as a normal |
| `lognormal` / `weibull` | Rolling-window MLE fits |
| `best_parametric` | Picks lognormal vs Weibull per prediction by held-out likelihood |
| `adaptive_lognormal` | Changepoint detection + held-out selection of window length |
| `weibull_aft` | lifelines Weibull AFT with covariates, refit periodically |
| `duration_lognormal` | Old Faithful only: short/long preceding-duration split |
| `minor_conditional` | Castle & Old Faithful: conditions on whether the previous eruption was a minor |
| `entry_conditional` | Logger-heavy geysers: conditions on electronic vs human entry |

## Wins

### The `minor` flag is the best covariate in the project

It was already in the archive, recorded by volunteers at logging time — so it is
legitimately known at prediction time.

- **Old Faithful**: post-minor and post-major distributions are almost disjoint
  — median 70 min (p95 83) after a minor, 102 min (p05 91) after a full
  eruption. 22% of recent eruptions are minors. Conditioning roughly halves CRPS
  (8.8 → 4.5) and **beats the classic duration-based split**, because the
  observer-set flag is cleaner than raw duration data, which is often missing.
- **Castle**: post-minor median 375 min vs post-major ~1081 — but only *after*
  the per-regime validity filter stopped deleting the short mode. Before that fix
  the branches were 1028 vs 1078 and the flag looked nearly useless; the earlier
  write-up claiming Castle's gain was "in variance rather than location" was
  describing a filter artifact, not the physics.

### Beehive's Indicator is the most valuable live signal found

Beehive's Indicator starts, and Beehive follows about 13 minutes later.
n=5,441 since 2000: median 13.0 min, p5–p95 3.0–20.6. n=3,486 since 2015: mean
13.0, sd 10.3. A normal fits far better than lognormal or gamma (KS 0.070 vs
0.178). Measured the other way, 93.7% of Indicator entries are followed by
Beehive within 25 minutes. 8,763 Indicator entries are logged in total.

Scored on a **nowcast** harness — decision times on a fixed 30-minute grid, each
scored twice with conditioning on and off — because "how long until the next
eruption, standing here now" is both the question a gazer asks and the only
unbiased way to score a conditional regime:

| Regime | n | CRPS off | CRPS on | Δ | 90% cov off | 90% cov on |
|---|---:|---:|---:|---:|---:|---:|
| overall | 30,965 | 118.2 | 114.7 | −2.9% | 85% | 89% |
| no Indicator | 28,937 | 116.1 | 116.2 | ±0.0% | 89% | 90% |
| **Indicator running** | **2,071** | **147.4** | **95.2** | **−35.4%** | **29%** | **87%** |

During those minutes the nominal 90% interval goes from catching **29%** of
eruptions to **87%** — the unconditioned model is wildly overconfident exactly
when someone is standing there waiting. Outside that window nothing changes,
which is the point.

Implemented as a Bayesian **mixture**, not a switch: the Indicator branch carries
weight ∝ reliability × P(lead > elapsed), so if 30 minutes pass with no eruption
the branch decays on its own. An early version hard-switched and produced 141 min
of error by insisting "any second now" long after the Indicator had plainly
failed.

## Losses, documented at equal length

### The covariate survival model does not earn its complexity

`weibull_aft` (lifelines Weibull AFT with previous interval, hour of day, day of
year, entry flags) ranks in the **bottom half on all eight unimodal geysers**.
(On Lion it lands second -- covariates help when the process really has state --
but still 11% behind the purpose-built series model.) Simple
rolling lognormal/Weibull fits beat it nearly everywhere, and a plain rolling
mean ± window — essentially what the existing community dashboard shows — wins
outright on Beehive.

**It also had a leakage bug that made it look good.** Its early apparent win on
Riverside (CRPS 106.6 vs 120.8) was an artifact: covariates were being taken
from the eruption being predicted rather than the anchor, so it was effectively
using the previous interval to spot missed-eruption doubles. Once covariates were
correctly anchored to the *previous* eruption and the harmonics were filtered
out, the signal vanished entirely.

**Rule that came out of this:** predicting an interval means standing at the
previous eruption. Only that eruption's clock time and flags are knowable. Using
the target's own hour-of-day leaks the answer. The `intervals` table therefore
exposes `prev_hour_local`, `prev_doy`, `prev_webcam`, `prev_minor` and so on —
never the target's own.

### The Turban → Grand lattice: real structure, no predictive value

Grand starts *with* a Turban. Only **0.1%** of Grand starts fall 5–13 minutes
after a Turban, against **24%** in the first two minutes (n=3,710). The lattice
is unmistakably real.

Gating Grand's density onto it does **nothing**: **+0.2% CRPS** (44.5 → 44.5
overall; 42.3 → 42.4 in the Turban-gated regime). Two reasons, both structural:

1. Turban's own interval scatters (sd 4.2 min on a 19-min period), so
   extrapolated phase **decoheres within about one cycle**.
2. Grand's base uncertainty is ~100 min — five times the Turban period — so the
   model's predicted median never drops low enough to consult the lattice at a
   range where it could discriminate.

Kept behind a `use_turban` flag, default off, so the negative result stays
reproducible.

### Rift and West Triplet → Grand: real effect, worse distribution

Rift (+32 min) and West Triplet (+15 min) both survive a length-bias-safe
significance test (Rift t=6.4), and both still **worsen** the predicted
distribution (+1.6% CRPS). Kept behind `use_precursors`, default off.

**One trap worth recording permanently:** the naive "did Rift erupt anywhere
between the two Grand eruptions?" test gives +45 min. That is **length-biased** —
longer intervals mechanically have more room to contain a Rift. Measured in a
*fixed* early window it is +32 min. **Roughly 40% of the apparent effect was an
artifact of the question.** Any neighbour-interaction study on this data needs a
fixed-window measurement.

### Electronic loggers: a real data-quality difference that does not predict

Great Fountain is ~60% logger-recorded. Entry-type transitions (2015+):

| transition | n | median | sd |
|---|---:|---:|---:|
| logger → logger | 2051 | 669 | 95 |
| logger → human | 627 | 665 | 88 |
| human → logger | 626 | 686 | 91 |
| human → human | 1139 | 708 | 276 |

This is **not** a timestamp offset — an offset would push the two mixed pairs
apart symmetrically while leaving like-to-like alone, which is not the pattern.
It is a completeness difference: a logger catches every eruption, so consecutive
logger entries are true intervals, whereas human-only stretches still contain
missed ones (mean 813 vs median 708 is a heavy right tail).

The implied model, `entry_conditional`, **does not help**: Great Fountain 46.0 vs
45.6 for plain lognormal. Kept in the roster and reported rather than quietly
dropped.

## Missed eruptions at prediction time

The naive forecast conditions on survival — *it hasn't erupted, so it's overdue*
— which is only sound if we would certainly have seen it. In crowdsourced data
that fails constantly: a silent 14 hours at Riverside usually means nobody was
looking.

`predict` treats the geyser as a **renewal process** from the last *logged*
eruption, with each eruption logged independently with probability `p_obs`. A
path on which k eruptions fell inside the silent window is consistent with the
evidence only if all k went unlogged, so it carries weight `(1 - p_obs)^k`.
Weighting simulated paths that way interpolates between the regimes
automatically: with fresh data it reduces to ordinary survival conditioning;
with stale data the weight shifts onto the k-missed hypotheses and the forecast
correctly becomes *"it already went; the next one is roughly one interval from
whenever that was"*.

`p_obs` is **estimated from the data, not tuned**: the recent share of gaps that
came through the validity filter as single intervals. On a 17-hour-old snapshot
Old Faithful reports ~9.4 expected missed eruptions and predicts the next one
just after now, instead of claiming it is 17 hours overdue.

**Note for anyone touching this**: the renewal adjustment is a Monte-Carlo
simulation, so the predicted median wobbles by a few seconds between identical
runs. Anything that keys on the predicted time must tolerate that — the
scoreboard ledger keys on the *anchor eruption* rather than the predicted minute
for exactly this reason.
